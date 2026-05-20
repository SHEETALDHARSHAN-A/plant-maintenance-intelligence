"""
load_to_exasol.py
=================
Connects to Exasol CE and:
  1. Creates schema + tables + views
  2. Registers Lua UDFs (run(ctx) pattern)
  3. Bulk-loads CSVs
  4. Runs scoring INSERT
  5. Prints verification summary

Credentials are read from .env (or environment variables).
Copy .env.example → .env and fill in your values before running.
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logger

# Load .env file if present — never required, env vars always take precedence
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on env vars or CLI args

# Setup logger
logger = setup_logger(__name__, Path(__file__).parent.parent / "logs")

try:
    import pyexasol
except ImportError:
    logger.critical("pyexasol not installed")
    print("ERROR: pyexasol not installed. Run: pip install pyexasol")
    sys.exit(1)

BASE_DIR     = Path(__file__).parent.parent
SQL_DIR      = BASE_DIR / "sql"
DATA_DIR     = BASE_DIR / "data"
REGISTRY_CSV = DATA_DIR / "machine_registry.csv"
TELEMETRY_CSV= DATA_DIR / "machine_telemetry.csv"


# ── Connection ─────────────────────────────────────────────────────────────────
def connect(host, port, user, password):
    logger.info("=" * 70)
    logger.info(f"Connecting to Exasol at {host}:{port} as {user}")
    logger.info("=" * 70)
    print(f"\n🔌 Connecting to Exasol at {host}:{port} as {user}...")
    
    for attempt in range(1, 4):
        try:
            logger.debug(f"Connection attempt {attempt}/3")
            conn = pyexasol.connect(
                dsn=f"{host}:{port}",
                user=user,
                password=password,
                websocket_sslopt={"cert_reqs": 0},
                fetch_size_bytes=50 * 1024 * 1024,
                query_timeout=300,
            )
            logger.info("Connection established successfully")
            version_query = "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
            logger.debug(f"Exasol version: {conn.execute(version_query).fetchval()}")
            print("   ✓ Connected")
            return conn
        except Exception as exc:
            logger.warning(f"Connection attempt {attempt}/3 failed: {exc}")
            print(f"   Attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                logger.debug(f"Retrying in 5 seconds...")
                time.sleep(5)
    
    logger.critical("Could not connect to Exasol after 3 attempts")
    print("ERROR: Could not connect after 3 attempts.")
    sys.exit(1)


# ── Schema SQL (no Lua UDFs — those are registered separately) ─────────────────
def run_schema(conn):
    logger.info("Creating schema, tables, and views")
    print("\n📄 Creating schema, tables, views...")
    
    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS PLANT_MAINTENANCE")
        logger.debug("Schema PLANT_MAINTENANCE created/verified")
        conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")
        logger.debug("Schema PLANT_MAINTENANCE opened")

        # Drop in FK-safe order
        objects_to_drop = [
            "VIEW V_RISK_TREND_24H",
            "VIEW V_LATEST_RISK_SUMMARY",
            "VIEW V_TELEMETRY_FEATURES",
            "TABLE SCORED_TELEMETRY_RESULTS",
            "TABLE MACHINE_TELEMETRY",
            "TABLE MACHINE_REGISTRY",
        ]
        
        for obj in objects_to_drop:
            try:
                conn.execute(f"DROP {obj}")
                logger.debug(f"Dropped {obj}")
            except Exception as e:
                logger.debug(f"Could not drop {obj} (may not exist): {e}")
                pass

        conn.execute("""
CREATE TABLE MACHINE_REGISTRY (
    machine_id                VARCHAR(20)  NOT NULL,
    plant_id                  VARCHAR(10)  NOT NULL,
    machine_type              VARCHAR(50)  NOT NULL,
    install_date              DATE         NOT NULL,
    baseline_temp_c           DECIMAL(6,2) NOT NULL,
    baseline_vibration        DECIMAL(6,3) NOT NULL,
    baseline_pressure_bar     DECIMAL(6,2) NOT NULL,
    baseline_power_kw         DECIMAL(8,2) NOT NULL,
    baseline_stddev_temp      DECIMAL(6,3) NOT NULL,
    baseline_stddev_vibration DECIMAL(6,3) NOT NULL,
    service_interval_hours    DECIMAL(8,1) NOT NULL,
    last_service_ts           TIMESTAMP    NOT NULL,
    location_zone             VARCHAR(20),
    criticality_class         VARCHAR(10)  DEFAULT 'STANDARD',
    CONSTRAINT pk_machine_registry PRIMARY KEY (machine_id)
)""")
        logger.info("Created table: MACHINE_REGISTRY")

        conn.execute("""
CREATE TABLE MACHINE_TELEMETRY (
    telemetry_id    DECIMAL(18,0) IDENTITY NOT NULL,
    machine_id      VARCHAR(20)   NOT NULL,
    reading_ts      TIMESTAMP     NOT NULL,
    temperature_c   DECIMAL(7,2),
    vibration_mm_s  DECIMAL(7,3),
    pressure_bar    DECIMAL(7,2),
    runtime_hours   DECIMAL(10,1),
    power_kw        DECIMAL(8,2),
    operating_mode  VARCHAR(20),
    error_code      VARCHAR(10),
    load_ts         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_machine_telemetry PRIMARY KEY (telemetry_id),
    CONSTRAINT fk_telemetry_machine FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
)""")
        logger.info("Created table: MACHINE_TELEMETRY")

        conn.execute("""
CREATE TABLE SCORED_TELEMETRY_RESULTS (
    score_id           DECIMAL(18,0) IDENTITY NOT NULL,
    machine_id         VARCHAR(20)   NOT NULL,
    reading_ts         TIMESTAMP     NOT NULL,
    risk_score         DECIMAL(5,4)  NOT NULL,
    risk_tier          VARCHAR(10)   NOT NULL,
    top_signal         VARCHAR(50),
    recommended_action VARCHAR(200),
    model_version      VARCHAR(20)   DEFAULT 'LUA_V1',
    scored_at          TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_scored_results PRIMARY KEY (score_id),
    CONSTRAINT fk_scored_machine FOREIGN KEY (machine_id)
        REFERENCES MACHINE_REGISTRY(machine_id)
)""")
        logger.info("Created table: SCORED_TELEMETRY_RESULTS")

        conn.execute("""
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
    (t.temperature_c  - r.baseline_temp_c)      / NULLIFZERO(r.baseline_stddev_temp)      AS z_temp,
    (t.vibration_mm_s - r.baseline_vibration)   / NULLIFZERO(r.baseline_stddev_vibration) AS z_vibration,
    AVG(t.temperature_c) OVER (
        PARTITION BY t.machine_id ORDER BY t.reading_ts
        RANGE BETWEEN INTERVAL '24' HOUR PRECEDING AND CURRENT ROW
    ) AS rolling_avg_temp_24h,
    AVG(t.vibration_mm_s) OVER (
        PARTITION BY t.machine_id ORDER BY t.reading_ts
        RANGE BETWEEN INTERVAL '24' HOUR PRECEDING AND CURRENT ROW
    ) AS rolling_avg_vibration_24h,
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
JOIN MACHINE_REGISTRY r ON t.machine_id = r.machine_id""")
        logger.info("Created view: V_TELEMETRY_FEATURES")

        conn.execute("""
CREATE OR REPLACE VIEW V_LATEST_RISK_SUMMARY AS
SELECT
    s.machine_id, r.plant_id, r.machine_type, r.location_zone,
    r.criticality_class, s.reading_ts, s.risk_score, s.risk_tier,
    s.top_signal, s.recommended_action, s.model_version
FROM SCORED_TELEMETRY_RESULTS s
JOIN MACHINE_REGISTRY r ON s.machine_id = r.machine_id
WHERE (s.machine_id, s.risk_score) IN (
    SELECT machine_id, MAX(risk_score)
    FROM SCORED_TELEMETRY_RESULTS
    WHERE reading_ts >= (
        SELECT ADD_HOURS(MAX(reading_ts), -144)
        FROM SCORED_TELEMETRY_RESULTS
    )
    GROUP BY machine_id
)""")
        logger.info("Created view: V_LATEST_RISK_SUMMARY")

        conn.execute("""
CREATE OR REPLACE VIEW V_RISK_TREND_24H AS
SELECT
    s.machine_id, r.plant_id, r.machine_type,
    s.reading_ts, s.risk_score, s.risk_tier, s.top_signal
FROM SCORED_TELEMETRY_RESULTS s
JOIN MACHINE_REGISTRY r ON s.machine_id = r.machine_id
WHERE s.reading_ts >= ADD_HOURS(CURRENT_TIMESTAMP, -24)""")
        logger.info("Created view: V_RISK_TREND_24H")

        logger.info("Schema creation completed successfully")
        print("   ✓ Schema, tables, views created")
    except Exception as e:
        logger.error(f"Schema creation failed: {e}", exc_info=True)
        raise


# ── Lua UDFs (correct run(ctx) pattern for Exasol scalar UDFs) ────────────────
def register_udfs(conn):
    logger.info("Registering Lua UDFs")
    print("\n📄 Registering Lua UDFs...")
    
    try:
        conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")
        logger.debug("Schema opened for UDF registration")

        conn.execute("""
CREATE OR REPLACE LUA SCALAR SCRIPT COMPUTE_RISK_SCORE(
    mid  VARCHAR(20), tc   DOUBLE,  vib  DOUBLE,  pres DOUBLE,
    rth  DOUBLE,      pwr  DOUBLE,  err  VARCHAR(10),
    bt   DOUBLE,      bv   DOUBLE,  bp   DOUBLE,  bpwr DOUBLE,
    sdt  DOUBLE,      sdv  DOUBLE,  hsvc DOUBLE,  sint DOUBLE
) RETURNS VARCHAR(500) AS

local function clamp(v)
    if v < 0 then return 0 elseif v > 1 then return 1 else return v end
end
local function zscore(z)
    if z == nil then return 0 end
    if z < 1.0 then return z * 0.15
    elseif z < 2.0 then return 0.15 + (z-1.0)*0.30
    elseif z < 3.0 then return 0.45 + (z-2.0)*0.35
    else return clamp(0.80 + (z-3.0)*0.10) end
end
local function sdiv(a,b)
    if b==nil or b==0 then return 0 else return a/b end
end
-- Exasol passes SQL NULL as a Lua userdata, not nil
local function nn(v, fallback)
    if v == nil or type(v) == "userdata" then return fallback or 0 end
    return v
end
local function ns(v)
    if v == nil or type(v) == "userdata" then return "" end
    return tostring(v)
end

function run(ctx)
    local t  = nn(ctx.tc,   nn(ctx.bt,  0))
    local v  = nn(ctx.vib,  nn(ctx.bv,  0))
    local p  = nn(ctx.pres, nn(ctx.bp,  0))
    local pw = nn(ctx.pwr,  nn(ctx.bpwr,0))
    local e  = ns(ctx.err)
    local h  = nn(ctx.hsvc, 0)
    local si = nn(ctx.sint, 2000)
    local _bt  = nn(ctx.bt,   0)
    local _bv  = nn(ctx.bv,   0)
    local _bp  = nn(ctx.bp,   0)
    local _bpwr= nn(ctx.bpwr, 0)
    local _sdt = nn(ctx.sdt,  1)
    local _sdv = nn(ctx.sdv,  1)

    local sv = zscore(math.abs(sdiv(v-_bv,  _sdv))) * 0.30
    local st = zscore(math.abs(sdiv(t-_bt,  _sdt))) * 0.25
    local sp = clamp(math.abs(sdiv(p-_bp,   _bp )) * 3.0) * 0.20
    local ss = clamp(sdiv(h, si) - 0.8) * 5.0 * 0.15
    local sw = clamp(math.abs(sdiv(pw-_bpwr,_bpwr)) * 2.5) * 0.10
    local raw= sv + st + sp + ss + sw
    local ex = 0
    if string.match(e, "^E5") then ex = 0.25 end
    local sc = clamp(raw + ex)

    local tier
    if sc>=0.80 then tier="CRITICAL"
    elseif sc>=0.60 then tier="HIGH"
    elseif sc>=0.35 then tier="MEDIUM"
    else tier="LOW" end

    local sigs = {
        {n="VIBRATION",       v=sv/0.30},
        {n="TEMPERATURE",     v=st/0.25},
        {n="PRESSURE",        v=sp/0.20},
        {n="SERVICE_OVERDUE", v=ss/0.15},
        {n="POWER",           v=sw/0.10}
    }
    if ex > 0 then table.insert(sigs, {n="ERROR_CODE_E5XX", v=1.0}) end
    local tn, tv = "NONE", -1
    for _, s in ipairs(sigs) do
        if s.v > tv then tv = s.v; tn = s.n end
    end

    local act
    if tier=="CRITICAL" then act="IMMEDIATE SHUTDOWN - emergency maintenance within 4 hours"
    elseif tier=="HIGH"  then act="URGENT - schedule maintenance within 24 hours"
    elseif tier=="MEDIUM"then act="MONITOR - plan maintenance within 7 days; review "..tn
    else act="NORMAL - continue standard monitoring schedule" end

    return string.format("%.4f|%s|%s|%s", sc, tier, tn, act)
end
""")

        conn.execute("""
CREATE OR REPLACE LUA SCALAR SCRIPT PARSE_RISK_SCORE_FIELD(
    raw VARCHAR(500),
    idx DECIMAL(2,0)
) RETURNS VARCHAR(500) AS
function run(ctx)
    if ctx.raw == nil or type(ctx.raw) == "userdata" then return nil end
    local raw_str = tostring(ctx.raw)
    local parts = {}
    for p in string.gmatch(raw_str, "([^|]+)") do table.insert(parts, p) end
    local i = 1
    if ctx.idx ~= nil and type(ctx.idx) ~= "userdata" then
        i = math.floor(tonumber(tostring(ctx.idx)) or 1)
    end
    return parts[i]
end
""")
        print("   ✓ COMPUTE_RISK_SCORE + PARSE_RISK_SCORE_FIELD registered")

        # Smoke test
        r = conn.execute("""
        SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
            'T', 110.0, 5.5, 12.0, 1950.0, 155.0, 'E501',
            85.0, 2.5, 12.0, 150.0, 3.0, 0.4, 1950.0, 2000.0)
    """).fetchval()
        parts = r.split("|")
        print(f"   ✓ UDF smoke test: score={parts[0]} tier={parts[1]} signal={parts[2]}")

        # Test with NULL error_code (the common case)
        r2 = conn.execute("""
        SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
            'T', 40.0, 1.0, 3.5, 500.0, 30.0, NULL,
            40.0, 1.0, 3.5, 30.0, 1.5, 0.2, 500.0, 3000.0)
    """).fetchval()
        parts2 = r2.split("|")
        print(f"   ✓ NULL err test:  score={parts2[0]} tier={parts2[1]}")
    except Exception as e:
        logger.error(f"UDF registration failed: {e}", exc_info=True)
        raise


# ── CSV bulk load ──────────────────────────────────────────────────────────────
def bulk_load_csv(conn, csv_path, table, schema="PLANT_MAINTENANCE"):
    print(f"\n📥 Loading {csv_path.name} → {schema}.{table}...")
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with open(csv_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        row_count = sum(1 for _ in f)
    start = time.time()
    conn.import_from_file(
        csv_path,
        (schema, table),
        import_params={
            "skip": 1,
            "column_separator": ",",
            "row_separator": "LF",
            "null": "",
            "columns": header,
        },
    )
    print(f"   ✓ {row_count:,} rows loaded in {time.time()-start:.1f}s")


# ── Scoring ────────────────────────────────────────────────────────────────────
def run_scoring(conn):
    print("\n📄 Running Lua UDF scoring across all telemetry...")
    conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")
    conn.execute("DELETE FROM SCORED_TELEMETRY_RESULTS")

    start = time.time()
    # Parse the pipe-delimited UDF output using SQL string functions
    # Format: "0.8361|CRITICAL|VIBRATION|IMMEDIATE SHUTDOWN..."
    conn.execute("""
INSERT INTO SCORED_TELEMETRY_RESULTS
    (machine_id, reading_ts, risk_score, risk_tier, top_signal, recommended_action, model_version)
SELECT
    machine_id,
    reading_ts,
    CAST(SUBSTR(raw_result, 1, INSTR(raw_result, '|') - 1) AS DECIMAL(5,4)),
    SUBSTR(raw_result,
           INSTR(raw_result, '|') + 1,
           INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) - INSTR(raw_result, '|') - 1),
    SUBSTR(raw_result,
           INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1,
           INSTR(raw_result, '|', INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1)
             - INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) - 1),
    SUBSTR(raw_result,
           INSTR(raw_result, '|', INSTR(raw_result, '|', INSTR(raw_result, '|') + 1) + 1) + 1),
    'LUA_V1'
FROM (
    SELECT
        f.machine_id,
        f.reading_ts,
        COMPUTE_RISK_SCORE(
            f.machine_id,
            COALESCE(f.temperature_c,  f.baseline_temp_c),
            COALESCE(f.vibration_mm_s, f.baseline_vibration),
            COALESCE(f.pressure_bar,   f.baseline_pressure_bar),
            COALESCE(f.runtime_hours,  0),
            COALESCE(f.power_kw,       f.baseline_power_kw),
            CASE WHEN f.error_code IS NULL OR TRIM(f.error_code) = ''
                 THEN NULL ELSE TRIM(f.error_code) END,
            f.baseline_temp_c,
            f.baseline_vibration,
            f.baseline_pressure_bar,
            f.baseline_power_kw,
            f.baseline_stddev_temp,
            f.baseline_stddev_vibration,
            GREATEST(f.hours_since_service, 0),
            f.service_interval_hours
        ) AS raw_result
    FROM V_TELEMETRY_FEATURES f
    WHERE f.operating_mode != 'MAINTENANCE'
) scored
WHERE raw_result IS NOT NULL
""")
    count = conn.execute("SELECT COUNT(*) FROM SCORED_TELEMETRY_RESULTS").fetchval()
    print(f"   ✓ {count:,} rows scored in {time.time()-start:.1f}s")


# ── Verification ───────────────────────────────────────────────────────────────
def verify(conn):
    print("\n📊 Verification:")
    for tbl in ["MACHINE_REGISTRY", "MACHINE_TELEMETRY", "SCORED_TELEMETRY_RESULTS"]:
        n = conn.execute(f"SELECT COUNT(*) FROM PLANT_MAINTENANCE.{tbl}").fetchval()
        print(f"   {tbl:<35} {n:>8,} rows")

    print("\n📈 Risk distribution (latest score per machine):")
    rows = conn.execute("""
        SELECT risk_tier,
               COUNT(DISTINCT machine_id) AS machines,
               ROUND(AVG(risk_score),4)   AS avg_score,
               LISTAGG(machine_id, ', ')  AS machine_ids
        FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
        GROUP BY risk_tier
        ORDER BY MAX(risk_score) DESC
    """).fetchall()
    icons = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
    for tier, machines, avg, ids in rows:
        bar = "█" * int(float(avg) * 20)
        print(f"   {icons.get(tier,'⚪')} {tier:<10} {machines} machine(s)  avg={avg:.4f}  {bar}")
        print(f"      → {ids}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Load data into Exasol and run risk scoring."
    )
    # CLI args override .env, which overrides built-in defaults
    parser.add_argument("--host",     default=os.environ.get("EXA_HOST",     "localhost"))
    parser.add_argument("--port",     type=int, default=int(os.environ.get("EXA_PORT", "8563")))
    parser.add_argument("--user",     default=os.environ.get("EXA_USER",     "sys"))
    parser.add_argument("--password", default=os.environ.get("EXA_PASSWORD", "exasol"))
    args = parser.parse_args()

    conn = connect(args.host, args.port, args.user, args.password)
    try:
        run_schema(conn)
        register_udfs(conn)
        bulk_load_csv(conn, REGISTRY_CSV,  "MACHINE_REGISTRY")
        bulk_load_csv(conn, TELEMETRY_CSV, "MACHINE_TELEMETRY")
        run_scoring(conn)
        verify(conn)
        print("\nPhase 1 complete — Exasol loaded and scored. Ready for dashboard.\n")
    except Exception as exc:
        print(f"\nFailed: {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
