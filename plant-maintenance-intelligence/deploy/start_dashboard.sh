#!/bin/bash
# =============================================================
# start_dashboard.sh
# Starts the Streamlit dashboard on the EC2 instance
# Run after install_exasol.sh and load_to_exasol.py
# =============================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="/tmp/streamlit_dashboard.log"
PID_FILE="/tmp/streamlit_dashboard.pid"

echo "Starting Plant Maintenance Dashboard..."
echo "Project dir: $PROJECT_DIR"

# Kill any existing instance
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing dashboard (PID $OLD_PID)..."
        kill "$OLD_PID"
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# Start Streamlit in background
nohup python3 -m streamlit run \
    "$PROJECT_DIR/dashboard/app.py" \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false \
    -- \
    --host localhost \
    --port 8563 \
    --user sys \
    --password exasol \
    > "$LOG_FILE" 2>&1 &

DASHBOARD_PID=$!
echo $DASHBOARD_PID > "$PID_FILE"

sleep 3

if kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    echo ""
    echo "Dashboard started (PID $DASHBOARD_PID)"
    echo "   Log: $LOG_FILE"
    echo ""
    echo "Access options:"
    echo "  1. SSH tunnel (recommended):"
    echo "     ssh -i sheetal-server.pem -L 8501:localhost:8501 admin@ec2-18-212-151-119.compute-1.amazonaws.com"
    echo "     Then open: http://localhost:8501"
    echo ""
    echo "  2. Direct (if port 8501 open in Security Group):"
    echo "     http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8501"
else
    echo "Dashboard failed to start. Check log: $LOG_FILE"
    cat "$LOG_FILE"
    exit 1
fi
