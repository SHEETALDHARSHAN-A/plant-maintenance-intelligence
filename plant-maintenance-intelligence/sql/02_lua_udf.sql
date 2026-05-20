-- =============================================================
-- Phase 1: COMPUTE_RISK_SCORE — Lua EMITS UDF
-- Weights: vibration 30%, temp 25%, pressure 20%,
--          service overdue 15%, power 10%, E5xx +0.25 premium
-- Returns: risk_score, risk_tier, top_signal, action
-- =============================================================

OPEN SCHEMA PLANT_MAINTENANCE;

CREATE OR REPLACE LUA SCALAR SCRIPT COMPUTE_RISK_SCORE(
    p_machine_id        VARCHAR(20),
    p_temperature_c     DOUBLE,
    p_vibration_mm_s    DOUBLE,
    p_pressure_bar      DOUBLE,
    p_runtime_hours     DOUBLE,
    p_power_kw          DOUBLE,
    p_error_code        VARCHAR(10),
    p_baseline_temp     DOUBLE,
    p_baseline_vibration DOUBLE,
    p_baseline_pressure DOUBLE,
    p_baseline_power    DOUBLE,
    p_stddev_temp       DOUBLE,
    p_stddev_vibration  DOUBLE,
    p_hours_since_service DOUBLE,
    p_service_interval  DOUBLE
)
EMITS (risk_score DOUBLE, risk_tier VARCHAR(10), top_signal VARCHAR(50), recommended_action VARCHAR(200)) AS

-- -------------------------------------------------------
-- Helper: clamp value between 0 and 1
-- -------------------------------------------------------
local function clamp(v)
    if v < 0 then return 0 end
    if v > 1 then return 1 end
    return v
end

-- -------------------------------------------------------
-- Helper: sigmoid-like normaliser for Z-scores
-- -------------------------------------------------------
local function z_to_score(z_abs)
    if z_abs == nil then return 0 end
    if z_abs < 1.0 then return z_abs * 0.15 end
    if z_abs < 2.0 then return 0.15 + (z_abs - 1.0) * 0.30 end
    if z_abs < 3.0 then return 0.45 + (z_abs - 2.0) * 0.35 end
    return clamp(0.80 + (z_abs - 3.0) * 0.10)
end

-- -------------------------------------------------------
-- Helper: safe division
-- -------------------------------------------------------
local function safe_div(a, b)
    if b == nil or b == 0 then return 0 end
    return a / b
end

local function is_null(v)
    return v == nil or type(v) == "userdata"
end

-- -------------------------------------------------------
-- Main execution
-- -------------------------------------------------------
function run(ctx)
    -- Null-guard all inputs and apply penalty if sensor data is missing
    local missing_penalty = 0
    if is_null(ctx.p_temperature_c) or is_null(ctx.p_vibration_mm_s) or is_null(ctx.p_pressure_bar) or is_null(ctx.p_power_kw) then
        missing_penalty = 0.5
    end

    local temp      = (not is_null(ctx.p_temperature_c) and ctx.p_temperature_c) or (not is_null(ctx.p_baseline_temp) and ctx.p_baseline_temp) or 0
    local vib       = (not is_null(ctx.p_vibration_mm_s) and ctx.p_vibration_mm_s) or (not is_null(ctx.p_baseline_vibration) and ctx.p_baseline_vibration) or 0
    local pres      = (not is_null(ctx.p_pressure_bar) and ctx.p_pressure_bar) or (not is_null(ctx.p_baseline_pressure) and ctx.p_baseline_pressure) or 0
    local pwr       = (not is_null(ctx.p_power_kw) and ctx.p_power_kw) or (not is_null(ctx.p_baseline_power) and ctx.p_baseline_power) or 0
    local err       = (not is_null(ctx.p_error_code) and ctx.p_error_code) or ""
    local hrs_svc   = (not is_null(ctx.p_hours_since_service) and ctx.p_hours_since_service) or 0
    local svc_int   = (not is_null(ctx.p_service_interval) and ctx.p_service_interval) or 2000
    
    local bt = (not is_null(ctx.p_baseline_temp) and ctx.p_baseline_temp) or 0
    local bv = (not is_null(ctx.p_baseline_vibration) and ctx.p_baseline_vibration) or 0
    local bp = (not is_null(ctx.p_baseline_pressure) and ctx.p_baseline_pressure) or 0
    local bpwr = (not is_null(ctx.p_baseline_power) and ctx.p_baseline_power) or 0
    local sdt = (not is_null(ctx.p_stddev_temp) and ctx.p_stddev_temp) or 1
    local sdv = (not is_null(ctx.p_stddev_vibration) and ctx.p_stddev_vibration) or 1

    -- ---- Component 1: Vibration (weight 0.30) ----
    local z_vib = math.abs(safe_div(vib - bv, sdv))
    local s_vib = z_to_score(z_vib) * 0.30

    -- ---- Component 2: Temperature (weight 0.25) ----
    local z_temp = math.abs(safe_div(temp - bt, sdt))
    local s_temp = z_to_score(z_temp) * 0.25

    -- ---- Component 3: Pressure (weight 0.20) ----
    local pres_dev = math.abs(safe_div(pres - bp, bp))
    local s_pres = clamp(pres_dev * 3.0) * 0.20

    -- ---- Component 4: Service overdue (weight 0.15) ----
    local overdue_ratio = safe_div(hrs_svc, svc_int)
    local s_svc = clamp(overdue_ratio - 0.8) * 5.0 * 0.15

    -- ---- Component 5: Power anomaly (weight 0.10) ----
    local pwr_dev = math.abs(safe_div(pwr - bpwr, bpwr))
    local s_pwr = clamp(pwr_dev * 2.5) * 0.10

    -- ---- Raw score (0–1) ----
    local raw = s_vib + s_temp + s_pres + s_svc + s_pwr

    -- ---- E5xx error premium (+0.25, capped at 1.0) ----
    local e5xx_premium = 0
    if type(err) == "string" and string.match(err, "^E5") then
        e5xx_premium = 0.25
    end

    local final_score = clamp(raw + e5xx_premium + missing_penalty)

    -- ---- Risk tier ----
    local tier
    if     final_score >= 0.80 then tier = "CRITICAL"
    elseif final_score >= 0.60 then tier = "HIGH"
    elseif final_score >= 0.35 then tier = "MEDIUM"
    else                            tier = "LOW"
    end

    -- ---- Top signal (which component contributed most) ----
    local signals = {
        {name="VIBRATION",      val=s_vib  / 0.30},
        {name="TEMPERATURE",    val=s_temp / 0.25},
        {name="PRESSURE",       val=s_pres / 0.20},
        {name="SERVICE_OVERDUE",val=s_svc  / 0.15},
        {name="POWER",          val=s_pwr  / 0.10}
    }
    if e5xx_premium > 0 then
        table.insert(signals, {name="ERROR_CODE_E5XX", val=1.0})
    end
    if missing_penalty > 0 then
        table.insert(signals, {name="MISSING_SENSOR_DATA", val=1.0})
    end

    local top_signal = "NONE"
    local top_val    = -1
    for _, sig in ipairs(signals) do
        if sig.val > top_val then
            top_val    = sig.val
            top_signal = sig.name
        end
    end

    -- ---- Recommended action ----
    local action
    if tier == "CRITICAL" then
        action = "IMMEDIATE SHUTDOWN — schedule emergency maintenance within 4 hours"
    elseif tier == "HIGH" then
        action = "URGENT — schedule maintenance within 24 hours; increase monitoring frequency"
    elseif tier == "MEDIUM" then
        action = "MONITOR — plan maintenance within 7 days; review " .. top_signal .. " readings"
    else
        action = "NORMAL — continue standard monitoring schedule"
    end

    ctx.emit(final_score, tier, top_signal, action)
end
/
