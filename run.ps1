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

$script = Join-Path $PSScriptRoot "startup.pyw"
if (-not (Test-Path $script)) { throw "startup.pyw was not found next to this script" }

$extra = @()
if ($Settings) { $extra += "--settings" }
if ($VerbosePreference -eq "Continue") { $extra += "--verbose" }

if ($Console) {
    # Calling a native command directly quotes each argument properly.
    & $command.Source $script @extra
}
else {
    # Start-Process joins -ArgumentList with spaces and does not quote the pieces, so an
    # array would split this path at any space in it - "OneDrive - Contoso", "My
    # Documents" - and Python would go looking for a script whose name stops at the
    # first space. Under pythonw.exe there is no console for that error to appear in, so
    # the app simply never started and said nothing. Pass one already-quoted command
    # line instead.
    $commandLine = (@("`"$script`"") + $extra) -join " "
    Start-Process -FilePath $command.Source -ArgumentList $commandLine `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    Write-Host "MX Master Tweaker is running - look for the mouse icon in the notification area." -ForegroundColor Green
}
