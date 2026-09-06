param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:PHOTO_ROOT -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\FOTOS JAPON'))) {
    $env:PHOTO_ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..\FOTOS JAPON')).Path
}
$python = Join-Path $env:USERPROFILE 'miniconda3/envs/wuxia/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
& $python -m uvicorn studio.app:app --host 127.0.0.1 --port $Port
