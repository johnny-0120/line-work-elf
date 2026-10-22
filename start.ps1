$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskSupervisor = Get-ScheduledTask -TaskName 'LINE Work Elf Supervisor' -ErrorAction SilentlyContinue
if ($taskSupervisor) {
    if ($taskSupervisor.Actions.WorkingDirectory -ne $PSScriptRoot) {
        throw 'The supervisor belongs to another checkout. Use that checkout or stop it before installing a second instance.'
    }
    Enable-ScheduledTask -TaskName 'LINE Work Elf Supervisor' | Out-Null
    Start-ScheduledTask -TaskName 'LINE Work Elf Supervisor'
    Write-Host 'The supervised local assistant is starting; existing credentials and workflows are preserved.'
    exit 0
}
python setup.py
python local_runtime.py start
if ($LASTEXITCODE -ne 0) { throw 'Local startup failed. Run install-local.ps1 for the first installation.' }
