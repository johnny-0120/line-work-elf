$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python setup.py
$taskRuntime = Join-Path $env:LOCALAPPDATA 'LineWorkElf/runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
npm.cmd install --prefix $taskRuntime n8n@2.43.7 --no-audit --no-fund --loglevel error
if ($LASTEXITCODE -ne 0) { throw 'n8n installation failed.' }
python local_runtime.py init
if ($LASTEXITCODE -ne 0) { throw 'n8n import failed.' }
python local_runtime.py start
Write-Host 'Open http://localhost:5678 to create your local n8n owner account.'
