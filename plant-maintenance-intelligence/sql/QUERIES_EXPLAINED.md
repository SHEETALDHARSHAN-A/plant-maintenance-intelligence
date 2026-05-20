# SQL Queries & Views — Demo Walkthrough

Every query and view used in this demo, explained line by line.
Written for someone who knows what SQL is but hasn't seen this schema before.

---

## The Schema at a Glance

```
MACHINE_REGISTRY          ← static info about each machine (10 rows)
       │
       ├── MACHINE_TELEMETRY          ← hourly sensor readings (7,200 rows)
       │          │
       │          └── V_TELEMETRY_FEATURES   ← view: adds Z-scores + service hours
       │
       └── SCORED_TELEMETRY_RESULTS   ← one risk score per reading (7,154 rows)
                  │
                  ├── V_LATEST_RISK_SUMMARY  ← view: worst recent score per machine
                  └── V_RISK_TREND_24H       ← view: last 24h of scores
```

---

## Table 1: MACHINE_REGISTRY

```sql
CREATE TABLE MACHINE_REGISTRY (
    machine_id                VARCHAR(20)  NOT NULL,
    plant_id                  VARCHAR(10)  NOT NULL,
    machine_type              VARCHAR(50)  NOT NULL,
    install_date              DATE         NOT NULL,

    -- "Normal" operating values for this specific machine.
    -- Every machine has different baselines — a turbine runs hotter than a pump.
    baseline_temp_c           DECIMAL(6,2) NOT NULL,
    baseline_vibration        DECIMAL(6,3) NOT NULL,
    baseline_pressure_bar     DECIMAL(6,2) NOT NULL,
    baseline_power_kw         DECIMAL(8,2) NOT NULL,

    -- Standard deviations of the baseline — how much natural variation is normal.
    -- Used to calculate Z-scores: how many sigmas away from normal is this reading?
    baseline_stddev_temp      DECIMAL(6,3) NOT NULL,
    baseline_stddev_vibration DECIMAL(6,3) NOT NULL,

    -- Service schedule. We use these to detect overdue maintenance.
    service_interval_hours    DECIMAL(8,1) NOT NULL,
    last_service_ts           TIMESTAMP    NOT NULL,

    location_zone             VARCHAR(20),
    -- CRITICAL = high business impact if it fails. STANDARD = normal priority.
    criticality_class         VARCHAR(10)  DEFAULT 'STANDARD',

    CONSTRAINT pk_machine_registry PRIMARY KEY (machine_id)
)
```

**Why it's designed this way:**
Each machine gets its own baseline values rather than using a global average.
A compressor running at 85°C is normal. A conveyor running at 85°C is on fire.
Storing per-machine baselines is what makes the Z-score approach work.

---

## Table 2: MACHINE_TELEMETRY

```sql
CREATE TABLE MACHINE_TELEMETRY (
    -- IDENTITY = auto-incrementing primary key. Exasol assigns this on insert.
    telemetry_id    DECIMAL(18,0) IDENTITY NOT NULL,
    machine_id      VARCHAR(20)   NOT NULL,
    reading_ts      TIMESTAMP     NOT NULL,

    -- All sensor columns are nullable — sensors go offline, networks drop.
    -- The scoring UDF handles NULLs with a +0.50 penalty (assume worst case).
    temperature_c   DECIMAL(7,2),
    vibration_mm_s  DECIMAL(7,3),
    pressure_bar    DECIMAL(7,2),
    runtime_hours   DECIMAL(10,1),
    power_kw        DECIMAL(8,2),

    -- RUNNING / IDLE / STARTUP / MAINTENANCE
    -- Rows with MAINTENANCE are excluded from scoring — machines behave
    -- abnormally during maintenance windows and would create false alarms.
    operating_mode  VARCHAR(20),

    -- NULL = no error. E5xx = critical fault code (adds +0.25 to risk score).
    -- Only E5xx prefix is treated as critical — other codes are informational.
    error_code      VARCHAR(10),

    -- Audit timestamp — when this row was loaded, not when the reading happened.
    load_ts         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_machine_telemetry  PRIMARY KEY (telemetry_id),
    CONSTRAINT fk_telemetry_machine  FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
)
```

**Why sensor columns are nullable:**
In real factories, sensors fail, networks drop, and maintenance windows create gaps.
Making them NOT NULL would cause bulk loads to fail on any missing reading.
The scoring logic handles NULLs explicitly — it doesn't silently ignore them.

---

## Table 3: SCORED_TELEMETRY_RESULTS

```sql
CREATE TABLE SCORED_TELEMETRY_RESULTS (
    score_id           DECIMAL(18,0) IDENTITY NOT NULL,
    machine_id         VARCHAR(20)   NOT NULL,
    reading_ts         TIMESTAMP     NOT NULL,

    -- The core output: a number from 0.0000 (healthy) to 1.0000 (critical).
    -- Computed by the Lua UDF COMPUTE_RISK_SCORE.
    risk_score         DECIMAL(5,4)  NOT NULL,

    -- Derived from risk_score: LOW / MEDIUM / HIGH / CRITICAL
    -- Thresholds: CRITICAL >= 0.80, HIGH >= 0.60, MEDIUM >= 0.35, else LOW
    risk_tier          VARCHAR(10)   NOT NULL,

    -- Which sensor drove the score highest: VIBRATION, TEMPERATURE,
    -- PRESSURE, SERVICE_OVERDUE, POWER, or ERROR_CODE_E5XX
    top_signal         VARCHAR(50),

    -- Human-readable action: "IMMEDIATE SHUTDOWN..." / "Schedule within 24h" etc.
    recommended_action VARCHAR(200),

    -- Model version tag — lets us track which version of the scoring logic
    -- produced each row. Useful when we retune weights and want to compare.
    model_version      VARCHAR(20)   DEFAULT 'LUA_V1',

    -- When the score was computed, not when the sensor reading happened.
    scored_at          TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_scored_results  PRIMARY KEY (score_id),
    CONSTRAINT fk_scored_machine  FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
)
```

**Why store the tier and action as columns instead of computing them on the fly:**
The dashboard queries this table constantly. Pre-computing and storing the tier
means the dashboard never has to run CASE expressions on every query.
It also means we have a historical record of what tier each reading was assigned,
even if we later change the thresholds.

---

## View 1: V_TELEMETRY_FEATURES

This is the most important view. It's the input to the scoring UDF.

```sql
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

    -- Z-score for temperature: how many standard deviations above/below normal?
    -- Formula: (actual - baseline) / stddev
    -- NULLIFZERO prevents division-by-zero if stddev is 0 (returns NULL instead).
    -- A Z-score of 0 = exactly normal. Z=2 = unusual. Z=3+ = very abnormal.
    (t.temperature_c  - r.baseline_temp_c)    / NULLIFZERO(r.baseline_stddev_temp)      AS z_temp,
    (t.vibration_mm_s - r.baseline_vibration) / NULLIFZERO(r.baseline_stddev_vibration) AS z_vibration,

    -- 24-hour rolling average temperature per machine.
    -- Uses a window function: looks back 24 hours from each reading.
    -- This smooths out spikes and shows the trend, not just the instant value.
    -- Note: this is computed in the view but not currently used by the scoring UDF.
    -- It's here for future use in trend-based alerting.
    AVG(t.temperature_c) OVER (
        PARTITION BY t.machine_id          -- separate window per machine
        ORDER BY t.reading_ts
        RANGE BETWEEN INTERVAL '24' HOUR PRECEDING AND CURRENT ROW
    ) AS rolling_avg_temp_24h,

    AVG(t.vibration_mm_s) OVER (
        PARTITION BY t.machine_id
        ORDER BY t.reading_ts
        RANGE BETWEEN INTERVAL '24' HOUR PRECEDING AND CURRENT ROW
    ) AS rolling_avg_vibration_24h,

    -- Hours elapsed since the last service event.
    -- SECONDS_BETWEEN returns the difference in seconds; divide by 3600 for hours.
    -- This feeds the "service overdue" component of the risk score.
    SECONDS_BETWEEN(r.last_service_ts, t.reading_ts) / 3600.0 AS hours_since_service,

    -- Pass through all baseline values so the UDF has everything it needs
    -- without needing to join to MACHINE_REGISTRY itself.
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
-- Inner join: if a telemetry row has no matching machine in the registry,
-- it's dropped. This enforces referential integrity at the view level.
JOIN MACHINE_REGISTRY r ON t.machine_id = r.machine_id
```

**Why a view instead of a subquery in the scoring INSERT:**
The scoring INSERT is already complex enough. Putting the join and Z-score
calculations in a view keeps the INSERT readable and lets us test the
feature calculations independently.

**Why Z-scores instead of raw values:**
A temperature of 90°C means nothing without context. Is that normal for this machine?
Z-scores normalize across all machine types — a Z-score of 3.0 means "3 standard
deviations above normal" regardless of whether it's a turbine or a conveyor.

---

## View 2: V_LATEST_RISK_SUMMARY

The dashboard's main view — shows the current health of every machine.

```sql
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

-- The WHERE clause is the critical design decision here.
-- We want the WORST score each machine has seen in the last 144 hours (6 days),
-- not just the single most recent reading.
--
-- Why not MAX(score_id)?
--   score_id is an IDENTITY column — it increments in DB storage order,
--   not time order. Exasol is a columnar DB and doesn't guarantee row order.
--   MAX(score_id) could return any hour, not the latest one.
--
-- Why not MAX(reading_ts)?
--   A single reading at the last timestamp can be noisy (Gaussian noise in
--   the mock data). The score at exactly hour 719 might be 0.52 even though
--   the machine peaked at 0.83 earlier in the window.
--
-- Why MAX(risk_score) within a 144-hour window?
--   This shows the worst state the machine has been in recently.
--   For a maintenance dashboard, "what's the worst it's been lately?"
--   is more actionable than "what was the exact last reading?".
--   144 hours = 6 days — enough to capture a full degradation cycle.
WHERE (s.machine_id, s.risk_score) IN (
    SELECT machine_id, MAX(risk_score)
    FROM SCORED_TELEMETRY_RESULTS
    WHERE reading_ts >= (
        -- Anchor the window to the latest reading in the entire table,
        -- not to CURRENT_TIMESTAMP — because mock data is historical
        -- and CURRENT_TIMESTAMP would return zero rows.
        SELECT ADD_HOURS(MAX(reading_ts), -144)
        FROM SCORED_TELEMETRY_RESULTS
    )
    GROUP BY machine_id
)

---

## View 4: V_ACTIONABLE_RISK

This view maps the latest risk summary into a concise, human-friendly actionable table for operators. It includes:

- `reason`: a short plain-language explanation derived from `top_signal` (e.g., "Elevated vibration relative to baseline").
- `action`: the `recommended_action` produced by the scoring UDF.
- `priority_rank`: a rank ordered by `risk_score` (1 = highest risk).
- `is_top_5`: boolean flag for the highest-priority five machines.

Use this view to drive top-N notifications, runbooks, or a simple "what to do next" export for operations.
```

**The bug this view fixed:**
The original version used `WHERE s.score_id IN (SELECT MAX(score_id) ...)`.
Because `score_id` is an IDENTITY column in a columnar database, `MAX(score_id)`
was returning a random mid-range hour instead of the latest one.
All machines showed scores around 0.58 (MEDIUM) instead of their actual peak scores.

---

## View 3: V_RISK_TREND_24H

Used by the trend chart on the dashboard.

```sql
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
-- Only return rows from the last 24 hours.
-- ADD_HOURS is an Exasol function — equivalent to DATEADD in SQL Server.
-- For mock data (which is historical), this view returns nothing because
-- the data timestamps are in April 2026, not today.
-- The dashboard trend chart uses all scored results instead for the demo.
WHERE s.reading_ts >= ADD_HOURS(CURRENT_TIMESTAMP, -24)
```

**Note for production:**
When connected to real sensors streaming live data, this view will work as intended.
For the demo with historical mock data, the dashboard queries
`SCORED_TELEMETRY_RESULTS` directly with `ORDER BY reading_ts ASC` instead.

---

## The Scoring INSERT

This is the query that runs the Lua UDF across all telemetry rows.

```sql
INSERT INTO SCORED_TELEMETRY_RESULTS
    (machine_id, reading_ts, risk_score, risk_tier, top_signal, recommended_action, model_version)
SELECT
    machine_id,
    reading_ts,

    -- The UDF returns a pipe-delimited string: "0.8361|CRITICAL|VIBRATION|IMMEDIATE SHUTDOWN..."
    -- We parse it using SQL string functions instead of a second Lua UDF.
    --
    -- Why SQL string functions instead of a Lua parser UDF?
    -- We originally had a PARSE_RISK_SCORE_FIELD Lua UDF to split the string.
    -- It had a bug: Exasol passes integer literals (1, 2, 3) as Lua "userdata",
    -- not as numbers. The null check reset the index to 1 every time, so all
    -- four columns stored the same numeric score value.
    -- SQL INSTR/SUBSTR has no such issue.

    -- Field 1: the numeric score (everything before the first pipe)
    CAST(SUBSTR(raw_result, 1, INSTR(raw_result, '|') - 1) AS DECIMAL(5,4)),

    -- Field 2: the tier string (between pipe 1 and pipe 2)
    SUBSTR(raw_result,
           INSTR(raw_result, '|') + 1,
           INSTR(raw_result, '|', INSTR(raw_result, '|') + 1)
             - INSTR(raw_result, '|') - 1),

    -- Field 3: the top signal (between pipe 2 and pipe 3)
    SUBSTR(raw_result,
           INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1,
           INSTR(raw_result, '|', INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1)
             - INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) - 1),

    -- Field 4: the recommended action (everything after pipe 3)
    SUBSTR(raw_result,
           INSTR(raw_result, '|', INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1) + 1),

    'LUA_V1'

FROM (
    -- Inner subquery: call the UDF once per row, get the raw pipe-delimited string.
    -- We call it here so we only invoke the UDF once per row, not four times.
    SELECT
        f.machine_id,
        f.reading_ts,
        COMPUTE_RISK_SCORE(
            f.machine_id,

            -- COALESCE: if the sensor reading is NULL, fall back to the baseline.
            -- In the UDF, if required sensors (temperature or vibration) are missing,
            -- a distinct `DATA_LOSS` tier is emitted rather than blending a penalty
            -- into the numeric score. This flags telemetry/connectivity issues for IT.
            COALESCE(f.temperature_c,  f.baseline_temp_c),
            COALESCE(f.vibration_mm_s, f.baseline_vibration),
            COALESCE(f.pressure_bar,   f.baseline_pressure_bar),
            COALESCE(f.runtime_hours,  0),
            COALESCE(f.power_kw,       f.baseline_power_kw),

            -- Normalize error_code: empty string and NULL both become NULL.
            -- The UDF checks for NULL to skip the E5xx premium.
            CASE WHEN f.error_code IS NULL OR TRIM(f.error_code) = ''
                 THEN NULL ELSE TRIM(f.error_code) END,

            -- Pass all baseline values so the UDF can compute deviations.
            f.baseline_temp_c,
            f.baseline_vibration,
            f.baseline_pressure_bar,
            f.baseline_power_kw,
            f.baseline_stddev_temp,
            f.baseline_stddev_vibration,

            -- GREATEST ensures we never pass a negative hours_since_service.
            -- This can happen if last_service_ts is in the future (data error).
            GREATEST(f.hours_since_service, 0),
            f.service_interval_hours

        ) AS raw_result
    FROM V_TELEMETRY_FEATURES f

    -- Skip MAINTENANCE mode rows entirely.
    -- During maintenance, machines are intentionally shut down or running abnormally.
    -- Scoring them would generate false CRITICAL alerts.
    WHERE f.operating_mode != 'MAINTENANCE'

) scored

-- Drop any rows where the UDF returned NULL (shouldn't happen, but defensive).
WHERE raw_result IS NOT NULL
```

---

## Dashboard Queries

### KPI Row — Count of machines per tier

```sql
SELECT
    -- Re-derive the tier from the numeric score using a CASE expression.
    -- The dashboard does this instead of using the stored risk_tier column
    -- so the thresholds can be adjusted in one place (the Python function
    -- tier_case_sql()) without reloading all the data.
    CASE
        WHEN risk_score >= 0.80 THEN 'CRITICAL'
        WHEN risk_score >= 0.60 THEN 'HIGH'
        WHEN risk_score >= 0.35 THEN 'MEDIUM'
        ELSE 'LOW'
    END AS RISK_TIER,
    COUNT(DISTINCT machine_id) AS machine_count,
    ROUND(AVG(risk_score), 4)  AS avg_score
FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
WHERE UPPER(TRIM(plant_id)) IN ('PLANT_A', 'PLANT_B', 'PLANT_C')  -- from filter
  AND <tier_case_expression> IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')  -- from filter
GROUP BY <tier_case_expression>
ORDER BY MAX(risk_score) DESC
```

### Machine Drilldown — Last 72 sensor readings

```sql
SELECT
    TO_CHAR(reading_ts, 'YYYY-MM-DD HH24:MI') AS READING_TIME,
    temperature_c   AS TEMP_C,
    vibration_mm_s  AS VIB_MM_S,
    pressure_bar    AS PRES_BAR,
    power_kw        AS POWER_KW,
    operating_mode  AS OPERATING_MODE,
    error_code      AS ERROR_CODE
FROM PLANT_MAINTENANCE.MACHINE_TELEMETRY
WHERE machine_id = 'MCH_A01'   -- selected machine
ORDER BY reading_ts DESC
LIMIT 72   -- last 72 hours
```

### Error Code Analysis — Frequency per machine

```sql
SELECT
    t.machine_id AS MACHINE,
    r.plant_id   AS PLANT,
    t.error_code AS ERROR_CODE,
    COUNT(*)     AS OCCURRENCES,
    TO_CHAR(MIN(t.reading_ts), 'YYYY-MM-DD HH24:MI') AS FIRST_SEEN,
    TO_CHAR(MAX(t.reading_ts), 'YYYY-MM-DD HH24:MI') AS LAST_SEEN
FROM PLANT_MAINTENANCE.MACHINE_TELEMETRY t
JOIN PLANT_MAINTENANCE.MACHINE_REGISTRY r ON t.machine_id = r.machine_id
WHERE t.error_code IS NOT NULL
  AND TRIM(t.error_code) != ''
  AND UPPER(TRIM(r.plant_id)) IN ('PLANT_A', 'PLANT_B', 'PLANT_C')
GROUP BY t.machine_id, r.plant_id, t.error_code
ORDER BY COUNT(*) DESC
LIMIT 50
```

**Why LIMIT 50:**
In production with years of data, a machine could have thousands of distinct
error events. We cap at 50 for dashboard performance. A real system would
add a date range filter here.

---

## Verification Query (used after scoring)

```sql
SELECT
    risk_tier,
    COUNT(DISTINCT machine_id)  AS machine_count,
    ROUND(AVG(risk_score), 4)   AS avg_score,
    LISTAGG(machine_id, ', ')   AS machine_ids
FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
GROUP BY risk_tier
ORDER BY MAX(risk_score) DESC
```

**LISTAGG** is an Exasol aggregate function that concatenates strings within a group.
It's the equivalent of `GROUP_CONCAT` in MySQL or `STRING_AGG` in PostgreSQL.
We use it here to show which machines are in each tier in a single row.

---

## Known Limitations of These Queries

**1. No parameterized queries in the dashboard**
The dashboard builds SQL strings with Python f-strings. This is fine for a prototype
where the only inputs are plant names and tier names from our own database.
In production with user-supplied input, use parameterized queries to prevent SQL injection.

**2. V_RISK_TREND_24H doesn't work with mock data**
The view filters on `CURRENT_TIMESTAMP - 24 hours`. Mock data timestamps are in
April 2026, so this view always returns zero rows in the demo.
The dashboard works around this by querying `SCORED_TELEMETRY_RESULTS` directly.

**3. V_LATEST_RISK_SUMMARY uses a correlated subquery**
The `WHERE reading_ts >= (SELECT ADD_HOURS(MAX(reading_ts), -144) ...)` is a
correlated subquery that scans the entire table to find the max timestamp.
In production with millions of rows, this should be replaced with a materialized
view or a pre-computed "latest window" table that gets updated incrementally.

**4. No indexes**
Exasol is a columnar database — it doesn't use traditional B-tree indexes.
It uses column compression and parallel scan instead. For this prototype size
(7,200 rows), query times are under 100ms. At 10M+ rows, need to add distribution keys and partition pruning.
