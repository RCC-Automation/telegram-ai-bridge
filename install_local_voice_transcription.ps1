$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot
py -3 -m pip install --target ".vendor_py" faster-whisper

$model = $env:TELEGRAM_LOCAL_WHISPER_MODEL
if ([string]::IsNullOrWhiteSpace($model)) {
    $model = "base"
}

py -3 -c "import sys; from pathlib import Path; sys.path.insert(0, r'.vendor_py'); from faster_whisper import WhisperModel; root = Path(r'..\telegram-messages\models\faster-whisper'); root.mkdir(parents=True, exist_ok=True); WhisperModel('$model', device='cpu', compute_type='int8', download_root=str(root)); print('Downloaded/verified local faster-whisper model:', '$model')"
py -3 .\telegram_voice_transcribe.py --status
