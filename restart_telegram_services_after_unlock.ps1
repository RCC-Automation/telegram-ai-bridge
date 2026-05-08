param(
    [switch]$EnableWakeGateway
)

$ErrorActionPreference = "Continue"

$Repo = $PSScriptRoot
$Lock = Join-Path $Repo "telegram_codex_gateway.lock"
$Log = Join-Path $Repo "telegram_restart_helper.log"
$NotifierPidFile = Join-Path $Repo "telegram_notifier_restart_pid.txt"
$GatewayPidFile = Join-Path $Repo "telegram_gateway_restart_pid.txt"

function Write-RestartLog($Message) {
    Add-Content -Path $Log -Value "[$(Get-Date -Format s)] $Message"
}

Write-RestartLog "restart helper started"

for ($i = 0; $i -lt 180; $i++) {
    if (-not (Test-Path $Lock)) {
        break
    }
    Start-Sleep -Seconds 1
}

$pidsToStop = @()
if (Test-Path $NotifierPidFile) {
    $pidsToStop += [int](Get-Content $NotifierPidFile)
}
if (Test-Path $GatewayPidFile) {
    $pidsToStop += [int](Get-Content $GatewayPidFile)
}

foreach ($targetPid in $pidsToStop) {
    try {
        Stop-Process -Id $targetPid -Force -ErrorAction Stop
        Write-RestartLog "stopped process $targetPid"
    } catch {
        Write-RestartLog "could not stop process ${targetPid}: $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 2

Write-RestartLog "starting notifier"
Start-Process powershell -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $Repo "run_telegram_notifier.ps1")

Start-Sleep -Seconds 3

if ($EnableWakeGateway) {
    Write-RestartLog "starting gateway"
    Start-Process powershell -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $Repo "run_telegram_codex_gateway.ps1")
} else {
    Write-RestartLog "gateway disabled; embedded MCP mode should read Telegram inbox"
}

Write-RestartLog "restart helper finished"
