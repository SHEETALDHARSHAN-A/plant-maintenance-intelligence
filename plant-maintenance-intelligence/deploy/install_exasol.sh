#!/bin/bash
# =============================================================
# install_exasol.sh
# Installs Exasol Community Edition via Docker on Debian/Ubuntu EC2
# Exasol CE is distributed as a Docker image: exasol/docker-db
# Run as: bash install_exasol.sh
# =============================================================

set -euo pipefail

EXASOL_IMAGE="exasol/docker-db:latest"
CONTAINER_NAME="exasol-ce"
EXASOL_PORT=8563
EXASOL_DATA_DIR="/opt/exasol/data"

echo "=============================================="
echo " Exasol CE Installer (Docker) — Phase 1 Setup"
echo "=============================================="

# ── 1. System update ──────────────────────────────────────────
echo ""
echo "[1/6] Updating system packages..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl wget ca-certificates gnupg lsb-release \
    python3 python3-pip python3-venv \
    net-tools htop netcat-openbsd
echo "  OK System packages ready"

# ── 2. Install Docker ─────────────────────────────────────────
echo ""
echo "[2/6] Installing Docker..."

if command -v docker &>/dev/null; then
    echo "  OK Docker already installed: $(docker --version)"
else
    # Official Docker install for Debian/Ubuntu
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    sudo systemctl enable docker
    sudo systemctl start  docker

    # Allow admin user to run docker without sudo
    sudo usermod -aG docker "$USER" || true
    echo "  OK Docker installed: $(docker --version)"
fi

# ── 3. Pull Exasol Docker image ───────────────────────────────
echo ""
echo "[3/6] Pulling Exasol CE Docker image (this may take a few minutes)..."
sudo docker pull "${EXASOL_IMAGE}"
echo "  OK Image pulled"

# ── 4. Start Exasol container ─────────────────────────────────
echo ""
echo "[4/6] Starting Exasol CE container..."

# Stop and remove any existing container
if sudo docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "  Removing existing container..."
    sudo docker stop  "${CONTAINER_NAME}" 2>/dev/null || true
    sudo docker rm    "${CONTAINER_NAME}" 2>/dev/null || true
fi

# Create data directory
sudo mkdir -p "${EXASOL_DATA_DIR}"

# Run Exasol CE container
# --privileged is required by Exasol for kernel parameter tuning
sudo docker run -d \
    --name "${CONTAINER_NAME}" \
    --privileged \
    -p ${EXASOL_PORT}:8563 \
    -p 2580:2580 \
    -v "${EXASOL_DATA_DIR}:/exa" \
    --restart unless-stopped \
    "${EXASOL_IMAGE}"

echo "  OK Container started: ${CONTAINER_NAME}"

# ── 5. Wait for Exasol to be ready ───────────────────────────
echo ""
echo "[5/6] Waiting for Exasol to be ready (up to 3 minutes)..."
READY=false
for i in $(seq 1 36); do
    if nc -z localhost ${EXASOL_PORT} 2>/dev/null; then
        echo "  OK Exasol is listening on port ${EXASOL_PORT} (after $((i*5))s)"
        READY=true
        break
    fi
    echo "  Waiting... ($i/36) — $(sudo docker logs ${CONTAINER_NAME} 2>&1 | tail -1)"
    sleep 5
done

if [ "$READY" = false ]; then
    echo "  [WAIT] Exasol not yet on port ${EXASOL_PORT} after 3 minutes"
    echo "  Container logs:"
    sudo docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
    echo ""
    echo "  The container may still be initializing. Check with:"
    echo "    sudo docker logs -f ${CONTAINER_NAME}"
fi

# ── 6. Install Python dependencies ───────────────────────────
echo ""
echo "[6/6] Installing Python dependencies..."

# Debian 12+ enforces PEP 668 — use venv or --break-system-packages
pip3 install \
    pyexasol==0.25.2 \
    pandas==2.2.2 \
    streamlit==1.35.0 \
    plotly==5.22.0 \
    websocket-client==1.8.0 \
    --break-system-packages 2>/dev/null \
|| pip3 install \
    pyexasol==0.25.2 \
    pandas==2.2.2 \
    streamlit==1.35.0 \
    plotly==5.22.0 \
    websocket-client==1.8.0

echo "  OK Python packages installed"

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " Installation complete!"
echo "=============================================="
echo ""
echo " Exasol CE container : ${CONTAINER_NAME}"
echo " WebSocket port      : localhost:${EXASOL_PORT}"
echo " Default credentials : sys / exasol"
echo ""
echo " Useful commands:"
echo "   sudo docker logs -f ${CONTAINER_NAME}   # watch logs"
echo "   sudo docker exec -it ${CONTAINER_NAME} bash  # shell into container"
echo "   sudo docker stop/start ${CONTAINER_NAME}     # stop/start"
echo ""
echo " Next steps:"
echo "   python3 ~/plant-maintenance/scripts/generate_mock_data.py"
echo "   python3 ~/plant-maintenance/scripts/load_to_exasol.py --host localhost"
echo "   bash ~/plant-maintenance/deploy/start_dashboard.sh"
echo ""
