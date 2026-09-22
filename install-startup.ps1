#Requires -Version 5.1
<#
.SYNOPSIS
    Starts MX Master Tweaker automatically when you sign in to Windows.
.DESCRIPTION
    Registers a scheduled task that launches the app in your own session at every logon,
    and restarts it if it stops. No administrator rights are needed: the task runs as
    you, which is also what lets it see your mouse at all.

    The app has an equivalent tick box in its settings window; this script exists so the
    same thing can be done from a terminal, and so the task can be given a start delay.

    Use -Remove to undo it.
.EXAMPLE
    .\install-startup.ps1
    .\install-startup.ps1 -DelaySeconds 5
    .\install-startup.ps1 -Remove
#>
[CmdletBinding()]
param(
    # How long to wait after sign-in before starting, so the desktop can settle first.
    [int]$DelaySeconds = 10,

    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$taskName = "MX Master Tweaker"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed the '$taskName' scheduled task" -ForegroundColor Green
    }
    else {
        Write-Host "Nothing to remove." -ForegroundColor Yellow
    }
    return
}

$starter = Join-Path $PSScriptRoot "startup.pyw"
if (-not (Test-Path $starter)) { throw "startup.pyw was not found next to this script" }

# pythonw.exe has no console at all, so nothing can flash on screen at sign-in.
$python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python was not found on PATH. Run .\setup.ps1 first." }
    Write-Warning "pythonw.exe was not found; using python.exe, which shows a console window."
}

$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $python.Source `
    -Argument "`"$starter`"" -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
if ($DelaySeconds -gt 0) { $trigger.Delay = "PT${DelaySeconds}S" }

# A mouse hook is cheap and the app is idle almost all of the time, so there is no
# execution time limit and no reason to stop it on battery.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 3

# Interactive, not S4U: a hook only sees input in a real, signed-in desktop session.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force -ErrorAction Stop `
    -Description "Personalises the Logitech MX Master buttons, locally" | Out-Null

Write-Host "Installed the '$taskName' scheduled task" -ForegroundColor Green
Write-Host "It will start $DelaySeconds s after you sign in." -ForegroundColor Green
Write-Host "Start it now with:  Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Cyan
Write-Host "Undo with:          .\install-startup.ps1 -Remove" -ForegroundColor Cyan
