# =============================================================
# deploy.ps1
# Windows PowerShell deployment script
# Fixes PEM permissions, uploads project, runs remote setup
# =============================================================

param(
    [string]$EC2Host   = "ec2-18-212-151-119.compute-1.amazonaws.com",
    [string]$PemFile   = "d:\Machinary-problem\sheetal-server.pem",
    [string]$RemoteUser = "admin",
    [string]$ProjectDir = "d:\Machinary-problem\plant-maintenance"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Phase 1 Deployment Script" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# ── Step 1: Fix PEM file permissions ──────────────────────────
Write-Host "`n[1/6] Fixing PEM file permissions..." -ForegroundColor Yellow

# Remove all inherited permissions and grant only current user
$acl = Get-Acl $PemFile
$acl.SetAccessRuleProtection($true, $false)   # disable inheritance
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $currentUser, "Read", "Allow"
)
$acl.AddAccessRule($rule)
Set-Acl -Path $PemFile -AclObject $acl
Write-Host "  OK PEM permissions fixed for $currentUser" -ForegroundColor Green

# ── Step 2: Test SSH connectivity ─────────────────────────────
Write-Host "`n[2/6] Testing SSH connection to $EC2Host..." -ForegroundColor Yellow

$sshTest = ssh -i $PemFile `
    -o StrictHostKeyChecking=no `
    -o ConnectTimeout=10 `
    "${RemoteUser}@${EC2Host}" `
    "echo SSH_OK" 2>&1

if ($sshTest -match "SSH_OK") {
    Write-Host "  OK SSH connection successful" -ForegroundColor Green
} else {
    Write-Host "  ✗ SSH connection FAILED" -ForegroundColor Red
    Write-Host "  Output: $sshTest"
    Write-Host ""
    Write-Host "  ACTION REQUIRED — Fix EC2 Security Group:" -ForegroundColor Red
    Write-Host "  1. Go to AWS Console → EC2 → Instances"
    Write-Host "  2. Select your instance → Security tab"
    Write-Host "  3. Click the Security Group link"
    Write-Host "  4. Edit Inbound Rules → Add Rule:"
    Write-Host "     Type: SSH | Port: 22 | Source: My IP (or 0.0.0.0/0 for testing)"
    Write-Host "  5. Add Rule: Custom TCP | Port: 8563 | Source: My IP"
    Write-Host "  6. Add Rule: Custom TCP | Port: 8501 | Source: My IP"
    Write-Host "  7. Save rules and re-run this script"
    exit 1
}

# ── Step 3: Generate mock data locally ────────────────────────
Write-Host "`n[3/6] Generating mock data locally..." -ForegroundColor Yellow

$dataDir = Join-Path $ProjectDir "data"
if (-not (Test-Path (Join-Path $dataDir "machine_telemetry.csv"))) {
    python "$ProjectDir\scripts\generate_mock_data.py"
    Write-Host "  OK Mock data generated" -ForegroundColor Green
} else {
    Write-Host "  OK Mock data already exists, skipping" -ForegroundColor Green
}

# ── Step 4: Upload project to EC2 ─────────────────────────────
Write-Host "`n[4/6] Uploading project files to EC2..." -ForegroundColor Yellow

# Use scp to upload the entire project directory
scp -i $PemFile `
    -o StrictHostKeyChecking=no `
    -r $ProjectDir `
    "${RemoteUser}@${EC2Host}:~/plant-maintenance"

Write-Host "  OK Project uploaded to ~/plant-maintenance" -ForegroundColor Green

# ── Step 5: Run remote installation ───────────────────────────
Write-Host "`n[5/6] Running Exasol CE installation on EC2..." -ForegroundColor Yellow
Write-Host "  (This may take 3-5 minutes)" -ForegroundColor Gray

ssh -i $PemFile `
    -o StrictHostKeyChecking=no `
    "${RemoteUser}@${EC2Host}" `
    "chmod +x ~/plant-maintenance/deploy/install_exasol.sh && bash ~/plant-maintenance/deploy/install_exasol.sh"

Write-Host "  OK Remote installation complete" -ForegroundColor Green

# ── Step 6: Load data and start dashboard ─────────────────────
Write-Host "`n[6/6] Loading data into Exasol and starting dashboard..." -ForegroundColor Yellow

ssh -i $PemFile `
    -o StrictHostKeyChecking=no `
    "${RemoteUser}@${EC2Host}" `
    @"
cd ~/plant-maintenance
python3 scripts/generate_mock_data.py
python3 scripts/load_to_exasol.py --host localhost --password 'exasol'
echo 'DATA_LOADED_OK'
"@

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host " Deployment Complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host " To start the dashboard, SSH in and run:" -ForegroundColor Cyan
Write-Host "   ssh -i $PemFile ${RemoteUser}@${EC2Host}"
Write-Host "   cd ~/plant-maintenance"
Write-Host "   streamlit run dashboard/app.py -- --host localhost &"
Write-Host ""
Write-Host " Then open in browser (with SSH tunnel):"
Write-Host "   ssh -i $PemFile -L 8501:localhost:8501 ${RemoteUser}@${EC2Host}"
Write-Host "   http://localhost:8501"
Write-Host ""
