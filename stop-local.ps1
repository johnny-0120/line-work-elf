param([ValidateSet('all', 'elf', 'n8n', 'relay', 'calendar', 'cloudflared')][string]$Service = 'all')
$ErrorActionPreference = 'Stop'
if ($Service -eq 'all' -and (Get-ScheduledTask -TaskName 'LINE Work Elf Supervisor' -ErrorAction SilentlyContinue)) {
    $taskExisting = Get-ScheduledTask -TaskName 'LINE Work Elf Supervisor'
    if ($taskExisting.Actions.WorkingDirectory -eq $PSScriptRoot) {
        Disable-ScheduledTask -TaskName 'LINE Work Elf Supervisor' | Out-Null
        Stop-ScheduledTask -TaskName 'LINE Work Elf Supervisor'
    }
}
$taskHomeLine = Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env') | Where-Object { $_ -match '^ELF_HOME=' } | Select-Object -Last 1
$taskHome = if ($taskHomeLine) { $taskHomeLine.Substring(9).Trim() } else { Join-Path $env:LOCALAPPDATA 'LineWorkElf' }
$taskState = Join-Path $taskHome 'state'
$taskProcessFile = Join-Path $taskState 'processes.json'
if (-not (Test-Path -LiteralPath $taskProcessFile)) { exit 0 }
$taskProcesses = Get-Content -LiteralPath $taskProcessFile -Raw | ConvertFrom-Json
foreach ($taskEntry in $taskProcesses.PSObject.Properties) {
    if ($Service -ne 'all' -and $taskEntry.Name -ne $Service) { continue }
    $taskPid = [int]$taskEntry.Value
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $taskPid" -ErrorAction SilentlyContinue
    if (-not $taskProcess) { continue }
    $taskExpected = switch ($taskEntry.Name) {
        'elf' { Join-Path $PSScriptRoot 'elf.py' }
        'calendar' { Join-Path $PSScriptRoot 'calendar_oauth.py' }
        'relay' { Join-Path $PSScriptRoot 'webhook_relay.py' }
        'cloudflared' { Join-Path $taskHome 'bin/cloudflared.exe' }
        'n8n' { Join-Path $taskHome 'runtime/node_modules/n8n/bin/n8n' }
        default { $null }
    }
    if (-not $taskExpected) { continue }
    if ($taskProcess.CommandLine -and $taskProcess.CommandLine.Replace('/', '\').Contains($taskExpected.Replace('/', '\'))) {
        Stop-Process -Id $taskPid
        Write-Host ($taskEntry.Name + ' stopped.')
    } else {
        Write-Host ('Skipped ' + $taskEntry.Name + ': PID no longer belongs to this project.')
    }
}
