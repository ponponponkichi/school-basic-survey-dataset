param(
    [ValidateRange(0, 23)]
    [int]$Hour = 9,
    [ValidateRange(0, 59)]
    [int]$Minute = 0,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$taskName = "SchoolBasicSurvey_MonthlyUpdate"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed task: $taskName"
    exit 0
}

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $projectDir "run_monthly_update.cmd"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher was not found: $launcher"
}

$startAt = (Get-Date).Date.AddHours($Hour).AddMinutes($Minute)
$action = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\cmd.exe" `
    -Argument "/d /c `"`"$launcher`"`"" `
    -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $startAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Checks daily and updates all published years and forms once per month."

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Write-Host "Registered task: $taskName"
Write-Host ("Checks daily at {0:D2}:{1:D2} and updates only once per month." -f $Hour, $Minute)
Write-Host "A missed run starts after the next startup and sign-in."
