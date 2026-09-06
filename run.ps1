param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $env:USERPROFILE 'miniconda3/envs/wuxia/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
& $python -m uvicorn studio.app:app --host 127.0.0.1 --port $Port
