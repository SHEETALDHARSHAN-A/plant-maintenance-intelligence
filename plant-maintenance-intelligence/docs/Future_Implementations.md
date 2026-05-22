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
I strongly believe in implementing a rule-based logic first for the prototype. Rule-based scores are explainable and fully traceable—meaning engineers immediately trust the metrics... Building a machine learning can take time...

Once the application logs enough real failure signatures, I'll transition to unsupervised learning to adapt automatically.

### The Isolation Forest Strategy 
Instead of waiting weeks for explicit failure labels (which manufacturing plants often lack initially), an unsupervised **Isolation Forest** anomaly detection algorithm is the natural first ML addition. 

I have referred this Research paper that can help our future implementation: https://www.researchgate.net/figure/High-level-pipeline-architecture-The-pipeline-architecture-illustrates-the-architecture_fig1_340567136?hl=en-US