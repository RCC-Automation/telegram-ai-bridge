$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:TELEGRAM_NOTIFIER_INTERACTIVE_CODEX = "true"
Set-Content -Path (Join-Path $PSScriptRoot "telegram_notifier_restart_pid.txt") -Value $PID
py -3 .\telegram_service_bootstrap.py `
    .\telegram_notifier_service.py `
    --stdout (Join-Path $PSScriptRoot "telegram_notifier_service.out.log") `
    --stderr (Join-Path $PSScriptRoot "telegram_notifier_service.err.log")
