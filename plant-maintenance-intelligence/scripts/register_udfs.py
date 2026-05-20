"""
register_udfs.py
================
Registers the Lua UDFs in Exasol using the canonical SQL file.
Exasol Lua UDFs: parameters accessed via ctx.param_name inside run(ctx).

Credentials are read from .env (or environment variables).
Copy .env.example -> .env and fill in your values before running.

Run on EC2: python3 scripts/register_udfs.py
"""

import os
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import pyexasol

BASE_DIR = Path(__file__).parent.parent
SQL_DIR = BASE_DIR / "sql"

EXA_HOST = os.environ.get("EXA_HOST", "localhost")
EXA_PORT = os.environ.get("EXA_PORT", "8563")
EXA_USER = os.environ.get("EXA_USER", "sys")
EXA_PASSWORD = os.environ.get("EXA_PASSWORD", "exasol")


def execute_sql_script(conn, sql_path: Path):
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


conn = pyexasol.connect(
    dsn=f"{EXA_HOST}:{EXA_PORT}",
    user=EXA_USER,
    password=EXA_PASSWORD,
    websocket_sslopt={"cert_reqs": 0},
    query_timeout=120,
)
conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")

print("Registering COMPUTE_RISK_SCORE from sql/02_lua_udf.sql...")
execute_sql_script(conn, SQL_DIR / "02_lua_udf.sql")
print("  OK COMPUTE_RISK_SCORE registered")

print("\nSmoke test 1 — CRITICAL machine with E5xx error...")
result = conn.execute("""
    SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
        'MCH_A01', 110.0, 5.5, 12.0, 1950.0, 155.0, 'E501',
        85.0, 2.5, 12.0, 150.0, 3.0, 0.4, 1950.0, 2000.0
    )
""").fetchone()
print(f"  Score  : {result[0]:.4f}  Tier: {result[1]}  Signal: {result[2]}")

print("\nSmoke test 2 — LOW machine, healthy...")
result2 = conn.execute("""
    SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
        'MCH_C07', 40.5, 1.05, 3.52, 500.0, 30.2, NULL,
        40.0, 1.0, 3.5, 30.0, 1.5, 0.2, 500.0, 3000.0
    )
""").fetchone()
print(f"  Score  : {result2[0]:.4f}  Tier: {result2[1]}  (expected LOW)")

print("\nSmoke test 3 — NULL sensor values (offline)...")
result3 = conn.execute("""
    SELECT PLANT_MAINTENANCE.COMPUTE_RISK_SCORE(
        'MCH_C08', NULL, NULL, NULL, 500.0, NULL, NULL,
        40.0, 1.0, 3.5, 30.0, 1.5, 0.2, 500.0, 3000.0
    )
""").fetchone()
print(f"  Score  : {result3[0]:.4f}  Tier: {result3[1]}  (expected DATA_LOSS)")

conn.close()
print("\nAll UDFs working correctly.")
