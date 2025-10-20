param(
    [string]$Port = "",
    [string]$Interval = "0.5",
    [string]$BleAddress = "",
    [switch]$VerboseMode
)

# This script registers a Scheduled Task to auto-run the PC stats sender at user logon.
# It ensures the virtual environment exists and dependencies are installed prior to run.

$ErrorActionPreference = 'Stop'

# Resolve repo root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Pythonw = Join-Path $RepoRoot '.venv\Scripts\pythonw.exe'
$Pip = Join-Path $RepoRoot '.venv\Scripts\pip.exe'
$Requirements = Join-Path $RepoRoot 'pc\requirements.txt'
$Sender = Join-Path $RepoRoot 'pc\pc_stats_sender.py'

Write-Host "Repo root: $RepoRoot"

# Create venv if missing
if (!(Test-Path $Python)) {
    Write-Host "Creating venv..."
    & python -m venv (Join-Path $RepoRoot '.venv')
}

# Install deps (avoid pip.exe shim issues by using python -m pip)
Write-Host "Installing requirements..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r $Requirements

# Compose script arguments
$ArgsList = @()
if ($Port -ne "") { $ArgsList += @('--port', $Port) }
if ($Interval -ne "") { $ArgsList += @('--interval', $Interval) }
if ($VerboseMode) { $ArgsList += @('--verbose') }

# Create a wrapper PowerShell script that activates venv and runs sender
$RunnerPath = Join-Path $RepoRoot 'scripts\run_sender.ps1'
# Build argument items string for embedding
$ArgsItems = ($ArgsList | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ', '
$RunnerContent = @"
# Auto-generated runner for Wio PC Monitor sender
`$RepoRoot = Split-Path -Parent `$PSScriptRoot
`$Pythonw = Join-Path `$RepoRoot '.venv\Scripts\pythonw.exe'
`$Sender = Join-Path `$RepoRoot 'pc\pc_stats_sender.py'
# Prepare log directory under LocalAppData
`$LogDir = Join-Path `$env:LOCALAPPDATA 'wio-pc-monitor'
if (!(Test-Path `$LogDir)) { New-Item -ItemType Directory -Path `$LogDir | Out-Null }
`$LogFile = Join-Path `$LogDir 'sender.log'
`$ScriptArgs = @($ArgsItems) + @('--log-file', `$LogFile, '--open-wait', '0.3')
# Build argument list: script path + arguments
`$AllArgs = @(`$Sender) + `$ScriptArgs
# Start sender hidden (no console) using pythonw
Start-Process -FilePath `$Pythonw -ArgumentList `$AllArgs -WorkingDirectory `$RepoRoot -WindowStyle Hidden
"@
Set-Content -LiteralPath $RunnerPath -Value $RunnerContent -Encoding UTF8

# Register Scheduled Task
$TaskName = 'Wio PC Monitor Sender'
$argList = @()
if ($Sender -ne "") { $argList += '"' + $Sender + '"' }
if ($Port -ne "") { $argList += @('--port', $Port) }
if ($Interval -ne "") { $argList += @('--interval', $Interval) }
if ($VerboseMode) { $argList += @('--verbose') }
if ($BleAddress -ne "") { $argList += @('--ble-address', $BleAddress) }
$ArgString = $argList -join ' '

# Register Scheduled Task to run the PowerShell runner (handles quoting/venv; hidden window)
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunnerPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Run Wio PC Monitor sender at user logon (background)' -Force

Write-Host "Scheduled Task '$TaskName' registered. It will run at user logon in the background."
Write-Host "You can test it now (optional): powershell -NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`""
