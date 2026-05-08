param(
    [switch]$EnableWakeGateway
)

$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

$NotifierOutLog = Join-Path $PSScriptRoot "telegram_notifier_service.out.log"
$NotifierErrLog = Join-Path $PSScriptRoot "telegram_notifier_service.err.log"
$GatewayOutLog = Join-Path $PSScriptRoot "telegram_codex_gateway.out.log"
$GatewayErrLog = Join-Path $PSScriptRoot "telegram_codex_gateway.err.log"

function Stop-BridgeProcess($Process, $Name) {
    if ($null -eq $Process) {
        return
    }
    try {
        if (-not $Process.HasExited) {
            Write-Host "Stopping $Name (PID $($Process.Id))..."
            Stop-Process -Id $Process.Id -Force -ErrorAction Stop
        }
    } catch {
        Write-Warning "Could not stop ${Name}: $($_.Exception.Message)"
    }
}

Write-Host "Starting Telegram notifier..."
$notifier = Start-Process py `
    -ArgumentList ".\telegram_notifier_service.py" `
    -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $NotifierOutLog `
    -RedirectStandardError $NotifierErrLog `
    -PassThru `
    -WindowStyle Hidden

Start-Sleep -Seconds 3

$gateway = $null
if ($EnableWakeGateway) {
    Write-Host "Starting Telegram Codex gateway..."
    $gateway = Start-Process py `
        -ArgumentList ".\telegram_codex_gateway.py" `
        -WorkingDirectory $PSScriptRoot `
        -RedirectStandardOutput $GatewayOutLog `
        -RedirectStandardError $GatewayErrLog `
        -PassThru `
        -WindowStyle Hidden
} else {
    Write-Host "Telegram Codex gateway is disabled."
    Write-Host "Embedded MCP mode will read Telegram messages from this Codex chat."
    Write-Host "To enable the legacy wake adapter explicitly, run:"
    Write-Host "  .\run_telegram_bridge.ps1 -EnableWakeGateway"
}

Write-Host ""
Write-Host "Telegram bridge is running."
Write-Host "Notifier PID: $($notifier.Id)"
if ($null -ne $gateway) {
    Write-Host "Gateway PID:  $($gateway.Id)"
} else {
    Write-Host "Gateway PID:  disabled"
}
Write-Host "Notifier stdout: $NotifierOutLog"
Write-Host "Notifier stderr: $NotifierErrLog"
if ($null -ne $gateway) {
    Write-Host "Gateway stdout:  $GatewayOutLog"
    Write-Host "Gateway stderr:  $GatewayErrLog"
}
Write-Host ""
Write-Host "Press Ctrl+C in this window to stop both services."

try {
    while ($true) {
        Start-Sleep -Seconds 2
        if ($notifier.HasExited) {
            throw "Telegram notifier exited with code $($notifier.ExitCode). Check $NotifierOutLog and $NotifierErrLog"
        }
        if ($null -ne $gateway -and $gateway.HasExited) {
            throw "Telegram Codex gateway exited with code $($gateway.ExitCode). Check $GatewayOutLog and $GatewayErrLog"
        }
    }
} finally {
    if ($null -ne $gateway) {
        Stop-BridgeProcess $gateway "Telegram Codex gateway"
    }
    Stop-BridgeProcess $notifier "Telegram notifier"
}
