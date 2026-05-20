"""
generate_mock_data.py
=====================
Generates Phase 1 mock data:
  - 10 machines × 3 plants × 720 hours (30 days) = 7,200 rows
  - Guaranteed risk spread: 2 Critical, 3 High, 3 Medium, 2 Low
  - Degradation injected from hour 0 so scores are visible immediately

Outputs:
  - data/machine_registry.csv
  - data/machine_telemetry.csv
"""

import csv
import random
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logger

# Setup logger
logger = setup_logger(__name__, Path(__file__).parent.parent / "logs")

random.seed(42)

START_TS = datetime(2026, 4, 20, 0, 0, 0)
HOURS    = 720   # 30 days

# ── Machine definitions ────────────────────────────────────────────────────────
# last_service_offset_h: hours BEFORE start_ts that last service happened
# A large value means the machine is already overdue when data begins
MACHINES = [

    # ══ CRITICAL (2) ══════════════════════════════════════════════════════════
    {
        "machine_id": "MCH_A01", "plant_id": "PLANT_A",
        "machine_type": "COMPRESSOR",
        "install_date": "2021-03-15", "location_zone": "ZONE_A",
        "criticality_class": "CRITICAL",
        "baseline_temp_c": 85.0,  "baseline_vibration": 2.5,
        "baseline_pressure_bar": 12.0, "baseline_power_kw": 150.0,
        "stddev_temp": 3.0, "stddev_vibration": 0.4,
        "service_interval_h": 2000,
        "last_service_offset_h": 1950,   # already 97.5% through interval → overdue
        "degradation": {
            "temp_slope":      0.050,    # calibrated: score=0.95 at h719 → CRITICAL
            "vib_slope":       0.0075,
            "vib_spike_prob":  0.08,
            "vib_spike_mag":   4.0,
            "error_prob":      0.10,
        }
    },
    {
        "machine_id": "MCH_B03", "plant_id": "PLANT_B",
        "machine_type": "TURBINE",
        "install_date": "2019-07-22", "location_zone": "ZONE_B",
        "criticality_class": "CRITICAL",
        "baseline_temp_c": 120.0, "baseline_vibration": 3.2,
        "baseline_pressure_bar": 18.0, "baseline_power_kw": 320.0,
        "stddev_temp": 4.0, "stddev_vibration": 0.5,
        "service_interval_h": 1500,
        "last_service_offset_h": 1480,   # 98.7% through interval
        "degradation": {
            "temp_slope":      0.050,    # calibrated: score=1.00 at h719 → CRITICAL
            "vib_slope":       0.0075,
            "vib_spike_prob":  0.15,
            "vib_spike_mag":   7.0,
            "error_prob":      0.12,
        }
    },

    # ══ HIGH (3) ══════════════════════════════════════════════════════════════
    {
        "machine_id": "MCH_A02", "plant_id": "PLANT_A",
        "machine_type": "PUMP",
        "install_date": "2020-11-10", "location_zone": "ZONE_A",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 65.0, "baseline_vibration": 1.8,
        "baseline_pressure_bar": 8.5, "baseline_power_kw": 75.0,
        "stddev_temp": 2.5, "stddev_vibration": 0.3,
        "service_interval_h": 1800,
        "last_service_offset_h": 1600,   # 88.9% through interval
        "degradation": {
            "temp_slope":      0.01000,  # calibrated: score=0.68 at h719 → HIGH
            "vib_slope":       0.00150,
            "vib_spike_prob":  0.05,
            "vib_spike_mag":   2.5,
            "error_prob":      0.02,
        }
    },
    {
        "machine_id": "MCH_B04", "plant_id": "PLANT_B",
        "machine_type": "CONVEYOR",
        "install_date": "2022-01-05", "location_zone": "ZONE_C",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 45.0, "baseline_vibration": 1.2,
        "baseline_pressure_bar": 4.0, "baseline_power_kw": 40.0,
        "stddev_temp": 2.0, "stddev_vibration": 0.25,
        "service_interval_h": 2500,
        "last_service_offset_h": 2300,   # 92% through interval
        "degradation": {
            "temp_slope":      0.01000,  # calibrated: score=0.72 at h719 → HIGH
            "vib_slope":       0.00150,
            "vib_spike_prob":  0.06,
            "vib_spike_mag":   2.0,
            "error_prob":      0.02,
        }
    },
    {
        "machine_id": "MCH_C05", "plant_id": "PLANT_C",
        "machine_type": "MIXER",
        "install_date": "2021-06-18", "location_zone": "ZONE_A",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 55.0, "baseline_vibration": 2.0,
        "baseline_pressure_bar": 6.0, "baseline_power_kw": 90.0,
        "stddev_temp": 2.8, "stddev_vibration": 0.35,
        "service_interval_h": 2000,
        "last_service_offset_h": 1750,   # 87.5% through interval
        "degradation": {
            "temp_slope":      0.01200,  # calibrated: score=0.74 at h719 → HIGH
            "vib_slope":       0.00180,
            "vib_spike_prob":  0.04,
            "vib_spike_mag":   1.8,
            "error_prob":      0.02,
        }
    },

    # ══ MEDIUM (3) ════════════════════════════════════════════════════════════
    {
        "machine_id": "MCH_A03", "plant_id": "PLANT_A",
        "machine_type": "PUMP",
        "install_date": "2022-04-20", "location_zone": "ZONE_B",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 70.0, "baseline_vibration": 2.1,
        "baseline_pressure_bar": 9.0, "baseline_power_kw": 85.0,
        "stddev_temp": 3.0, "stddev_vibration": 0.4,
        "service_interval_h": 2200,
        "last_service_offset_h": 1500,   # 68% through interval
        "degradation": {
            "temp_slope":      0.00938,  # calibrated: score=0.48 at h719 → MEDIUM
            "vib_slope":       0.00141,
            "vib_spike_prob":  0.02,
            "vib_spike_mag":   1.2,
            "error_prob":      0.01,
        }
    },
    {
        "machine_id": "MCH_B05", "plant_id": "PLANT_B",
        "machine_type": "COMPRESSOR",
        "install_date": "2020-09-14", "location_zone": "ZONE_B",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 90.0, "baseline_vibration": 2.8,
        "baseline_pressure_bar": 14.0, "baseline_power_kw": 180.0,
        "stddev_temp": 3.5, "stddev_vibration": 0.45,
        "service_interval_h": 1800,
        "last_service_offset_h": 1200,   # 66% through interval
        "degradation": {
            "temp_slope":      0.00938,  # calibrated: score=0.47 at h719 → MEDIUM
            "vib_slope":       0.00141,
            "vib_spike_prob":  0.02,
            "vib_spike_mag":   1.0,
            "error_prob":      0.01,
        }
    },
    {
        "machine_id": "MCH_C06", "plant_id": "PLANT_C",
        "machine_type": "TURBINE",
        "install_date": "2019-12-01", "location_zone": "ZONE_C",
        "criticality_class": "CRITICAL",
        "baseline_temp_c": 110.0, "baseline_vibration": 3.0,
        "baseline_pressure_bar": 16.0, "baseline_power_kw": 280.0,
        "stddev_temp": 4.0, "stddev_vibration": 0.5,
        "service_interval_h": 1600,
        "last_service_offset_h": 1100,   # 68% through interval
        "degradation": {
            "temp_slope":      0.00625,  # calibrated: score=0.38 at h719 → MEDIUM
            "vib_slope":       0.00094,
            "vib_spike_prob":  0.02,
            "vib_spike_mag":   1.1,
            "error_prob":      0.01,
        }
    },

    # ══ LOW (2) ═══════════════════════════════════════════════════════════════
    {
        "machine_id": "MCH_C07", "plant_id": "PLANT_C",
        "machine_type": "CONVEYOR",
        "install_date": "2023-02-28", "location_zone": "ZONE_A",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 40.0, "baseline_vibration": 1.0,
        "baseline_pressure_bar": 3.5, "baseline_power_kw": 30.0,
        "stddev_temp": 1.5, "stddev_vibration": 0.2,
        "service_interval_h": 3000,
        "last_service_offset_h": 300,    # 10% through interval — recently serviced
        "degradation": None
    },
    {
        "machine_id": "MCH_C08", "plant_id": "PLANT_C",
        "machine_type": "MIXER",
        "install_date": "2023-05-10", "location_zone": "ZONE_B",
        "criticality_class": "STANDARD",
        "baseline_temp_c": 50.0, "baseline_vibration": 1.5,
        "baseline_pressure_bar": 5.0, "baseline_power_kw": 60.0,
        "stddev_temp": 2.0, "stddev_vibration": 0.25,
        "service_interval_h": 2800,
        "last_service_offset_h": 200,    # 7% through interval — recently serviced
        "degradation": None
    },
]

OPERATING_MODES = ["RUNNING"] * 8 + ["IDLE", "STARTUP"]


def generate_reading(m: dict, hour_idx: int) -> dict:
    deg = m.get("degradation")
    ts  = START_TS + timedelta(hours=hour_idx)

    # Base noise around baseline
    temp = random.gauss(m["baseline_temp_c"],      m["stddev_temp"])
    vib  = random.gauss(m["baseline_vibration"],   m["stddev_vibration"])
    pres = random.gauss(m["baseline_pressure_bar"], m["baseline_pressure_bar"] * 0.02)
    pwr  = random.gauss(m["baseline_power_kw"],    m["baseline_power_kw"] * 0.03)

    runtime    = m["last_service_offset_h"] + hour_idx
    mode       = random.choice(OPERATING_MODES)
    error_code = ""

    if deg and mode != "MAINTENANCE":
        # Degradation active from hour 0 — full slope applied
        temp += deg["temp_slope"] * hour_idx
        vib  += deg["vib_slope"]  * hour_idx

        if random.random() < deg["vib_spike_prob"]:
            vib += random.gauss(deg["vib_spike_mag"], deg["vib_spike_mag"] * 0.2)

        if random.random() < deg["error_prob"]:
            error_code = f"E5{random.randint(10, 99)}"

    # Occasional maintenance window (0.5%)
    if random.random() < 0.005:
        mode = "MAINTENANCE"

    return {
        "machine_id":      m["machine_id"],
        "reading_ts":      ts.strftime("%Y-%m-%d %H:%M:%S"),
        "temperature_c":   max(10.0,  round(temp, 2)),
        "vibration_mm_s":  max(0.01,  round(abs(vib), 3)),
        "pressure_bar":    max(0.1,   round(pres, 2)),
        "runtime_hours":   round(runtime, 1),
        "power_kw":        max(1.0,   round(pwr, 2)),
        "operating_mode":  mode,
        "error_code":      error_code,
    }


def write_registry(machines, out_path):
    logger.info(f"Writing machine registry to {out_path}")
    fields = [
        "machine_id","plant_id","machine_type","install_date",
        "baseline_temp_c","baseline_vibration","baseline_pressure_bar",
        "baseline_power_kw","baseline_stddev_temp","baseline_stddev_vibration",
        "service_interval_hours","last_service_ts","location_zone","criticality_class",
    ]
    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            for m in machines:
                last_svc = START_TS - timedelta(hours=m["last_service_offset_h"])
                w.writerow({
                    "machine_id":               m["machine_id"],
                    "plant_id":                 m["plant_id"],
                    "machine_type":             m["machine_type"],
                    "install_date":             m["install_date"],
                    "baseline_temp_c":          m["baseline_temp_c"],
                    "baseline_vibration":       m["baseline_vibration"],
                    "baseline_pressure_bar":    m["baseline_pressure_bar"],
                    "baseline_power_kw":        m["baseline_power_kw"],
                    "baseline_stddev_temp":     m["stddev_temp"],
                    "baseline_stddev_vibration":m["stddev_vibration"],
                    "service_interval_hours":   m["service_interval_h"],
                    "last_service_ts":          last_svc.strftime("%Y-%m-%d %H:%M:%S"),
                    "location_zone":            m["location_zone"],
                    "criticality_class":        m["criticality_class"],
                })
        logger.info(f"Registry written successfully: {len(machines)} machines")
        print(f"  ✓ Registry: {out_path}  ({len(machines)} machines)")
    except Exception as e:
        logger.error(f"Failed to write registry to {out_path}: {e}", exc_info=True)
        raise


def write_telemetry(machines, out_path):
    logger.info(f"Writing telemetry data to {out_path}")
    fields = [
        "machine_id","reading_ts","temperature_c","vibration_mm_s",
        "pressure_bar","runtime_hours","power_kw","operating_mode","error_code",
    ]
    total = 0
    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            for m in machines:
                for h in range(HOURS):
                    w.writerow(generate_reading(m, h))
                    total += 1
        logger.info(f"Telemetry written successfully: {total:,} rows ({len(machines)} machines × {HOURS} hours)")
        print(f"  ✓ Telemetry: {out_path}  ({total:,} rows)")
    except Exception as e:
        logger.error(f"Failed to write telemetry to {out_path}: {e}", exc_info=True)
        raise


def main():
    logger.info("=" * 70)
    logger.info("Starting mock data generation")
    logger.info(f"Configuration: {len(MACHINES)} machines × {HOURS} hours = {len(MACHINES)*HOURS:,} rows")
    logger.info(f"Start timestamp: {START_TS}")
    logger.info("=" * 70)
    
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Data directory: {data_dir}")

    print(f"\n🔧 Generating mock data  ({len(MACHINES)} machines × {HOURS} hours = {len(MACHINES)*HOURS:,} rows)\n")
    
    try:
        write_registry(MACHINES, data_dir / "machine_registry.csv")
        write_telemetry(MACHINES, data_dir / "machine_telemetry.csv")

        print("\nDone.")
        print("   Expected risk tiers (by end of 720h window):")
        print("   CRITICAL : MCH_A01, MCH_B03  (heavy degradation + overdue service + E5xx)")
        print("   HIGH     : MCH_A02, MCH_B04, MCH_C05  (moderate degradation + near-overdue)")
        print("   MEDIUM   : MCH_A03, MCH_B05, MCH_C06  (mild degradation)")
        print("   LOW      : MCH_C07, MCH_C08  (recently serviced, no degradation)")
        
        logger.info("Mock data generation completed successfully")
        logger.info("Expected distribution: 2 CRITICAL, 3 HIGH, 3 MEDIUM, 2 LOW")
        
    except Exception as e:
        logger.critical(f"Mock data generation failed: {e}", exc_info=True)
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
