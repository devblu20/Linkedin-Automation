param([switch]$DisableAutoStart, [switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLauncher = Join-Path $taskRoot 'scripts\worker-launcher.pyw'
$taskCandidates = @(
    (Join-Path $taskRoot '.venv\Scripts\pythonw.exe'),
    (Join-Path (Split-Path -Parent $taskRoot) '.venv\Scripts\pythonw.exe')
)
$taskPython = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $taskPython -or -not (Test-Path -LiteralPath $taskLauncher -PathType Leaf)) {
    throw 'Use this setup from the installed LinkedIn project with its Python environment.'
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.env.railway.local') -PathType Leaf)) {
    throw 'Configure the private dashboard credentials file before setting up the worker.'
}
$taskStartup = [Environment]::GetFolderPath('Startup')
if (-not $taskStartup -or -not [IO.Path]::IsPathRooted($taskStartup)) {
    throw 'Cannot locate the current user Windows Startup folder.'
}
$taskShortcut = Join-Path $taskStartup 'BluQQ LinkedIn Worker.lnk'
$taskShell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $taskShortcut) {
    $taskOld = $taskShell.CreateShortcut($taskShortcut)
    if ($taskOld.Arguments -ne ('"{0}"' -f $taskLauncher)) {
        throw 'A different shortcut already uses this name. It has not been changed.'
    }
}
if ($DisableAutoStart) {
    if (Test-Path -LiteralPath $taskShortcut) { Remove-Item -LiteralPath $taskShortcut }
    Write-Output 'LinkedIn worker automatic start disabled. Dashboard start link is still available.'
    return
}

# A fixed per-user protocol launches only this helper. It deliberately takes no
# URL arguments: a web link cannot inject a command, change account or select a job.
$taskProtocol = 'HKCU:\Software\Classes\bluqq-linkedin-worker'
if (Test-Path -LiteralPath $taskProtocol) {
    $taskOwner = (Get-ItemProperty -LiteralPath $taskProtocol -ErrorAction Stop).BluQQInstallRoot
    if ($taskOwner -ne $taskRoot) { throw 'The worker URL handler belongs to a different installation.' }
}
New-Item -Path $taskProtocol -Force | Out-Null
Set-Item -LiteralPath $taskProtocol -Value 'URL:BluQQ LinkedIn Worker'
New-ItemProperty -LiteralPath $taskProtocol -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $taskProtocol -Name 'BluQQInstallRoot' -Value $taskRoot -PropertyType String -Force | Out-Null
$taskCommand = Join-Path $taskProtocol 'shell\open\command'
New-Item -Path $taskCommand -Force | Out-Null
Set-Item -LiteralPath $taskCommand -Value ('"{0}" "{1}"' -f $taskPython, $taskLauncher)

$taskLink = $taskShell.CreateShortcut($taskShortcut)
$taskLink.TargetPath = $taskPython
$taskLink.Arguments = '"{0}"' -f $taskLauncher
$taskLink.WorkingDirectory = $taskRoot
$taskLink.Description = 'Keep the LinkedIn browser worker available after Windows sign-in.'
$taskLink.Save()
Write-Output 'LinkedIn worker setup complete: start at Windows sign-in and restart after worker exits.'
Write-Output 'Dashboard Start worker link opens this fixed helper; no credentials are stored in the link.'
Write-Output 'Keep this computer awake. Windows sign-in and LinkedIn verification remain manual.'
if (-not $NoLaunch) { & (Join-Path $PSScriptRoot 'start-browser-worker.ps1') }
