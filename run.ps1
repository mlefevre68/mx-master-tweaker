#Requires -Version 5.1
<#
.SYNOPSIS
    Starts MX Master Tweaker.
.DESCRIPTION
    Launches the app with pythonw.exe so no console window appears, and returns
    immediately. If a copy is already running, that copy's settings window is brought
    to the front instead of a second copy being started.
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Settings
    .\run.ps1 -Console -Verbose
#>
[CmdletBinding()]
param(
    # Open the settings window straight away.
    [switch]$Settings,

    # Run in this window with the log on screen, for troubleshooting.
    [switch]$Console
)

$ErrorActionPreference = "Stop"

$python = if ($Console) { "python.exe" } else { "pythonw.exe" }
$command = Get-Command $python -ErrorAction SilentlyContinue
if (-not $command) {
    throw "$python is not on PATH. Run .\setup.ps1 first, or install Python from python.org."
}

$arguments = @((Join-Path $PSScriptRoot "startup.pyw"))
if ($Settings) { $arguments += "--settings" }
if ($VerbosePreference -eq "Continue") { $arguments += "--verbose" }

if ($Console) {
    & $command.Source @arguments
}
else {
    Start-Process -FilePath $command.Source -ArgumentList $arguments `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    Write-Host "MX Master Tweaker is running - look for the mouse icon in the notification area." -ForegroundColor Green
}
