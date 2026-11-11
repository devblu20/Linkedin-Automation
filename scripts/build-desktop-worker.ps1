param([switch]$SkipSelfTest)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path (Split-Path -Parent $taskRoot) '.venv\Scripts\python.exe'
$taskBundle = Join-Path $taskRoot 'data\portable312-build'
$taskCache = Join-Path $taskRoot 'data\portable-downloads'
New-Item -ItemType Directory -Path $taskCache -Force | Out-Null
$taskRuntimeZip = Join-Path $taskCache 'python312.zip'
if (-not (Test-Path -LiteralPath $taskRuntimeZip)) {
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $taskRuntimeZip
}
Expand-Archive -LiteralPath $taskRuntimeZip -DestinationPath "$taskBundle\runtime" -Force
$taskSignature = Get-AuthenticodeSignature "$taskBundle\runtime\python.exe"
if ($taskSignature.Status -ne 'Valid' -or $taskSignature.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
    throw 'Official runtime publisher verification failed'
}
Set-Content -LiteralPath "$taskBundle\runtime\python312._pth" -Encoding ascii -Value @('python312.zip', '.', '..\lib', '..\src')
& $taskPython "$taskRoot\scripts\copy-worker-dependencies.py" "$taskBundle\lib"
if ($LASTEXITCODE -ne 0) { throw 'Portable dependency build failed' }
New-Item -ItemType Directory -Path "$taskBundle\src", "$taskBundle\scripts", "$taskBundle\config" -Force | Out-Null
Copy-Item -LiteralPath "$taskRoot\src\linkedin_automation" -Destination "$taskBundle\src" -Recurse -Force
Copy-Item -LiteralPath "$taskRoot\scripts\desktop-worker.py" -Destination "$taskBundle\scripts\desktop-worker.py" -Force
foreach ($taskName in @('search.example.yaml', 'bluqq-london-independent-traders.yaml', 'bluqq-london-prop-family-offices.yaml', 'bluqq-london-small-funds-trading-teams.yaml')) {
    Copy-Item -LiteralPath "$taskRoot\config\$taskName" -Destination "$taskBundle\config" -Force
}
Set-Content -LiteralPath "$taskBundle\Start BluQQ Worker.cmd" -Encoding ascii -Value @('@echo off', 'cd /d "%~dp0"', '"runtime\python.exe" "scripts\desktop-worker.py"', 'if errorlevel 1 pause')
Set-Content -LiteralPath "$taskBundle\README.txt" -Encoding utf8 -Value @('BluQQ LinkedIn Worker - Windows 10/11 x64 with Chrome', 'Extract the ZIP and double-click Start BluQQ Worker.cmd.', 'Keep the helper running and the computer awake.', 'Sign in with your approved app email on the connection page. Check your PC name, then connect.', 'Use that same browser for Search and fetch. LinkedIn login appears locally when needed.', 'For a different browser or account, press Enter in the helper to generate a new pairing code.', 'The helper registers its own Start link and starts at Windows sign-in for this Windows user.', 'No API keys, owner credentials or LinkedIn cookies are bundled.', 'Close the helper window to stop this helper and its workers.', 'Mac/Linux are not supported in this package.')
if (-not $SkipSelfTest) {
    & "$taskBundle\runtime\python.exe" "$taskBundle\scripts\desktop-worker.py" --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Portable helper self-test failed' }
}
$taskDownloads = Join-Path $taskRoot 'downloads'
New-Item -ItemType Directory -Path $taskDownloads -Force | Out-Null
& $taskPython "$taskRoot\scripts\zip-desktop-worker.py" $taskBundle "$taskDownloads\BluQQ-Worker.zip"
if ($LASTEXITCODE -ne 0) { throw 'Portable archive validation failed' }
Get-FileHash -LiteralPath "$taskDownloads\BluQQ-Worker.zip" -Algorithm SHA256 | Select-Object Algorithm,Hash
