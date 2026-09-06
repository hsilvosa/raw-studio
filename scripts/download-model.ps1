$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$hf = Join-Path $env:USERPROFILE 'miniconda3/envs/wuxia/Scripts/hf.exe'
& $hf download Qwen/Qwen3-VL-4B-Instruct-GGUF --include '*Q4_K_M.gguf' 'mmproj*F16.gguf' --local-dir (Join-Path $root '.studio/models/qwen3-vl-4b')
if ($LASTEXITCODE -ne 0) { throw 'La descarga no se completó' }
