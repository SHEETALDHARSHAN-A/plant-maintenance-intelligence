# Plant Maintenance Intelligence

A machine health monitoring prototype I built for the Exasol Solution Challenge. It watches factory equipment vitals — temperature, vibration, pressure, power — and scores each machine's risk from 0 to 1 using a Lua UDF running inside Exasol. The whole pipeline runs from mock data generation to a live Streamlit dashboard.

---

## What's in This Repo

```text
plant-maintenance/
├── dashboard/        Streamlit app (5 views: KPIs, overview, distribution, trends, drilldown)
├── data/             Generated CSVs (machine_registry.csv, machine_telemetry.csv)
├── deploy/           EC2 setup and dashboard startup scripts
├── docs/             Architecture, product, research, and implementation docs
├── logs/             Runtime logs from the dashboard and utilities
├── Results/          Screenshots, demo video, and scored output data
├── scripts/          Mock data generation and Exasol loading
├── sql/              Schema, Lua UDF, and scoring procedure
├── utils/            Shared logger
├── requirements.txt
└── README.md
```

---

## Docs

All design and decision documents are in `docs/`:

| File | What it covers |
|------|----------------|
| [PRD.md](docs/PRD.md) | Product requirements — the problem I'm solving, scoring weights, risk tiers, and business impact targets |
| [ARD.md](docs/ARD.md) | Architecture decisions — data model, schema design, SQL corrections, and how each component maps to a production equivalent |
| [Research.md](docs/Research.md) | Why I picked Exasol, Lua, rule-based scoring, and Streamlit over the alternatives |
| [current_implementation.md](docs/current_implementation.md) | Full walkthrough of what I built — the pipeline, scoring formula, dashboard, assumptions, and known limitations |
| [Future_Implementations.md](docs/Future_Implementations.md) | Production scaling plan — Kafka ingestion, Airflow DAGs, Isolation Forest ML, and Grafana migration |

---

## Results

### Scored Output

The `Results/Result-datas/Overview_data.csv` file has the actual scored output from a full run. Here's what it looks like:

| Machine | Plant | Type | Risk Tier | Score | Top Signal | Last Reading |
|---------|-------|------|-----------|-------|------------|--------------|
| MCH_B03 | PLANT_B | TURBINE | CRITICAL | 0.8341 | VIBRATION | 2026-05-15 |
| MCH_A01 | PLANT_A | COMPRESSOR | CRITICAL | 0.8334 | VIBRATION | 2026-05-19 |
| MCH_B04 | PLANT_B | CONVEYOR | HIGH | 0.7738 | ERROR_CODE_E5XX | 2026-05-19 |
| MCH_A02 | PLANT_A | PUMP | HIGH | 0.6911 | ERROR_CODE_E5XX | 2026-05-17 |
| MCH_C06 | PLANT_C | TURBINE | HIGH | 0.6471 | ERROR_CODE_E5XX | 2026-05-14 |
| MCH_C05 | PLANT_C | MIXER | MEDIUM | 0.5351 | VIBRATION | 2026-05-19 |
| MCH_B05 | PLANT_B | COMPRESSOR | MEDIUM | 0.5189 | VIBRATION | 2026-05-18 |
| MCH_A03 | PLANT_A | PUMP | MEDIUM | 0.5110 | VIBRATION | 2026-05-19 |
| MCH_C07 | PLANT_C | CONVEYOR | LOW | 0.3087 | VIBRATION | 2026-05-18 |
| MCH_C08 | PLANT_C | MIXER | LOW | 0.2929 | VIBRATION | 2026-05-06 |

2 CRITICAL, 3 HIGH, 3 MEDIUM, 2 LOW — exactly the distribution I engineered the mock data to produce.

### Screenshots

| View | Screenshot |
|------|-----------|
| Dashboard overview (page 1) | `Results/screen-shots/Dashboard-page-1.png` |
| Risk distribution chart | `Results/screen-shots/Dashboard-DIstribution-chart.png` |
| All-machine trend graph | `Results/screen-shots/all-record-trends-graph.png` |
| Per-machine score + error viewer | `Results/screen-shots/Each-machine-score-and-errorViewer.png` |
| Individual machine drilldown | `Results/screen-shots/Each-machine-view.png` |
| Filters and controls | `Results/screen-shots/Filters-controls.png` |

### Demo Video

Full walkthrough of the dashboard: `Results/Result-video.mp4`

---

## How to Run It

### Prerequisites

- Python 3.9+
- Exasol Community Edition running (locally or on EC2)
- Environment variables set — copy `.env.example` and fill in your Exasol connection details

### Local / EC2 Quick Start

```bash
# 1. Generate mock data
python3 scripts/generate_mock_data.py

# 2. Load into Exasol and run scoring
python3 scripts/load_to_exasol.py

# 3. Start the dashboard
streamlit run dashboard/app.py
```

### AWS EC2 Deployment

If Exasol is already installed on the instance, use the simplified script from Windows:

```powershell
cd d:\Machinary-problem\plant-maintenance
powershell -ExecutionPolicy Bypass -File .\deploy\deploy_simple.ps1
```

For a full setup (fresh EC2), use `deploy.ps1` — but that one needs elevated PowerShell privileges for PEM handling.

### SSH Tunnel (to open the dashboard in your browser)

Keep this terminal open while you use the dashboard:

```powershell
ssh -i "d:\Machinary-problem\sheetal-server.pem" -L 8501:localhost:8501 admin@ec2-18-212-151-119.compute-1.amazonaws.com
```

Then open `http://localhost:8501`.

### EC2 Manual Commands

```bash
cd ~/plant-maintenance
bash deploy/install_exasol.sh
python3 scripts/load_to_exasol.py
bash deploy/start_dashboard.sh
```

---

## AWS Security Group

Before running on EC2, make sure these inbound rules are open from your IP:

| Type | Port | Purpose |
|------|------|---------|
| SSH | 22 | Remote access |
| Custom TCP | 8563 | Exasol |
| Custom TCP | 8501 | Streamlit dashboard |

---

## Logs

Logs go through `utils/logger.py` — structured console output and rotating file logs under `logs/`.

```bash
tail -f /tmp/streamlit_dashboard.log
tail -f ~/plant-maintenance/logs/*.log
```

---

## Notes

- Dashboard reads Exasol connection settings from environment variables.
- Filtering by plant or risk tier runs in SQL, not Python — stays fast regardless of data volume.
- If deployment fails, check the EC2 security group first, then verify the SSH key path and remote username.
