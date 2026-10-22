$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskHomeLine = Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env') | Where-Object { $_ -match '^ELF_HOME=' } | Select-Object -Last 1
$taskBase = if ($taskHomeLine) { $taskHomeLine.Substring(9).Trim() } else { Join-Path $env:LOCALAPPDATA 'LineWorkElf' }
$taskCloudflared = Join-Path $taskBase 'bin/cloudflared.exe'
if (-not (Test-Path -LiteralPath $taskCloudflared)) {
    throw 'cloudflared is not installed. See README.md for the official download.'
}
$taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8790/health'
if (-not $taskHealth.ok) { throw 'Start the local services before starting the tunnel.' }
$taskState = Join-Path $taskBase 'state'
$taskLog = Join-Path $taskState 'tunnel.log'
$taskOut = Join-Path $taskState 'tunnel.stdout.log'
$taskProcessFile = Join-Path $taskState 'processes.json'
$taskProcesses = if (Test-Path -LiteralPath $taskProcessFile) { Get-Content -LiteralPath $taskProcessFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
if ($taskProcesses.PSObject.Properties.Name -contains 'cloudflared') {
    $taskPrevious = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskProcesses.cloudflared)" -ErrorAction SilentlyContinue
    if ($taskPrevious -and $taskPrevious.CommandLine -and $taskPrevious.CommandLine.Contains('cloudflared')) {
        Write-Host 'The project tunnel is already running. Check the existing tunnel.log.'
        exit 0
    }
}
$taskProcess = Start-Process -FilePath $taskCloudflared -ArgumentList @('tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8790') -WindowStyle Hidden -RedirectStandardError $taskLog -RedirectStandardOutput $taskOut -PassThru
$taskProcesses | Add-Member -MemberType NoteProperty -Name cloudflared -Value $taskProcess.Id -Force
$taskProcesses | ConvertTo-Json | Set-Content -LiteralPath $taskProcessFile -Encoding utf8
Write-Host ('Tunnel started. Its public HTTPS URL will appear in ' + $taskLog)
