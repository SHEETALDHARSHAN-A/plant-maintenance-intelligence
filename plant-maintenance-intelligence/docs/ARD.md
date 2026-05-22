# Architecture Decision Record (ARD)

## 1. High-Level Architecture
My solution is designed to scale progressively. Every architectural decision I make in the current prototype is explicitly chosen to be compatible with production scaling and future ML implementations without requiring a schema rewrite.

### Component Map
I architected the system across straightforward layers:
- **Ingestion & Storage**: Currently, I load a Python CSV bulk file directly into two simple Exasol CE tables. For production scaling, I can transition this to a high-frequency Kafka stream (`IMPORT FROM KAFKA`) while retaining the exact same Exasol tables—just properly partitioned.
- **Feature Engineering & Scoring**: Right now, I handle feature windows via a SQL view and score the data using a fast, rule-based Lua UDF. As the data grows, I can swap to incremental materialized scoring via Airflow DAGs, and eventually insert Python UDFs running Isolation Forests for ML scoring. 
- **Serving**: The current Streamlit application queries the live view directly. In a production state, I'd connect an alerting dashboard like Grafana directly to a pre-scored materialized table to save compute capability, utilizing Redis or similar for cache protection.

## 2. Data Model
My schema is intentionally forward-compatible. Because I isolated the complexity, the exact same tables support my current prototype just as well as they will support my future implementations.

### 2.1 MACHINE_TELEMETRY (Fact Table)
| Column | Type | Description | Notes |
|---|---|---|---|
| machine_id | VARCHAR(20) | Unique machine identifier | FK to MACHINE_REGISTRY |
| reading_ts | TIMESTAMP | UTC sensor reading time | Partition key in production |
| temperature_c | DECIMAL(8,2) | Operating temp in Celsius | |
| vibration_mm_s | DECIMAL(8,2) | Vibration RMS in mm/s | Highest-weight risk signal |
| pressure_bar | DECIMAL(8,2) | Internal pressure in bar | |
| runtime_hours | DECIMAL(10,2) | Cumulative hours | Drives overdue-service flag |
| power_kw | DECIMAL(8,2) | Power draw in kilowatts | |
| operating_mode | VARCHAR(20) | IDLE / NORMAL / HIGH_LOAD | Context for threshold |
| error_code | VARCHAR(10) | NULL or E1xx–E5xx code | E5xx = +0.25 risk premium (final score clipped to 1.0) |


### 2.2 MACHINE_REGISTRY (Dimension Table v1.1)
| Column | Type | Description | Notes |
|---|---|---|---|
| machine_id | VARCHAR(20) | Primary key | |
| machine_name | VARCHAR(100)| Human-readable name | |
| plant_id | VARCHAR(20) | PLANT_A / PLANT_B / PLANT_C | |
| machine_type | VARCHAR(50) | CNC_MILL / PRESS / etc | |
| baseline_temp_c | DECIMAL(8,2) | Normal operating temperature | |
| baseline_stddev_temp | DECIMAL(8,4) | Computed offline variance |  |
| baseline_vibration | DECIMAL(8,2) | Normal vibration baseline | |
| baseline_stddev_vibration | DECIMAL(8,4) | Computed offline variance |  |
| max_pressure | DECIMAL(8,2) | Critical pressure threshold | |
| service_interval_hours | DECIMAL(10,2)| Scheduled maintenance | |

### 2.3 SCORED_TELEMETRY_RESULTS (Materialized)
| Column | Type | Description |
|---|---|---|
| machine_id | VARCHAR(20) | FK to MACHINE_REGISTRY |
| window_end_ts | TIMESTAMP | End of the scoring window |
| risk_score | DECIMAL(5,4) | Computed [0.000 – 1.000] |
| risk_tier | VARCHAR(10) | LOW/MEDIUM/HIGH/CRITICAL |
| top_signal | VARCHAR(100) | Primary cause text |
| recommended_action | VARCHAR(200) | Ops-facing recommendation |
| scored_at | TIMESTAMP | Computation timestamp |
| model_version | VARCHAR(20) | UDF tag for audit trail |

## 3. SQL — Corrected Feature Engineering
Two major SQL design corrections secure the current architecture features:
1. fixed the SQL logic from `ROWS BETWEEN 23 PRECEDING` to `RANGE BETWEEN INTERVAL '24' HOUR PRECEDING`. This appropriately handles missing time gaps in sensor data...
2. **Zero Data Leakage for Z-Scores**: I use static `baseline_stddev_*` columns from the registry table for z-score math instead of calculating live `STDDEV()` windows that would accidentally peek into the future across partition evaluations.