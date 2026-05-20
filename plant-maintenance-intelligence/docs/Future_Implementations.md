# Future Implementations

## Production Scaling
My current architecture (using live views and Streamlit direct queries) works flawlessly for 72K rows. However, when I scale this to 100M+ rows with high-frequency streaming, certain components will inevitably hit their limits. Doing a full-table window function over the `V_MACHINE_RISK` view on every query would eventually saturate the CPU. High-frequency IoT sensors often send double readings which corrupt Z-scores, and frequent single `INSERTs` could easily bottleneck the database commit log. 

### Solutions for Production
To safeguard against this without breaking my foundational schema, I plan to:
1. **Kafka Micro-Batch Ingestion**: Use `IMPORT FROM KAFKA` to parallelize data load and buffer single inserts into larger micro-batches.
2. **Incremental Materialized Scoring**: Extract the UDF from the live view entirely. Run an Airflow DAG every 5 minutes rendering scores into `SCORED_TELEMETRY_RESULTS`. Streamlit (or Grafana) then just reads from the pre-scored table.
3. **Data Deduplication**: Handle duplicate sensor pings natively at ingestion via a `MERGE` statement on `(machine_id, reading_ts)`.
4. **Grafana Transition**: Connect Grafana directly to the materialized table for enterprise RBAC and Slack alerting, replacing my Streamlit demo app.

## ML Enhancement Roadmap
Future implementations will replace my rule-based Lua UDF with trained machine learning models running as Python UDFs directly inside the Exasol database context.

### Why Rule-Based First, ML Second
I strongly believe in implementing a rule-based logic first for the prototype. Rule-based scores are 100% explainable and fully traceable—meaning engineers immediately trust the metrics without seeing it as a "black box." Building a machine learning pipeline takes weeks of data prep and historical labels, whereas the rule-based Lua UDF was built in hours. 

Once the application logs enough real failure signatures, I'll transition to unsupervised learning to adapt automatically.

### The Isolation Forest Strategy 
Instead of waiting weeks for explicit failure labels (which manufacturing plants often lack initially), an unsupervised **Isolation Forest** anomaly detection algorithm is the natural first ML addition. 

#### Example Python3 UDF
Exasol's Python script container natively supports pandas and pickle.

```python
CREATE OR REPLACE PYTHON3 SET SCRIPT SCORE_ISOLATION_FOREST(
  machine_id     VARCHAR(20),
  temp_zscore    DOUBLE,
  vib_zscore     DOUBLE
) EMITS (machine_id VARCHAR(20), anomaly_score DOUBLE, is_anomaly BOOLEAN) AS
import pandas as pd
import pickle

def run(ctx):
    # Load an offline-trained Isolation Forest model
    with open('/buckets/iforest_model_v2.pkl', 'rb') as f:
        model = pickle.load(f)
        
    df = ctx.get_dataframe(num_rows=10000)
    features = ['temp_zscore','vib_zscore']
    
    # Compute base decision scores and anomalies
    scores = model.decision_function(df[features])
    
    for i, row in df.iterrows():
        ctx.emit(row['machine_id'], float(scores[i]), scores[i] < 0)
/
```

Note on current scoring baseline: the prototype uses rule-based weights (Vibration 30%, Temperature 25%, Pressure 20%, Service Overdue 15%, Power 10%), with E5xx errors adding a flat +0.25 premium and final scores clipped to 1.0. Any ML model should be trained to reproduce or improve upon this baseline for backward compatibility.