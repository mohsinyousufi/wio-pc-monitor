param(
    [string]$Port = "",
    [string]$Interval = "0.5",
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

# Install deps
Write-Host "Installing requirements..."
& $Python -m pip install --upgrade pip
& $Pip install -r $Requirements

# Compose script arguments
$ArgsList = @()
if ($Port -ne "") { $ArgsList += @('--port', $Port) }
if ($Interval -ne "") { $ArgsList += @('--interval', $Interval) }
if ($VerboseMode) { $ArgsList += @('--verbose') }

# Create a wrapper PowerShell script that activates venv and runs sender
$RunnerPath = Join-Path $RepoRoot 'scripts\run_sender.ps1'
$RunnerContent = @"
# Auto-generated runner for Wio PC Monitor sender
`$RepoRoot = "$RepoRoot"
`$Python = Join-Path `$RepoRoot '.venv\Scripts\python.exe'
`$Sender = Join-Path `$RepoRoot 'pc\pc_stats_sender.py'
`$Args = @(
@({0})
)
# Close stale serial monitor processes if any (best-effort)
# Start sender in a normal window
Start-Process -FilePath `$Python -ArgumentList @(`$Sender) + `$Args -WindowStyle Normal
"@ -f ($ArgsList | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' } -join ', ')
Set-Content -LiteralPath $RunnerPath -Value $RunnerContent -Encoding UTF8

# Register Scheduled Task
$TaskName = 'Wio PC Monitor Sender'
$argList = @()
if ($Sender -ne "") { $argList += '"' + $Sender + '"' }
if ($Port -ne "") { $argList += @('--port', $Port) }
if ($Interval -ne "") { $argList += @('--interval', $Interval) }
if ($VerboseMode) { $argList += @('--verbose') }
$ArgString = $argList -join ' '

# Register Scheduled Task to run pythonw.exe (no console window)
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument $ArgString -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Run Wio PC Monitor sender at user logon (background)' -Force -Hidden

Write-Host "Scheduled Task '$TaskName' registered (hidden). It will run at user logon in the background."
Write-Host "To test quickly: `"$Pythonw`" $ArgString"
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# Add 5s delay using repetition trick (workaround for simple delay)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Run Wio PC Monitor sender at user logon' -Force

Write-Host "Scheduled Task '$TaskName' registered. It will run at user logon."
Write-Host "You can test it now: powershell -NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`""
