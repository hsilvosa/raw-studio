$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$model = Join-Path $root '.studio/models/qwen3-vl-4b/Qwen3VL-4B-Instruct-Q4_K_M.gguf'
$projector = Join-Path $root '.studio/models/qwen3-vl-4b/mmproj-Qwen3VL-4B-Instruct-F16.gguf'
if (-not (Test-Path -LiteralPath $model)) { throw 'Ejecuta primero scripts/download-model.ps1' }
& llama-server -m $model --mmproj $projector --host 127.0.0.1 --port 8081 -ngl 99 -c 8192 --parallel 1
