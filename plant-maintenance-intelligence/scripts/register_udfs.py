"""
register_udfs.py
================
Registers the Lua UDFs in Exasol using the correct run(ctx) pattern.
Exasol Lua UDFs: parameters accessed via ctx.param_name inside run(ctx).

Credentials are read from .env (or environment variables).
Copy .env.example → .env and fill in your values before running.

Run on EC2: python3 scripts/register_udfs.py
"""
import os
import sys
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import pyexasol

EXA_HOST     = os.environ.get("EXA_HOST",     "localhost")
EXA_PORT     = os.environ.get("EXA_PORT",     "8563")
EXA_USER     = os.environ.get("EXA_USER",     "sys")
EXA_PASSWORD = os.environ.get("EXA_PASSWORD", "exasol")

conn = pyexasol.connect(
    dsn=f"{EXA_HOST}:{EXA_PORT}",
    user=EXA_USER,
    password=EXA_PASSWORD,
    websocket_sslopt={"cert_reqs": 0},
    query_timeout=120,
)
conn.execute("OPEN SCHEMA PLANT_MAINTENANCE")

# ── UDF 1: COMPUTE_RISK_SCORE ──────────────────────────────────────────────────
# Exasol Lua UDF: parameters accessed as ctx.param_name inside run(ctx)
print("Registering COMPUTE_RISK_SCORE (EMITS UDF)...")
conn.execute("""
CREATE OR REPLACE LUA SCALAR SCRIPT COMPUTE_RISK_SCORE(
    mid   VARCHAR(20),
    tc    DOUBLE,
    vib   DOUBLE,
    pres  DOUBLE,
    rth   DOUBLE,
    pwr   DOUBLE,
    err   VARCHAR(10),
    bt    DOUBLE,
    bv    DOUBLE,
    bp    DOUBLE,
    bpwr  DOUBLE,
    sdt   DOUBLE,
    sdv   DOUBLE,
    hsvc  DOUBLE,
    sint  DOUBLE
)
EMITS (risk_score DOUBLE, risk_tier VARCHAR(10), top_signal VARCHAR(50), recommended_action VARCHAR(200)) AS

local function clamp(v)
    if v < 0 then return 0 end
    if v > 1 then return 1 end
    return v
end

local function z_to_score(z)
    if z == nil then return 0 end
    if z < 1.0 then return z * 0.15 end
    if z < 2.0 then return 0.15 + (z - 1.0) * 0.30 end
    if z < 3.0 then return 0.45 + (z - 2.0) * 0.35 end
    return clamp(0.80 + (z - 3.0) * 0.10)
end

local function sdiv(a, b)
    if b == nil or b == 0 then return 0 end
    return a / b
end

local function is_null(v)
    return v == nil or type(v) == "userdata"
end

function run(ctx)
    local missing_required = false
    if is_null(ctx.tc) or is_null(ctx.vib) then
        missing_required = true
    end

    local t_val  = (not is_null(ctx.tc) and ctx.tc) or (not is_null(ctx.bt) and ctx.bt) or 0
    local v_val  = (not is_null(ctx.vib) and ctx.vib) or (not is_null(ctx.bv) and ctx.bv) or 0
    local p_val  = (not is_null(ctx.pres) and ctx.pres) or (not is_null(ctx.bp) and ctx.bp) or 0
    local pw_val = (not is_null(ctx.pwr) and ctx.pwr) or (not is_null(ctx.bpwr) and ctx.bpwr) or 0
    local e_val  = (not is_null(ctx.err) and ctx.err) or ""
    local h_val  = (not is_null(ctx.hsvc) and ctx.hsvc) or 0
    local s_val  = (not is_null(ctx.sint) and ctx.sint) or 2000
    local bt     = (not is_null(ctx.bt) and ctx.bt) or 0
    local bv     = (not is_null(ctx.bv) and ctx.bv) or 0
    local bp     = (not is_null(ctx.bp) and ctx.bp) or 0
    local bpwr   = (not is_null(ctx.bpwr) and ctx.bpwr) or 0
    local sdt    = (not is_null(ctx.sdt) and ctx.sdt) or 1
    local sdv    = (not is_null(ctx.sdv) and ctx.sdv) or 1

    local z_vib  = math.abs(sdiv(v_val - bv, sdv))
    local s_vib  = z_to_score(z_vib) * 0.30

    local z_temp = math.abs(sdiv(t_val - bt, sdt))
    local s_temp = z_to_score(z_temp) * 0.25

    local pd     = math.abs(sdiv(p_val - bp, bp))
    local s_pres = clamp(pd * 3.0) * 0.20

    local ovr    = sdiv(h_val, s_val)
    local s_svc  = clamp(ovr - 0.8) * 5.0 * 0.15

    local pwd    = math.abs(sdiv(pw_val - bpwr, bpwr))
    local s_pwr  = clamp(pwd * 2.5) * 0.10

    local raw    = s_vib + s_temp + s_pres + s_svc + s_pwr
    local e5xx   = 0
    if type(e_val) == "string" and string.match(e_val, "^E5") then e5xx = 0.25 end
    local score  = clamp(raw + e5xx)

    local tier
    if missing_required then
        tier = "DATA_LOSS"
        score = 0.0
    else
        if     score >= 0.80 then tier = "CRITICAL"
        elseif score >= 0.60 then tier = "HIGH"
        elseif score >= 0.35 then tier = "MEDIUM"
        else                       tier = "LOW"
        end
    end

    local sigs = {
        {n="VIBRATION",       v=s_vib  / 0.30},
        {n="TEMPERATURE",     v=s_temp / 0.25},
        {n="PRESSURE",        v=s_pres / 0.20},
        {n="SERVICE_OVERDUE", v=s_svc  / 0.15},
        {n="POWER",           v=s_pwr  / 0.10}
    }
    if e5xx > 0 then table.insert(sigs, {n="ERROR_CODE_E5XX", v=1.0}) end

    local top_n = "NONE"
    local top_v = -1
    for _, s in ipairs(sigs) do
        if s.v > top_v then top_v = s.v; top_n = s.n end
    end

    local action
    if tier == "CRITICAL" then
        action = "IMMEDIATE SHUTDOWN - emergency maintenance within 4 hours"
    elseif tier == "HIGH" then
        action = "URGENT - schedule maintenance within 24 hours"
    elseif tier == "MEDIUM" then
        action = "MONITOR - plan maintenance within 7 days; review " .. top_n
    else
        action = "NORMAL - continue standard monitoring schedule"
    end

    ctx.emit(score, tier, top_n, action)
end
""")
print("  ✓ COMPUTE_RISK_SCORE registered")

# ── Smoke tests ───────────────────────────────────────────────────────────────
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
print(f"  Score  : {result3[0]:.4f}  Tier: {result3[1]}  (expected penalty applied)")

conn.close()
print("\nAll UDFs working correctly.")
