#!/bin/bash
# Runs Streamlit completely detached from the SSH session
# Uses setsid + double-fork to survive SSH disconnect

export EXA_HOST=localhost
export EXA_PORT=8563
export EXA_USER=sys
export EXA_PASSWORD=exasol

LOG=/tmp/streamlit.log
PID=/tmp/streamlit.pid

# Kill any existing instance
if [ -f "$PID" ] && kill -0 "$(cat $PID)" 2>/dev/null; then
    kill "$(cat $PID)"
    sleep 1
fi

# Start fully detached
setsid python3 -m streamlit run \
    /home/admin/plant-maintenance/dashboard/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false \
    > "$LOG" 2>&1 &

echo $! > "$PID"
echo "Started PID $(cat $PID)"
sleep 3
tail -5 "$LOG"
