param([switch]$RegisterOnly)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path (Split-Path -Parent $taskRoot) '.venv\Scripts\pythonw.exe'
$taskLauncher = Join-Path $PSScriptRoot 'personal-browser-worker.pyw'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) { throw 'Project Python is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.env.railway.local'))) {
    throw 'Existing private admin credentials are missing.'
}
$taskProtocols = @{
    'bluqq-linkedin-worker' = ('"{0}" "{1}"' -f $taskPython, $taskLauncher)
    'bluqq-linkedin-delivery' = ('"{0}" "{1}" --delivery' -f $taskPython, $taskLauncher)
}
foreach ($taskName in $taskProtocols.Keys) {
    $taskProtocol = Join-Path 'HKCU:\Software\Classes' $taskName
    if (Test-Path -LiteralPath $taskProtocol) {
        $taskOwner = (Get-ItemProperty -LiteralPath $taskProtocol).BluQQInstallRoot
        if ($taskOwner -ne $taskRoot) { throw 'This link belongs to another installation.' }
    }
}
foreach ($taskName in $taskProtocols.Keys) {
    $taskProtocol = Join-Path 'HKCU:\Software\Classes' $taskName
    New-Item -Path $taskProtocol -Force | Out-Null
    Set-Item -LiteralPath $taskProtocol -Value 'URL:BluQQ LinkedIn Worker'
    New-ItemProperty -LiteralPath $taskProtocol -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $taskProtocol -Name 'BluQQInstallRoot' -Value $taskRoot -PropertyType String -Force | Out-Null
    $taskCommand = Join-Path $taskProtocol 'shell\open\command'
    New-Item -Path $taskCommand -Force | Out-Null
    Set-Item -LiteralPath $taskCommand -Value $taskProtocols[$taskName]
}
$taskStartup = Join-Path ([Environment]::GetFolderPath('Startup')) 'BluQQ LinkedIn Worker.lnk'
if (Test-Path -LiteralPath $taskStartup) {
    $taskShell = New-Object -ComObject WScript.Shell
    $taskLink = $taskShell.CreateShortcut($taskStartup)
    $taskOldLauncher = Join-Path $PSScriptRoot 'worker-launcher.pyw'
    if ($taskLink.Arguments -eq ('"{0}"' -f $taskOldLauncher) -or
        $taskLink.Arguments -eq ('"{0}"' -f $taskLauncher)) {
        $taskLink.TargetPath = $taskPython
        $taskLink.Arguments = '"{0}"' -f $taskLauncher
        $taskLink.WorkingDirectory = $taskRoot
        $taskLink.Save()
    } else { throw 'Existing startup shortcut belongs to another installation.' }
}
if ($RegisterOnly) {
    Write-Output 'Personal browser start and delivery links registered.'
} else {
    Start-Process -FilePath $taskPython -ArgumentList ('"{0}"' -f $taskLauncher) -WorkingDirectory $taskRoot -WindowStyle Hidden
    Write-Output 'Personal browser helper started. No search or message was created.'
}
