-- =============================================================
-- Phase 1: Scoring Execution Script
-- Runs COMPUTE_RISK_SCORE UDF across all telemetry rows
-- and populates SCORED_TELEMETRY_RESULTS
-- =============================================================

OPEN SCHEMA PLANT_MAINTENANCE;

-- Clear previous scores (idempotent re-run)
DELETE FROM SCORED_TELEMETRY_RESULTS;

-- Score all telemetry rows via the Lua EMITS UDF
INSERT INTO SCORED_TELEMETRY_RESULTS (
    machine_id,
    reading_ts,
    risk_score,
    risk_tier,
    top_signal,
    recommended_action,
    model_version
)
SELECT
    res.machine_id,
    res.reading_ts,
    CAST(res.risk_score AS DECIMAL(5,4)),
    res.risk_tier,
    res.top_signal,
    res.recommended_action,
    'LUA_V1' AS model_version
FROM (
    SELECT
        f.machine_id,
        f.reading_ts,
        COMPUTE_RISK_SCORE(
            f.machine_id,
            f.temperature_c,
            f.vibration_mm_s,
            f.pressure_bar,
            f.runtime_hours,
            f.power_kw,
            f.error_code,
            f.baseline_temp_c,
            f.baseline_vibration,
            f.baseline_pressure_bar,
            f.baseline_power_kw,
            f.baseline_stddev_temp,
            f.baseline_stddev_vibration,
            f.hours_since_service,
            f.service_interval_hours
        )
    FROM V_TELEMETRY_FEATURES f
    WHERE f.operating_mode != 'MAINTENANCE'   -- skip maintenance windows
) AS res;

-- Verify distribution
SELECT
    risk_tier,
    COUNT(*)            AS reading_count,
    COUNT(DISTINCT machine_id) AS machine_count,
    ROUND(AVG(risk_score), 4)  AS avg_score,
    ROUND(MIN(risk_score), 4)  AS min_score,
    ROUND(MAX(risk_score), 4)  AS max_score
FROM SCORED_TELEMETRY_RESULTS
GROUP BY risk_tier
ORDER BY MAX(risk_score) DESC;
