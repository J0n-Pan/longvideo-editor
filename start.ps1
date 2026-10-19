$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$taskPython = Join-Path $taskRoot 'work\longvideo-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '未找到项目虚拟环境，请查看README。' }
Set-Location -LiteralPath $PSScriptRoot
& $taskPython -X utf8 -m uvicorn app:app --host 127.0.0.1 --port 8765
