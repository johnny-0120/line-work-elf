$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = (Get-Command pythonw.exe -ErrorAction Stop).Source
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = 'LINE Work Elf Supervisor'
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting -and $taskExisting.Actions.WorkingDirectory -ne $taskRoot) {
    throw 'An existing supervisor belongs to another checkout; refusing to replace it.'
}
$taskHomeLine = Get-Content -LiteralPath (Join-Path $taskRoot '.env') | Where-Object { $_ -match '^ELF_HOME=' } | Select-Object -Last 1
$taskHome = if ($taskHomeLine) { $taskHomeLine.Substring(9).Trim() } else { Join-Path $env:LOCALAPPDATA 'LineWorkElf' }
$taskState = Join-Path $taskHome 'state'
$taskBackup = Join-Path $taskState 'backups'
New-Item -ItemType Directory -Path $taskBackup -Force | Out-Null
# Backups contain OAuth tokens and the n8n encryption key: restrict to this user.
& icacls.exe $taskBackup /inheritance:r /grant:r "${taskUser}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect backup directory.' }
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument ('"' + (Join-Path $taskRoot 'supervisor.py') + '"') -WorkingDirectory $taskRoot
$taskLogin = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskRecovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($taskLogin,$taskRecovery) -Settings $taskSettings -Principal $taskPrincipal -Description 'Keep the local LINE assistant running; recover services and synchronize its webhook.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Supervisor registered and started. It runs while this Windows user is signed in.'
