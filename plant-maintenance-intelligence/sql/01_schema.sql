-- =============================================================
-- Phase 1: Plant Maintenance Intelligence — Schema Setup
-- Exasol Community Edition
-- =============================================================

-- Create schema
CREATE SCHEMA IF NOT EXISTS PLANT_MAINTENANCE;
OPEN SCHEMA PLANT_MAINTENANCE;

-- Drop in FK-safe order (child tables first) for idempotent re-runs
DROP VIEW  IF EXISTS V_ACTIONABLE_RISK;
DROP VIEW  IF EXISTS V_RISK_TREND_24H;
DROP VIEW  IF EXISTS V_LATEST_RISK_SUMMARY;
DROP VIEW  IF EXISTS V_TELEMETRY_FEATURES;


-- =============================================================
-- Table 1: MACHINE_REGISTRY
-- Static metadata for each machine across all plants
-- =============================================================
CREATE TABLE IF NOT EXISTS MACHINE_REGISTRY (
    machine_id              VARCHAR(20)     NOT NULL,
    plant_id                VARCHAR(10)     NOT NULL,
    machine_type            VARCHAR(50)     NOT NULL,   -- e.g. COMPRESSOR, PUMP, TURBINE, CONVEYOR, MIXER
    install_date            DATE            NOT NULL,
    baseline_temp_c         DECIMAL(6,2)    NOT NULL,   -- normal operating temperature
    baseline_vibration      DECIMAL(6,3)    NOT NULL,   -- normal vibration mm/s
    baseline_pressure_bar   DECIMAL(6,2)    NOT NULL,   -- normal pressure
    baseline_power_kw       DECIMAL(8,2)    NOT NULL,   -- normal power draw
    baseline_stddev_temp    DECIMAL(6,3)    NOT NULL,   -- std dev for Z-score calc
    baseline_stddev_vibration DECIMAL(6,3)  NOT NULL,
    service_interval_hours  DECIMAL(8,1)    NOT NULL,   -- hours between scheduled service
    last_service_ts         TIMESTAMP       NOT NULL,
    location_zone           VARCHAR(20),                -- e.g. ZONE_A, ZONE_B
    criticality_class       VARCHAR(10)     DEFAULT 'STANDARD', -- CRITICAL / STANDARD
    CONSTRAINT pk_machine_registry PRIMARY KEY (machine_id)
);

-- =============================================================
-- Table 2: MACHINE_TELEMETRY
-- Time-series sensor readings (bulk loaded in Phase 1)
-- =============================================================
CREATE TABLE IF NOT EXISTS MACHINE_TELEMETRY (
    telemetry_id        DECIMAL(18,0)   IDENTITY NOT NULL,
    machine_id          VARCHAR(20)     NOT NULL,
    reading_ts          TIMESTAMP       NOT NULL,
    temperature_c       DECIMAL(7,2),
    vibration_mm_s      DECIMAL(7,3),
    pressure_bar        DECIMAL(7,2),
    runtime_hours       DECIMAL(10,1),
    power_kw            DECIMAL(8,2),
    operating_mode      VARCHAR(20),    -- RUNNING, IDLE, MAINTENANCE, STARTUP
    error_code          VARCHAR(10),    -- NULL = no error, E5xx = critical fault
    load_ts             TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_machine_telemetry PRIMARY KEY (telemetry_id),
    CONSTRAINT fk_telemetry_machine FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
);

-- =============================================================
-- Table 3: SCORED_TELEMETRY_RESULTS
-- Stores computed risk scores per reading
-- =============================================================
CREATE TABLE IF NOT EXISTS SCORED_TELEMETRY_RESULTS (
    score_id            DECIMAL(18,0)   IDENTITY NOT NULL,
    machine_id          VARCHAR(20)     NOT NULL,
    reading_ts          TIMESTAMP       NOT NULL,
    risk_score          DECIMAL(5,4)    NOT NULL,   -- 0.0000 to 1.0000
    risk_tier           VARCHAR(10)     NOT NULL,   -- LOW / MEDIUM / HIGH / CRITICAL
    top_signal          VARCHAR(50),                -- which sensor drove the score
    recommended_action  VARCHAR(200),
    model_version       VARCHAR(20)     DEFAULT 'LUA_V1',
    scored_at           TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_scored_results PRIMARY KEY (score_id),
    CONSTRAINT fk_scored_machine FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
);

-- =============================================================
-- View: V_TELEMETRY_FEATURES
-- Leakage-free Z-scores using 24-hour trailing window
-- Gap-safe with NULLIFZERO to avoid division by zero
-- =============================================================
CREATE OR REPLACE VIEW V_TELEMETRY_FEATURES AS
SELECT
    t.machine_id,
    t.reading_ts,
    t.temperature_c,
    t.vibration_mm_s,
    t.pressure_bar,
    t.runtime_hours,
    t.power_kw,
    t.operating_mode,
    t.error_code,

    -- Z-scores using machine baseline (leakage-free, no future data)
    (t.temperature_c  - r.baseline_temp_c)      / NULLIFZERO(r.baseline_stddev_temp)      AS z_temp,
    (t.vibration_mm_s - r.baseline_vibration)   / NULLIFZERO(r.baseline_stddev_vibration) AS z_vibration,

    -- Hours since last service (for overdue detection)
    SECONDS_BETWEEN(r.last_service_ts, t.reading_ts) / 3600.0 AS hours_since_service,

    r.service_interval_hours,
    r.baseline_temp_c,
    r.baseline_vibration,
    r.baseline_pressure_bar,
    r.baseline_power_kw,
    r.baseline_stddev_temp,
    r.baseline_stddev_vibration,
    r.criticality_class,
    r.plant_id,
    r.machine_type

FROM MACHINE_TELEMETRY t
JOIN MACHINE_REGISTRY r ON t.machine_id = r.machine_id;

-- =============================================================
-- View: V_LATEST_RISK_SUMMARY
-- Latest risk score per machine for dashboard overview
-- =============================================================
CREATE OR REPLACE VIEW V_LATEST_RISK_SUMMARY AS
SELECT
    s.machine_id,
    r.plant_id,
    r.machine_type,
    r.location_zone,
    r.criticality_class,
    s.reading_ts,
    s.risk_score,
    s.risk_tier,
    s.top_signal,
    s.recommended_action,
    s.model_version
FROM SCORED_TELEMETRY_RESULTS s
JOIN MACHINE_REGISTRY r ON s.machine_id = r.machine_id
QUALIFY ROW_NUMBER() OVER (PARTITION BY s.machine_id ORDER BY s.reading_ts DESC) = 1;

-- =============================================================
-- View: V_RISK_TREND_24H
-- Hourly risk trend for the last 24 hours per machine
-- =============================================================
CREATE OR REPLACE VIEW V_RISK_TREND_24H AS
SELECT
    s.machine_id,
    r.plant_id,
    r.machine_type,
    s.reading_ts,
    s.risk_score,
    s.risk_tier,
    s.top_signal
FROM SCORED_TELEMETRY_RESULTS s
JOIN MACHINE_REGISTRY r ON s.machine_id = r.machine_id
WHERE s.reading_ts >= ADD_HOURS(CURRENT_TIMESTAMP, -24);

-- =============================================================
-- View: V_ACTIONABLE_RISK
-- Provides a prioritized, human-friendly actionable table for ops
-- =============================================================
CREATE OR REPLACE VIEW V_ACTIONABLE_RISK AS
WITH latest AS (
    SELECT *
    FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
), ranked AS (
    SELECT
        l.machine_id,
        l.plant_id,
        l.machine_type,
        l.location_zone,
        l.criticality_class,
        l.reading_ts,
        l.risk_score,
        l.risk_tier,
        l.top_signal,
        l.recommended_action AS action,
        CASE
            WHEN l.top_signal = 'VIBRATION' THEN 'Elevated vibration relative to baseline (Z-score)'
            WHEN l.top_signal = 'TEMPERATURE' THEN 'Elevated temperature relative to baseline (Z-score)'
            WHEN l.top_signal = 'PRESSURE' THEN 'Pressure deviation from baseline'
            WHEN l.top_signal = 'POWER' THEN 'Power draw anomaly relative to baseline'
            WHEN l.top_signal = 'SERVICE_OVERDUE' THEN 'Approaching or past scheduled service interval'
            WHEN l.top_signal = 'ERROR_CODE_E5XX' THEN 'Critical error code observed (E5xx)'
            WHEN l.top_signal = 'DATA_LOSS' THEN 'Telemetry or sensor data missing - investigate connectivity'
            ELSE l.top_signal
        END AS reason,
        ROW_NUMBER() OVER (ORDER BY l.risk_score DESC) AS priority_rank
    FROM latest l
)
SELECT
    machine_id,
    plant_id,
    machine_type,
    location_zone,
    criticality_class,
    reading_ts,
    risk_score,
    risk_tier,
    top_signal,
    action,
    reason,
    priority_rank,
    CASE WHEN priority_rank <= 5 THEN TRUE ELSE FALSE END AS is_top_5
FROM ranked;

