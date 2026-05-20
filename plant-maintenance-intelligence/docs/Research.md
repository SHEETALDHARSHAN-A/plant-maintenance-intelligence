# Research & Technology Decisions

## 1. Core Analytics Database
When evaluating where to process our machine telemetry and risk models, the decision was straightforward: work with what we already have. Since our operations already rely on Exasol as our primary database, moving massive volumes of sensitive telemetry data to an external service or a different platform introduces unnecessary privacy risks, data transfer costs, and pipeline complexity.

By bringing our algorithms directly to the data rather than extracting the data out, we ensure that our risk intelligence is secure, immediate, and keeps our architecture simple without requiring a major platform switch.

## 2. UDF Language for Risk Scoring
When deciding how to write our risk scoring logic directly inside the database, we chose to start with Lua. Lua is extremely lightweight, requires zero setup or external libraries, and runs incredibly fast for the straightforward math we need right now. While Python is more powerful for heavy machine learning and will certainly be our choice as our intelligence needs grow, using Lua today allows us to prove our architecture works seamlessly without introducing heavy startup times or complex dependencies. Java and pure SQL were considered but either felt too clunky to write or too restrictive for future flexibility.

## 3. Risk Scoring Methodology
For calculating risk, we currently use a straightforward rule-based approach driven by domain knowledge—checking thresholds and calculating drift from baselines. The biggest benefit of this approach is explainability. When a machine is flagged as critical, our floor operators understand exactly *why* (e.g., "temperature is 20% over baseline"). While advanced machine learning methods like Isolation Forests or deep learning offer superior anomaly detection, they often act as "black boxes" and require weeks of data preparation and labeling. By starting with clear, explainable rules, we build trust with our engineering teams while laying the groundwork to introduce more complex ML models down the line.

Key scoring specifics used in the prototype:
- Weights: Vibration 30%, Temperature 25%, Pressure 20%, Service Overdue 15%, Power Anomaly 10%.
- Measurement: `Vibration` and `Temperature` are computed from Z-scores versus per-machine baselines; `Pressure` and `Power Anomaly` are computed as percentage deviations from baseline.
- Service overdue ramps starting at 80% of service interval and increases smoothly as the interval end approaches.
- Error codes (E5xx) apply a flat `+0.25` premium to the computed score; the final score is clipped to `[0.0, 1.0]` so no reading can exceed `1.0`.

## 4. Mock Data Generation
| Approach | Realism | Reproducibility | Build Time | Verdict |
|---|---|---|---|---|
| **Python + Gaussian Noise** | Configurable degradation | Seeded & reproducible | ~30 min | **SELECTED** |
| Faker / Mimesis | Meta good, realism bad | Fast | Minutes | Rejected |
| Hand-crafted CSV | static | Exact | Slow to extend| Rejected |
| NASA CMAPSS dataset | Physical realism | Published dataset | Days prep | Rejected (Demo only) |
To simulate the telemetry from our manufacturing floor, we opted to generate our own datasets using custom script logic rather than downloading generic samples or hand-typing data. By injecting calculated random variations into baseline patterns, we're able to closely mirror the actual physical degradation of our specific machines. This approach means our tests act realistically, we can easily tweak parameters to test new failure modes, and the entire simulation can be quickly generated from scratch in minutes without wrestling with massive, irrelevant external datasets.oduction)** |
| Tableau | JDBC | Limited | Enterprise | Customer-choice |
To get this risk intelligence in front of people as quickly as possible, we chose Streamlit to build our dashboard prototype. It connects effortlessly to our database and lets us build operational views using plain Python in a matter of hours. While enterprise BI tools or specialized alerting systems like Grafana will likely replace this for our permanent, production-scale control rooms due to their robust alerting and access control features, Streamlit achieves our immediate goal: proving the intelligence works and getting feedback from operators with minimum overhead.chitecture pattern for future ML | Migrate arithmetic to SQL CASE |
| **Rule-based vs ML** | Lower accuracy on complex modes | Retains explainability without historical labels | Deploy Isolation Forest/XGBoost once labels exist |
| **Hourly Granularity** | Misses transient rapid spikes | Limits row count for safe memory footprint | Kafka sub-minute streaming ingest for production |
Every system requires balancing trade-offs, and our current approach is no different. 

- **Explainability over AI Complexity**: We accepted that our current rule-based scoring might miss highly complex failure patterns that only AI could spot. We made this trade because it guarantees our results are easy to explain right now, which is critical for operator adoption. We will deploy proper ML models once we have accurate historical logs of actual machine failures.
- **Hourly vs. Real-Time Tracking**: We are presently tracking metrics on an hourly basis rather than capturing a firehose of sub-minute readings. While this means we might miss extremely short-lived spikes, it significantly reduces the immediate stress on our storage and memory while giving us a perfectly fine baseline. We can tighten this as we move toward full streaming in production. 
- **Prioritizing the Architecture over Ultimate Speed**: We are using functions that might slightly break optimal database processing speeds, but we made this choice intentionally. Proving that we can successfully embed logic into our database workflow using our chosen languages is more valuable right now than fighting for optimal processing performance. As our data grows, these processes can be easily migrated to hardened, optimized SQL.