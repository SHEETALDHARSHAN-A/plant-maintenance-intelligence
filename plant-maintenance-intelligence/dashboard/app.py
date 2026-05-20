"""
app.py — Current Prototype Streamlit Dashboard
Plant Maintenance Intelligence — Risk Monitoring
"""

import os
from datetime import datetime
import sys
from pathlib import Path

import streamlit as st

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logger

# Load .env file if present — env vars always take precedence over .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on env vars set by the shell or deploy script

logger = setup_logger(__name__)
logger.info("=" * 80)
logger.info("Plant Maintenance Intelligence Dashboard started")

try:
    import pyexasol
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    logger.info("All dependencies imported successfully")
except ImportError as e:
    logger.error(f"Missing dependency: {e}")
    st.error(f"Missing dependency: {e}. Run: pip install pyexasol pandas plotly")
    st.stop()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Plant Maintenance Intelligence",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

TIER_COLORS = {
    "CRITICAL": "#D32F2F",
    "HIGH":     "#F57C00",
    "MEDIUM":   "#FBC02D",
    "LOW":      "#388E3C",
}
TIER_EMOJI = {
    "CRITICAL": "",
    "HIGH":     "",
    "MEDIUM":   "",
    "LOW":      "",
}


def inject_styles():
    """Apply a presentation-friendly visual theme."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

        :root {
            --bg-0: #f4f6f8;
            --bg-1: #ffffff;
            --ink-0: #0f1b2a;
            --ink-1: #425466;
            --line: #dde3ea;
            --accent: #1f6f8b;
            --accent-2: #eb6e44;
            --critical: #d32f2f;
            --high: #f57c00;
            --medium: #fbc02d;
            --low: #388e3c;
        }

        .stApp {
            background:
                radial-gradient(1200px 350px at 80% -20%, #d7e9f1 10%, transparent 60%),
                radial-gradient(900px 300px at -10% 0%, #f8dfd6 10%, transparent 55%),
                var(--bg-0);
        }

        h1, h2, h3, h4, .stMarkdown, .stCaption {
            font-family: 'IBM Plex Sans', sans-serif !important;
            color: var(--ink-0);
        }

        .hero {
            border: 1px solid var(--line);
            background: linear-gradient(135deg, #ffffff 0%, #eef6fa 100%);
            border-radius: 16px;
            padding: 18px 22px;
            margin-bottom: 10px;
            box-shadow: 0 8px 22px rgba(15, 27, 42, 0.05);
        }

        .hero-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.55rem;
            font-weight: 700;
            margin: 0;
            letter-spacing: 0.2px;
        }

        .hero-sub {
            margin: 6px 0 0 0;
            color: var(--ink-1);
            font-size: 0.95rem;
        }

        .kpi-card {
            background: var(--bg-1);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 12px 14px;
            min-height: 96px;
            box-shadow: 0 6px 16px rgba(15, 27, 42, 0.05);
        }

        .kpi-title {
            font-size: 0.82rem;
            font-weight: 600;
            color: var(--ink-1);
            margin-bottom: 6px;
        }

        .kpi-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.6rem;
            line-height: 1.1;
            font-weight: 700;
            margin: 0;
        }

        .section-tag {
            display: inline-block;
            font-size: 0.75rem;
            color: #264653;
            background: #dcecf2;
            border-radius: 999px;
            padding: 3px 9px;
            margin-bottom: 6px;
            font-weight: 600;
            letter-spacing: 0.2px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_card(title: str, value: str, color: str):
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-title">{title}</div>
            <p class="kpi-value" style="color:{color}">{value}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── Connection config (env vars override defaults) ─────────────────────────────
EXA_HOST = os.environ.get("EXA_HOST",     "localhost")
EXA_PORT = int(os.environ.get("EXA_PORT", "8563"))
EXA_USER = os.environ.get("EXA_USER",     "sys")
EXA_PASS = os.environ.get("EXA_PASSWORD", "exasol")


def new_conn():
    """Open a fresh Exasol connection — never share across threads."""
    logger.debug(f"Attempting connection to Exasol at {EXA_HOST}:{EXA_PORT}")
    try:
        conn = pyexasol.connect(
            dsn=f"{EXA_HOST}:{EXA_PORT}",
            user=EXA_USER,
            password=EXA_PASS,
            websocket_sslopt={"cert_reqs": 0},
            query_timeout=60,
        )
        logger.info(f"Exasol connection established: {EXA_HOST}:{EXA_PORT}")
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to Exasol at {EXA_HOST}:{EXA_PORT}: {e}", exc_info=True)
        raise


def query_df(sql: str, params: list = None) -> pd.DataFrame:
    """Run SQL with optional params, return DataFrame with uppercase column names."""
    logger.debug(f"Executing query: {sql[:100]}..." if len(sql) > 100 else f"Executing query: {sql}")
    try:
        conn = new_conn()
        try:
            if hasattr(conn, "export_to_pandas"):
                df = conn.export_to_pandas(sql, params=params) if params else conn.export_to_pandas(sql)
                if df is None or df.empty:
                    logger.debug("Query returned empty result")
                    return pd.DataFrame()
                df.columns = [str(c).upper() for c in df.columns]
                logger.info(f"Query executed successfully, returned {len(df)} rows with columns: {list(df.columns)}")
                return df

            if params:
                stmt = conn.execute(sql, params)
            else:
                stmt = conn.execute(sql)
            rows = stmt.fetchall()
            if not rows:
                logger.debug("Query returned empty result")
                return pd.DataFrame()
            raw_cols = stmt.columns()
            cols = []
            for c in raw_cols:
                if isinstance(c, (tuple, list)) and len(c) > 0:
                    cols.append(str(c[0]).upper())
                else:
                    cols.append(str(c).upper())
            logger.info(f"Query executed successfully, returned {len(rows)} rows with columns: {cols}")
            return pd.DataFrame(rows, columns=cols)
        finally:
            conn.close()
            logger.debug("Connection closed")
    except Exception as exc:
        logger.error(f"Query execution failed: {exc}", exc_info=True)
        st.error(f"Query failed: {exc}")
        return pd.DataFrame()


def query_val(sql: str, params: list = None):
    """Run SQL, return single scalar value."""
    logger.debug(f"Executing scalar query: {sql[:100]}..." if len(sql) > 100 else f"Executing scalar query: {sql}")
    try:
        conn = new_conn()
        try:
            result = conn.execute(sql, params).fetchval() if params else conn.execute(sql).fetchval()
            logger.info(f"Scalar query executed successfully, returned: {result}")
            return result
        finally:
            conn.close()
            logger.debug("Connection closed")
    except Exception as exc:
        logger.error(f"Scalar query execution failed: {exc}", exc_info=True)
        st.error(f"Query failed: {exc}")
        return None


inject_styles()


def tier_case_sql(score_expr: str = "risk_score") -> str:
    """Return SQL CASE expression to derive risk tier from numeric score."""
    return (
        f"CASE "
        f"WHEN {score_expr} >= 0.80 THEN 'CRITICAL' "
        f"WHEN {score_expr} >= 0.60 THEN 'HIGH' "
        f"WHEN {score_expr} >= 0.35 THEN 'MEDIUM' "
        f"ELSE 'LOW' END"
    )


# ── Sidebar ────────────────────────────────────────────────────────────────────
logger.info("Rendering sidebar controls")
with st.sidebar:
    st.title(" Controls")
    st.markdown(f"**Exasol** `{EXA_HOST}:{EXA_PORT}`")
    st.caption("Prototype control panel for live client walkthroughs")
    st.divider()

    logger.debug("Fetching available plants from database")
    plants_df = query_df("""
        SELECT DISTINCT plant_id AS PLANT_ID
        FROM PLANT_MAINTENANCE.MACHINE_REGISTRY
        WHERE plant_id IS NOT NULL
        ORDER BY plant_id
    """)
    plant_options = (
        plants_df["PLANT_ID"].dropna().astype(str).tolist()
        if not plants_df.empty and "PLANT_ID" in plants_df.columns
        else ["PLANT_A", "PLANT_B", "PLANT_C"]
    )
    logger.info(f"Available plants: {plant_options}")

    logger.debug("Fetching available risk tiers from database")
    tiers_df = query_df(f"""
        SELECT DISTINCT {tier_case_sql('risk_score')} AS RISK_TIER
        FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
        ORDER BY RISK_TIER
    """)
    tier_options = (
        tiers_df["RISK_TIER"].dropna().astype(str).tolist()
        if not tiers_df.empty and "RISK_TIER" in tiers_df.columns
        else ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    )
    logger.info(f"Available risk tiers: {tier_options}")

    plant_filter = st.multiselect(
        "Filter by Plant",
        options=plant_options,
        default=plant_options,
    )
    logger.info(f"Plant filter selected: {plant_filter}")
    
    tier_filter = st.multiselect(
        "Filter by Risk Tier",
        options=tier_options,
        default=tier_options,
    )
    logger.info(f"Risk tier filter selected: {tier_filter}")
    
    st.divider()
    if st.button("Refresh Now"):
        logger.info("Manual refresh triggered by user")
        st.rerun()
    st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")


# ── SQL helpers ────────────────────────────────────────────────────────────────
def plant_in():
    vals = [str(v).strip().upper() for v in plant_filter if str(v).strip()]
    return vals

def tier_in():
    vals = [str(v).strip().upper() for v in tier_filter if str(v).strip()]
    return vals


def build_in_clause(vals: list):
    """Return (placeholders, params) for an SQL IN clause using positional params.

    Example: vals=['A','B'] -> returns ('?,?', ['A','B'])
    If vals is empty, returns ("''", []) to produce an empty literal set.
    """
    if not vals:
        return "''", []
    placeholders = ','.join(['?'] * len(vals))
    return placeholders, vals


# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="hero">
        <p class="hero-title">Plant Maintenance Intelligence</p>
        <p class="hero-sub">Current Prototype • Exasol CE + Lua UDF Risk Scoring</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("Prototype Glossary", expanded=False):
    st.markdown(
        """
        - **CRITICALITY_CLASS**: Business importance of the asset (`CRITICAL` / `STANDARD`).
        - **RISK_TIER**: Computed current risk (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) from `RISK_SCORE`.
        - **RECOMMENDED_ACTION**: Suggested maintenance response generated by the scoring logic.
        """
    )

# ── KPI row ────────────────────────────────────────────────────────────────────
logger.info("Rendering KPI row - fetching risk summary data")
plant_ph, plant_params = build_in_clause(plant_in())
tier_ph, tier_params = build_in_clause(tier_in())
summary_sql = f"""
    SELECT {tier_case_sql('risk_score')} AS RISK_TIER,
           COUNT(DISTINCT machine_id) AS machine_count,
           ROUND(AVG(risk_score), 4)  AS avg_score
    FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
        WHERE UPPER(TRIM(plant_id))  IN ({plant_ph})
            AND {tier_case_sql('risk_score')} IN ({tier_ph})
    GROUP BY {tier_case_sql('risk_score')}
    ORDER BY MAX(risk_score) DESC
"""
summary_df = query_df(summary_sql, params=(plant_params + tier_params))

tier_counts = {}
if not summary_df.empty:
    for _, row in summary_df.iterrows():
        tier_counts[row["RISK_TIER"]] = int(row["MACHINE_COUNT"])
    logger.info(f"Risk summary: {tier_counts}")
else:
    logger.warning("No summary data returned for KPI row")

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    render_kpi_card("Critical", str(tier_counts.get("CRITICAL", 0)), TIER_COLORS["CRITICAL"])
with c2:
    render_kpi_card("High", str(tier_counts.get("HIGH", 0)), TIER_COLORS["HIGH"])
with c3:
    render_kpi_card("Medium", str(tier_counts.get("MEDIUM", 0)), TIER_COLORS["MEDIUM"])
with c4:
    render_kpi_card("Low", str(tier_counts.get("LOW", 0)), TIER_COLORS["LOW"])
with c5:
    render_kpi_card("Machines", str(sum(tier_counts.values())), "#1f6f8b")

st.divider()

# ── Row 1: Overview table + pie ────────────────────────────────────────────────
logger.info("Rendering Row 1: Overview table and pie chart")
col_l, col_r = st.columns([3, 2])

with col_l:
    st.markdown('<span class="section-tag">OVERVIEW</span>', unsafe_allow_html=True)
    st.subheader("Machine Risk Overview")
    logger.debug("Fetching machine risk overview data")
        overview_sql = f"""
        SELECT machine_id      AS MACHINE,
               plant_id        AS PLANT,
             machine_type    AS MACHINE_TYPE,
               location_zone   AS LOCATION_ZONE,
             criticality_class AS CRITICALITY_CLASS,
                             {tier_case_sql('risk_score')} AS RISK_TIER,
               ROUND(risk_score, 4) AS SCORE,
               top_signal      AS TOP_SIGNAL,
             recommended_action AS RECOMMENDED_ACTION,
               TO_CHAR(reading_ts, 'YYYY-MM-DD HH24:MI') AS LAST_READING
        FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
                                WHERE UPPER(TRIM(plant_id))  IN ({plant_ph})
                                                                                AND {tier_case_sql('risk_score')} IN ({tier_ph})
                ORDER BY risk_score DESC
        """
        overview_df = query_df(overview_sql, params=(plant_params + tier_params))
    if not overview_df.empty:
        logger.info(f"Overview table: {len(overview_df)} machines displayed")
        def style_tier(val):
            color = TIER_COLORS.get(val, "#888")
            return f"background-color:{color}22;color:{color};font-weight:bold"
        st.dataframe(
            overview_df.style.map(style_tier, subset=["RISK_TIER"]),
            use_container_width=True, height=380,
        )
    else:
        logger.warning("No overview data available for display")
        st.info("No data — check connection or filters.")

with col_r:
    st.markdown('<span class="section-tag">DISTRIBUTION</span>', unsafe_allow_html=True)
    st.subheader("Risk Distribution")
    if not summary_df.empty:
        logger.info("Rendering risk distribution pie chart")
        fig = px.pie(
            summary_df, names="RISK_TIER", values="MACHINE_COUNT",
            color="RISK_TIER", color_discrete_map=TIER_COLORS, hole=0.45,
        )
        fig.update_traces(textinfo="label+value", textfont_size=14)
        fig.update_layout(showlegend=True, margin=dict(t=20,b=20,l=20,r=20), height=380)
        st.plotly_chart(fig, use_container_width=True)
    else:
        logger.warning("No data available for risk distribution chart")
        st.info("No data for chart.")

st.divider()

# ── Actionable Top-N ─────────────────────────────────────────────────────────
logger.info("Rendering Top 5 actionable machines")
top5_sql = f"""
    SELECT machine_id AS MACHINE,
           plant_id   AS PLANT,
           machine_type AS MACHINE_TYPE,
           TO_CHAR(reading_ts,'YYYY-MM-DD HH24:MI') AS LAST_READING,
           ROUND(risk_score,4) AS SCORE,
           risk_tier AS RISK_TIER,
           reason,
           action,
           priority_rank
    FROM PLANT_MAINTENANCE.V_ACTIONABLE_RISK
    WHERE UPPER(TRIM(plant_id)) IN ({plant_ph})
      AND {tier_case_sql('risk_score')} IN ({tier_ph})
    ORDER BY priority_rank ASC
    LIMIT 5
"""
top5_df = query_df(top5_sql, params=(plant_params + tier_params))
if not top5_df.empty:
    st.markdown('<span class="section-tag">TOP PRIORITY</span>', unsafe_allow_html=True)
    st.subheader("Top 5 Machines — Actionable Summary")
    def style_priority(row):
        color = TIER_COLORS.get(row['RISK_TIER'], '#888')
        return [f'background-color:{color}22;color:{color};font-weight:bold' if col=='RISK_TIER' else '' for col in row.index]
    st.table(top5_df[['MACHINE','PLANT','SCORE','RISK_TIER','REASON','ACTION']].rename(columns={
        'MACHINE':'Machine','PLANT':'Plant','SCORE':'Score','RISK_TIER':'Tier','REASON':'Reason','ACTION':'Action'
    }))
else:
    st.info("No actionable top machines for selected filters.")

st.divider()

# ── Row 2: 24h trend ───────────────────────────────────────────────────────────
logger.info("Rendering Row 2: Risk score trend chart")
st.markdown('<span class="section-tag">TREND</span>', unsafe_allow_html=True)
st.subheader("Risk Score Trend (all scored readings)")

# Use all scored results ordered by time — not just last 24h (mock data is historical)
logger.debug("Fetching trend data for all machines")
trend_sql = f"""
    SELECT s.machine_id  AS MACHINE,
           r.plant_id    AS PLANT,
           s.reading_ts  AS READING_TS,
           s.risk_score  AS RISK_SCORE,
           {tier_case_sql('s.risk_score')} AS RISK_TIER,
           s.top_signal  AS TOP_SIGNAL
    FROM PLANT_MAINTENANCE.SCORED_TELEMETRY_RESULTS s
    JOIN PLANT_MAINTENANCE.MACHINE_REGISTRY r ON s.machine_id = r.machine_id
        WHERE s.reading_ts >= ADD_HOURS(CURRENT_TIMESTAMP, -24)
          AND UPPER(TRIM(r.plant_id))  IN ({plant_ph})
          AND {tier_case_sql('s.risk_score')} IN ({tier_ph})
    ORDER BY s.reading_ts ASC
"""
trend_df = query_df(trend_sql, params=(plant_params + tier_params))

if not trend_df.empty:
    logger.info(f"Trend chart: {len(trend_df)} data points for {trend_df['MACHINE'].nunique()} machines")
    fig2 = px.line(
        trend_df, x="READING_TS", y="RISK_SCORE", color="MACHINE",
        hover_data=["PLANT", "RISK_TIER", "TOP_SIGNAL"],
    )
    for label, y_val, color in [
        ("CRITICAL (0.80)", 0.80, "#D32F2F"),
        ("HIGH (0.60)",     0.60, "#F57C00"),
        ("MEDIUM (0.35)",   0.35, "#FBC02D"),
    ]:
        fig2.add_hline(y=y_val, line_dash="dash", line_color=color,
                       annotation_text=label, annotation_position="right")
    fig2.update_layout(yaxis=dict(range=[0, 1.05], title="Risk Score"),
                       height=420, legend_title="Machine")
    st.plotly_chart(fig2, use_container_width=True)
else:
    logger.warning("No trend data available for chart")
    st.info("No trend data.")

st.divider()

# ── Row 3: Machine drilldown ───────────────────────────────────────────────────
logger.info("Rendering Row 3: Machine drilldown section")
st.markdown('<span class="section-tag">DRILLDOWN</span>', unsafe_allow_html=True)
st.subheader("Machine Drilldown")

logger.debug("Fetching list of available machines")
machines_df = query_df(
    "SELECT machine_id FROM PLANT_MAINTENANCE.MACHINE_REGISTRY ORDER BY machine_id"
)
machine_list = machines_df["MACHINE_ID"].tolist() if not machines_df.empty else []
logger.info(f"Available machines for drilldown: {len(machine_list)} machines")

selected = st.selectbox("Select a machine", options=machine_list)

if selected:
    logger.info(f"Machine selected for drilldown: {selected}")
    d1, d2 = st.columns(2)

    with d1:
        st.markdown(f"**Last 72 sensor readings — `{selected}`**")
        logger.debug(f"Fetching sensor telemetry for machine {selected}")
        sensor_df = query_df(f"""
            SELECT TO_CHAR(reading_ts,'YYYY-MM-DD HH24:MI') AS READING_TIME,
                   temperature_c   AS TEMP_C,
                   vibration_mm_s  AS VIB_MM_S,
                   pressure_bar    AS PRES_BAR,
                   power_kw        AS POWER_KW,
                   operating_mode  AS OPERATING_MODE,
                   error_code      AS ERROR_CODE
            FROM PLANT_MAINTENANCE.MACHINE_TELEMETRY
            WHERE machine_id = '{selected}'
            ORDER BY reading_ts DESC
            LIMIT 72
        """)
        if not sensor_df.empty:
            logger.info(f"Sensor data retrieved: {len(sensor_df)} readings for {selected}")
            st.dataframe(sensor_df, use_container_width=True, height=300)
        else:
            logger.warning(f"No sensor data available for machine {selected}")
            st.info("No sensor data.")

    with d2:
        st.markdown(f"**Risk score history — `{selected}`**")
        logger.debug(f"Fetching risk score history for machine {selected}")
        hist_df = query_df(f"""
            SELECT reading_ts  AS READING_TS,
                   risk_score  AS RISK_SCORE,
                   {tier_case_sql('risk_score')} AS TIER,
                   top_signal  AS TOP_SIGNAL
            FROM PLANT_MAINTENANCE.SCORED_TELEMETRY_RESULTS
            WHERE machine_id = '{selected}'
            ORDER BY reading_ts ASC
        """)
        if not hist_df.empty:
            logger.info(f"Risk score history retrieved: {len(hist_df)} records for {selected}")
            fig3 = px.line(
                hist_df, x="READING_TS", y="RISK_SCORE", color="TIER",
                color_discrete_map=TIER_COLORS,
                title=f"Risk Score — {selected}",
            )
            fig3.update_layout(height=300, yaxis=dict(range=[0, 1.05]))
            st.plotly_chart(fig3, use_container_width=True)
        else:
            logger.warning(f"No risk score history available for machine {selected}")
            st.info("No score history.")

    # Latest recommendation box
    logger.debug(f"Fetching latest risk assessment for machine {selected}")
    latest_df = query_df(f"""
            SELECT {tier_case_sql('risk_score')} AS RISK_TIER,
             ROUND(risk_score,4) AS RISK_SCORE,
             top_signal AS TOP_SIGNAL,
             recommended_action AS RECOMMENDED_ACTION
        FROM PLANT_MAINTENANCE.V_LATEST_RISK_SUMMARY
        WHERE machine_id = '{selected}'
    """)
    if not latest_df.empty:
        row    = latest_df.iloc[0]
        tier   = str(row.get("RISK_TIER",   "UNKNOWN"))
        score  = row.get("RISK_SCORE",  0)
        signal = str(row.get("TOP_SIGNAL",  "N/A"))
        action = str(row.get("RECOMMENDED_ACTION", "N/A"))
        logger.info(f"Latest assessment for {selected}: Tier={tier}, Score={score}, Signal={signal}")
        color  = TIER_COLORS.get(tier, "#888")
        emoji  = TIER_EMOJI.get(tier, "")
        st.markdown(f"""
        <div style="border-left:6px solid {color};padding:12px 20px;
                    background:{color}11;border-radius:4px;margin-top:12px">
            <h4 style="color:{color};margin:0">{emoji} {tier} — Score: {score}</h4>
            <p style="margin:4px 0"><b>Top Signal:</b> {signal}</p>
            <p style="margin:4px 0"><b>Action:</b> {action}</p>
        </div>""", unsafe_allow_html=True)
    else:
        logger.warning(f"No latest assessment available for machine {selected}")

st.divider()

# ── Row 4: Error codes ─────────────────────────────────────────────────────────
st.markdown('<span class="section-tag">RELIABILITY SIGNALS</span>', unsafe_allow_html=True)
st.subheader("Error Code Analysis")
err_sql = f"""
    SELECT t.machine_id AS MACHINE,
        r.plant_id   AS PLANT,
        t.error_code AS ERROR_CODE,
        COUNT(*)     AS OCCURRENCES,
        TO_CHAR(MIN(t.reading_ts),'YYYY-MM-DD HH24:MI') AS FIRST_SEEN,
        TO_CHAR(MAX(t.reading_ts),'YYYY-MM-DD HH24:MI') AS LAST_SEEN
    FROM PLANT_MAINTENANCE.MACHINE_TELEMETRY t
    JOIN PLANT_MAINTENANCE.MACHINE_REGISTRY r ON t.machine_id = r.machine_id
    WHERE t.error_code IS NOT NULL
      AND TRIM(t.error_code) != ''
            AND UPPER(TRIM(r.plant_id)) IN ({plant_ph})
    GROUP BY t.machine_id, r.plant_id, t.error_code
    ORDER BY COUNT(*) DESC
    LIMIT 50
"""
err_df = query_df(err_sql, params=plant_params)
if not err_df.empty:
    e1, e2 = st.columns([2, 3])
    with e1:
        st.dataframe(err_df, use_container_width=True, height=280)
    with e2:
        fig4 = px.bar(err_df, x="MACHINE", y="OCCURRENCES", color="ERROR_CODE",
                      barmode="stack", title="Error Frequency by Machine")
        fig4.update_layout(height=280)
        st.plotly_chart(fig4, use_container_width=True)
else:
    st.success("No error codes recorded for selected plants.")

st.divider()
st.caption(
    ""
    "Scoring: vibration 30% | temp 25% | pressure 20% | service 15% | power 10% | E5xx +0.25"
)
