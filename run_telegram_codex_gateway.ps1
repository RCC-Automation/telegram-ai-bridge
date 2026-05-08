$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Set-Content -Path (Join-Path $PSScriptRoot "telegram_gateway_restart_pid.txt") -Value $PID
py .\telegram_codex_gateway.py
