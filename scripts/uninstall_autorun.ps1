# Removes the Scheduled Task for the Wio PC Monitor sender
$TaskName = 'Wio PC Monitor Sender'
try {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
  Write-Host "Removed scheduled task: $TaskName"
} catch {
  Write-Warning "Scheduled task not found or could not be removed: $TaskName"
}
