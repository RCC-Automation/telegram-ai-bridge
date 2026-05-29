$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Set-Content -Path (Join-Path $PSScriptRoot "telegram_watchdog_launcher_pid.txt") -Value $PID
py -3 .\telegram_service_bootstrap.py `
    .\telegram_service_watchdog.py `
    --stdout (Join-Path $PSScriptRoot "telegram_watchdog.out.log") `
    --stderr (Join-Path $PSScriptRoot "telegram_watchdog.err.log")
