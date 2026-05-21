# =============================================================
# deploy_simple.ps1
# Deploy script — works even when EC2 IP changes after restart.
#
# Usage (default PEM path):
#   powershell -ExecutionPolicy Bypass -File deploy\deploy_simple.ps1
#
# Usage (custom IP — paste the new IP from AWS Console):
#   powershell -ExecutionPolicy Bypass -File deploy\deploy_simple.ps1 `
#     -EC2Host "ec2-XX-XX-XX-XX.compute-1.amazonaws.com"
#
# NOTE: Every time you STOP and START an EC2 instance, AWS assigns
#       a new public IP. Just pass the new hostname with -EC2Host.
#       The PEM key never changes — only the IP does.
# =============================================================

param(
    [string]$EC2Host    = "",                                    
    [string]$PemFile    = "d:\Machinary-problem\sheetal-server.pem",
    [string]$RemoteUser = "admin",
    [string]$ProjectDir = "d:\Machinary-problem\plant-maintenance"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Plant Maintenance — Deploy & Run"          -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# ── Resolve EC2 host (prompt if not passed) ────────────────────
if (-not $EC2Host) {
    Write-Host ""
    Write-Host "  EC2 IP not provided." -ForegroundColor Yellow
    Write-Host "  Find it in: AWS Console → EC2 → Instances → Public IPv4 DNS" -ForegroundColor Gray
    Write-Host ""
    $rawInput = Read-Host "  Paste EC2 hostname or IP (e.g. ec2-13-217-12-163.compute-1.amazonaws.com)"
    $EC2Host  = $rawInput.Trim()
}

# Accept bare IP like 13.217.12.163 — convert to DNS form if needed
if ($EC2Host -match '^\d+\.\d+\.\d+\.\d+$') {
    $parts    = $EC2Host -split '\.'
    $EC2Host  = "ec2-$($parts[0])-$($parts[1])-$($parts[2])-$($parts[3]).compute-1.amazonaws.com"
    Write-Host "  Converted to DNS: $EC2Host" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  Target  : $EC2Host"   -ForegroundColor White
Write-Host "  PEM     : $PemFile"   -ForegroundColor White
Write-Host "  User    : $RemoteUser" -ForegroundColor White
Write-Host ""

# ── Verify PEM file exists ─────────────────────────────────────
if (-not (Test-Path $PemFile)) {
    Write-Host "  ERROR: PEM file not found at $PemFile" -ForegroundColor Red
    Write-Host "  Update the -PemFile parameter or move the key to that path." -ForegroundColor Red
    exit 1
}

# ── Step 1: Test SSH connectivity ─────────────────────────────
Write-Host "[1/5] Testing SSH connection..." -ForegroundColor Yellow

$sshTest = ssh -i $PemFile `
    -o StrictHostKeyChecking=no `
    -o ConnectTimeout=15 `
    "${RemoteUser}@${EC2Host}" `
    "echo SSH_OK" 2>&1

if ($sshTest -match "SSH_OK") {
    Write-Host "  OK Connected to $EC2Host" -ForegroundColor Green
} else {
    Write-Host "  ✗ SSH failed: $sshTest" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Common causes when IP changes:" -ForegroundColor Yellow
    Write-Host "  1. You pasted the old IP — get the new one from AWS Console"
    Write-Host "  2. Security Group blocks port 22 from your current IP"
    Write-Host "     → AWS Console → EC2 → Security Groups → Edit Inbound Rules"
    Write-Host "     → Add SSH port 22, source = My IP"
    Write-Host "  3. Instance is still starting — wait 60 seconds and retry"
    Write-Host ""
    exit 1
}

# ── Step 2: Generate mock data locally (skip if already exists) ─
Write-Host "`n[2/5] Checking mock data..." -ForegroundColor Yellow

$telemetryFile = Join-Path $ProjectDir "data\machine_telemetry.csv"

if (-not (Test-Path $telemetryFile)) {
    Write-Host "  Generating mock data..." -ForegroundColor Gray
    & python "$ProjectDir\scripts\generate_mock_data.py"
    Write-Host "  OK Mock data generated" -ForegroundColor Green
} else {
    Write-Host "  OK Mock data already exists - skipping generation" -ForegroundColor Green
}

# ── Step 3: Upload project files to EC2 ───────────────────────
Write-Host "`n[3/5] Uploading project files..." -ForegroundColor Yellow

# Create remote directory structure first
ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "mkdir -p ~/plant-maintenance/data ~/plant-maintenance/scripts ~/plant-maintenance/dashboard ~/plant-maintenance/deploy ~/plant-maintenance/utils ~/plant-maintenance/sql"

scp -i $PemFile -o StrictHostKeyChecking=no -r "$ProjectDir\*" `
    "${RemoteUser}@${EC2Host}:~/plant-maintenance/"

Write-Host "  OK Files uploaded" -ForegroundColor Green

# ── Step 4: Load data into Exasol ─────────────────────────────
Write-Host "`n[4/5] Loading data and scoring..." -ForegroundColor Yellow

# Check Exasol is running first
$exaCheck = ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "docker ps --filter name=exasol --format '{{.Status}}' 2>/dev/null || echo NOT_RUNNING" 2>&1

if ($exaCheck -match "Up") {
    Write-Host "  OK Exasol is running" -ForegroundColor Green
} else {
    Write-Host "  Exasol not running — starting it..." -ForegroundColor Yellow
    ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
        "docker start exasol-ce 2>/dev/null || echo 'Could not start — may need install_exasol.sh'"
    Write-Host "  Waiting 30s for Exasol to boot..." -ForegroundColor Gray
    Start-Sleep -Seconds 30
}

ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "cd ~/plant-maintenance && python3 scripts/load_to_exasol.py --host localhost --port 8563 --user sys --password exasol"

Write-Host "  OK Data loaded and scored" -ForegroundColor Green

# ── Step 5: Start Streamlit dashboard ─────────────────────────
Write-Host "`n[5/5] Starting dashboard..." -ForegroundColor Yellow

# Kill any existing streamlit first
ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "pkill -f streamlit 2>/dev/null; sleep 2; echo killed"

# Start fresh
ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "cd ~/plant-maintenance && nohup python3 -m streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true --server.fileWatcherType none > /tmp/streamlit.log 2>&1 & disown; echo STARTED"

# Wait and verify port is up
Write-Host "  Waiting for dashboard to start..." -ForegroundColor Gray
Start-Sleep -Seconds 12

$portCheck = ssh -i $PemFile -o StrictHostKeyChecking=no "${RemoteUser}@${EC2Host}" `
    "ss -tlnp | grep 8501 | head -1" 2>&1

if ($portCheck -match "8501") {
    Write-Host "  OK Dashboard is live on port 8501" -ForegroundColor Green
} else {
    Write-Host "  ⚠ Port 8501 not detected yet — may still be starting" -ForegroundColor Yellow
    Write-Host "  Check logs: ssh into EC2 and run: cat /tmp/streamlit.log" -ForegroundColor Gray
}

# ── Done ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Deployment Complete!"                       -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Open the SSH tunnel in a NEW terminal window:" -ForegroundColor Green
Write-Host ""
Write-Host "  ssh -i `"$PemFile`" -L 8501:localhost:8501 ${RemoteUser}@${EC2Host}" -ForegroundColor White
Write-Host ""
Write-Host "  Then open your browser at: http://localhost:8501" -ForegroundColor White
Write-Host ""
Write-Host "  Keep that tunnel terminal open while using the dashboard." -ForegroundColor Gray
Write-Host ""
Write-Host "  If the IP changes next time, just run:" -ForegroundColor Gray
Write-Host "  powershell -ExecutionPolicy Bypass -File deploy\deploy_simple.ps1" -ForegroundColor Gray
Write-Host "  (it will prompt you for the new IP)" -ForegroundColor Gray
Write-Host ""
