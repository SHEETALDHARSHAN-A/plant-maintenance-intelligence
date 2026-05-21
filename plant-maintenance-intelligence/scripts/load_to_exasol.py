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
Copy .env.example -> .env and fill in your values before running.
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

BASE_DIR = Path(__file__).parent.parent
SQL_DIR = BASE_DIR / "sql"
DATA_DIR = BASE_DIR / "data"
REGISTRY_CSV = DATA_DIR / "machine_registry.csv"
TELEMETRY_CSV = DATA_DIR / "machine_telemetry.csv"


def execute_sql_script(conn, sql_path: Path):
    """Execute a SQL script that uses / as the statement terminator."""
    script = sql_path.read_text(encoding="utf-8")
    statement_lines = []

    for line in script.splitlines():
        if line.strip() == "/":
            statement = "\n".join(statement_lines).strip()
            if statement:
                conn.execute(statement)
            statement_lines = []
        else:
            statement_lines.append(line)

    tail = "\n".join(statement_lines).strip()
    if tail:
        conn.execute(tail)


# ── Connection ─────────────────────────────────────────────────────────────────
def connect(host, port, user, password):
    logger.info("=" * 70)
    logger.info(f"Connecting to Exasol at {host}:{port} as {user}")
    logger.info("=" * 70)
    print(f"\nConnecting to Exasol at {host}:{port} as {user}...")

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
            print("   OK Connected")
            return conn
        except Exception as exc:
            logger.warning(f"Connection attempt {attempt}/3 failed: {exc}")
            print(f"   Attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                logger.debug("Retrying in 5 seconds...")
                time.sleep(5)

    logger.critical("Could not connect to Exasol after 3 attempts")
    print("ERROR: Could not connect after 3 attempts.")
    sys.exit(1)


# ── Schema SQL ────────────────────────────────────────────────────────────────
def run_schema(conn):
    logger.info("Creating schema, tables, and views")
    print("\n Creating schema, tables, views...")

    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS PLANT_MAINTENANCE")
        logger.debug("Schema PLANT_MAINTENANCE created/verified")
        conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")
        logger.debug("Schema PLANT_MAINTENANCE opened")

        objects_to_drop = [
            "VIEW V_ACTIONABLE_RISK",
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
            except Exception as exc:
                logger.debug(f"Could not drop {obj} (may not exist): {exc}")

        conn.execute("""
CREATE TABLE MACHINE_REGISTRY (
    machine_id                VARCHAR(20)  NOT NULL,
    plant_id                  VARCHAR(10)  NOT NULL,
    machine_type              VARCHAR(50)   NOT NULL,
    install_date              DATE          NOT NULL,
    baseline_temp_c           DECIMAL(6,2)  NOT NULL,
    baseline_vibration        DECIMAL(6,3)  NOT NULL,
    baseline_pressure_bar     DECIMAL(6,2)  NOT NULL,
    baseline_power_kw         DECIMAL(8,2)  NOT NULL,
    baseline_stddev_temp      DECIMAL(6,3)  NOT NULL,
    baseline_stddev_vibration DECIMAL(6,3)  NOT NULL,
    service_interval_hours    DECIMAL(8,1)  NOT NULL,
    last_service_ts           TIMESTAMP     NOT NULL,
    location_zone             VARCHAR(20),
    criticality_class         VARCHAR(10)   DEFAULT 'STANDARD',
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
    (t.temperature_c - r.baseline_temp_c) / NULLIFZERO(r.baseline_stddev_temp) AS z_temp,
    (t.vibration_mm_s - r.baseline_vibration) / NULLIFZERO(r.baseline_stddev_vibration) AS z_vibration,
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
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.machine_id
    ORDER BY s.reading_ts DESC
) = 1""")
        logger.info("Created view: V_LATEST_RISK_SUMMARY")

        conn.execute("""
CREATE OR REPLACE VIEW V_ACTIONABLE_RISK AS
WITH latest AS (
    SELECT *
    FROM V_LATEST_RISK_SUMMARY
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
FROM ranked""")
        logger.info("Created view: V_ACTIONABLE_RISK")

        conn.execute("""
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
WHERE s.reading_ts >= ADD_HOURS(CURRENT_TIMESTAMP, -24)""")
        logger.info("Created view: V_RISK_TREND_24H")

        logger.info("Schema creation completed successfully")
        print("   OK Schema, tables, views created")
    except Exception as exc:
        logger.error(f"Schema creation failed: {exc}", exc_info=True)
        raise


# ── Lua UDFs ──────────────────────────────────────────────────────────────────
def register_udfs(conn):
    logger.info("Registering Lua UDFs")
    print("\n Registering Lua UDFs...")

    try:
        execute_sql_script(conn, SQL_DIR / "02_lua_udf.sql")
        print("   OK COMPUTE_RISK_SCORE registered from 02_lua_udf.sql")

        smoke = conn.execute("""
        SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
            'T', 110.0, 5.5, 12.0, 1950.0, 155.0, 'E501',
            85.0, 2.5, 12.0, 150.0, 3.0, 0.4, 1950.0, 2000.0)
        """).fetchone()
        print(f"   OK UDF smoke test: score={smoke[0]:.4f} tier={smoke[1]} signal={smoke[2]}")

        smoke_null = conn.execute("""
        SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
            'T', 40.0, 1.0, 3.5, 500.0, 30.0, NULL,
            40.0, 1.0, 3.5, 30.0, 1.5, 0.2, 500.0, 3000.0)
        """).fetchone()
        print(f"   OK NULL err test:  score={smoke_null[0]:.4f} tier={smoke_null[1]}")
    except Exception as exc:
        logger.error(f"UDF registration failed: {exc}", exc_info=True)
        raise


# ── CSV bulk load ──────────────────────────────────────────────────────────────
def bulk_load_csv(conn, csv_path, table, schema="PLANT_MAINTENANCE"):
    print(f"\nLoading {csv_path.name} -> {schema}.{table}...")
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with open(csv_path, encoding="utf-8") as file_handle:
        header = file_handle.readline().strip().split(",")
        row_count = sum(1 for _ in file_handle)
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
    print(f"   OK {row_count:,} rows loaded in {time.time() - start:.1f}s")


# ── Scoring ────────────────────────────────────────────────────────────────────
def run_scoring(conn):
    print("\n Running Lua UDF scoring across all telemetry...")
    conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")
    conn.execute("DELETE FROM SCORED_TELEMETRY_RESULTS")

    start = time.time()
    conn.execute("""
INSERT INTO SCORED_TELEMETRY_RESULTS
    (machine_id, reading_ts, risk_score, risk_tier, top_signal, recommended_action, model_version)
SELECT
    res.machine_id,
    res.reading_ts,
    CAST(res.risk_score AS DECIMAL(5,4)),
    res.risk_tier,
    res.top_signal,
    res.recommended_action,
    'LUA_V1'
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
            CASE WHEN f.error_code IS NULL OR TRIM(f.error_code) = ''
                 THEN NULL ELSE TRIM(f.error_code) END,
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
    WHERE f.operating_mode != 'MAINTENANCE'
) AS res
""")
    count = conn.execute("SELECT COUNT(*) FROM SCORED_TELEMETRY_RESULTS").fetchval()
    print(f"   OK {count:,} rows scored in {time.time() - start:.1f}s")


# ── Verification ───────────────────────────────────────────────────────────────
def verify(conn):
    print("\nVerification:")
    for table_name in ["MACHINE_REGISTRY", "MACHINE_TELEMETRY", "SCORED_TELEMETRY_RESULTS"]:
        count = conn.execute(f"SELECT COUNT(*) FROM PLANT_MAINTENANCE.{table_name}").fetchval()
        print(f"   {table_name:<35} {count:>8,} rows")

    print("\nRisk distribution (latest score per machine):")
    rows = conn.execute("""
        SELECT risk_tier,
               COUNT(DISTINCT machine_id) AS machines,
               ROUND(AVG(risk_score),4)   AS avg_score,
               LISTAGG(machine_id, ', ')  AS machine_ids
        FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
        GROUP BY risk_tier
        ORDER BY MAX(risk_score) DESC
    """).fetchall()
    for tier, machines, avg, machine_ids in rows:
        bar = "#" * int(float(avg) * 20)
        print(f"   {tier:<10} {machines} machine(s)  avg={avg:.4f}  {bar}")
        print(f"      -> {machine_ids}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Load data into Exasol and run risk scoring."
    )
    parser.add_argument("--host", default=os.environ.get("EXA_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXA_PORT", "8563")))
    parser.add_argument("--user", default=os.environ.get("EXA_USER", "sys"))
    parser.add_argument("--password", default=os.environ.get("EXA_PASSWORD", "exasol"))
    args = parser.parse_args()

    conn = connect(args.host, args.port, args.user, args.password)
    try:
        run_schema(conn)
        register_udfs(conn)
        bulk_load_csv(conn, REGISTRY_CSV, "MACHINE_REGISTRY")
        bulk_load_csv(conn, TELEMETRY_CSV, "MACHINE_TELEMETRY")
        run_scoring(conn)
        verify(conn)
        print("\nPhase 1 complete — Exasol loaded and scored. Ready for dashboard.\n")
    except Exception as exc:
        print(f"\nFailed: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
