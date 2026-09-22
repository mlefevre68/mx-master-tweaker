#Requires -Version 5.1
<#
.SYNOPSIS
    One-time setup for MX Master Tweaker.
.DESCRIPTION
    There is nothing to install: the app uses only the Python standard library, so this
    script just checks that Python is usable, makes a desktop shortcut, and optionally
    registers the app to start when you sign in.

    No administrator rights are needed and nothing is downloaded.
.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -AutoStart
#>
[CmdletBinding()]
param(
    # Also register the app to start when you sign in to Windows.
    [switch]$AutoStart,

    # Skip the desktop shortcut.
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Info($text) { Write-Host "  $text" -ForegroundColor Gray }

Write-Host "MX Master Tweaker - setup" -ForegroundColor White

# --- 1. Windows ------------------------------------------------------------
if ($env:OS -ne "Windows_NT") {
    throw "This app talks to the Windows input APIs directly and only runs on Windows."
}

# --- 2. Python -------------------------------------------------------------
Write-Step "Checking Python"
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
    throw @"
Python was not found on PATH.

Install it for your user account - no administrator rights needed - from
https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH",
then run this script again.
"@
}

$version = (& $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ([version]$version -lt [version]"3.10") {
    throw "Python $version was found, but 3.10 or newer is needed."
}
Write-Ok "Python $version at $($python.Source)"

& $python.Source -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw @"
This Python has no tkinter, so the settings window cannot open.
Re-run the Python installer and make sure "tcl/tk and IDLE" is selected.
"@
}
Write-Ok "tkinter is available"

Write-Info "No packages to install - the app uses only the standard library."

# --- 3. A quick self-test --------------------------------------------------
Write-Step "Checking the app"
& $python.Source -m unittest discover -s tests -q 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "The self-test did not pass. The app may still work; see the output of:"
    Write-Warning "    python -m unittest discover -s tests"
}
else {
    Write-Ok "Self-test passed"
}

# --- 4. Desktop shortcut ---------------------------------------------------
if (-not $NoShortcut) {
    Write-Step "Creating a desktop shortcut"
    $pythonw = Join-Path (Split-Path $python.Source) "pythonw.exe"
    if (-not (Test-Path $pythonw)) { $pythonw = $python.Source }
    $target = Join-Path ([Environment]::GetFolderPath("Desktop")) "MX Master Tweaker.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($target)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$(Join-Path $PSScriptRoot 'startup.pyw')`" --settings"
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = "$env:SystemRoot\System32\main.cpl,0"
    $shortcut.Description = "Personalise the MX Master buttons"
    $shortcut.Save()
    Write-Ok "Shortcut created: $target"
}

# --- 5. Auto-start ---------------------------------------------------------
if ($AutoStart) {
    Write-Step "Setting the app to start when you sign in"
    & (Join-Path $PSScriptRoot "install-startup.ps1")
}

# --- 6. Done ---------------------------------------------------------------
Write-Step "Setup complete"
Write-Host @"
  Start it:   .\run.ps1 -Settings   (or double-click the desktop shortcut)
  It lives in the notification area - click the mouse icon to open the settings.

  Everything happens on this machine. No account, no service, no network access.
"@ -ForegroundColor Green

if (-not $AutoStart) {
    Write-Info "To start it automatically at sign-in:  .\install-startup.ps1"
    Write-Info "or tick 'Start when I sign in to Windows' in the settings window."
}
