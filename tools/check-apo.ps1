# One-shot "is EqualizerAPO actually processing this mic?" check.
#
# Records a few seconds through WASAPI shared mode (the path APOs live in) and
# reports how much of it is hard-muted by the RNNoise gate plus the levels.
#
#   .\tools\check-apo.ps1
#   .\tools\check-apo.ps1 -Match "Sennheiser" -Seconds 8
#
# Interpretation:
#   zeros > 90 %, rms < -80 dBFS  -> chain is live and the gate is closed (stay quiet)
#   open segments, 0 % zeros      -> chain is live, you were speaking
#   no zeros and rms like raw mic -> APO not in the path (see README notes, §4)

param(
    [string]$Match = "Sennheiser",
    [int]$Seconds = 5
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wav = Join-Path $env:TEMP "apo-check.wav"

Push-Location (Join-Path $here "apo-capture")
try {
    dotnet run -c Release -- $Match $Seconds $wav
} finally {
    Pop-Location
}

python (Join-Path $here "verify_capture.py") $wav
