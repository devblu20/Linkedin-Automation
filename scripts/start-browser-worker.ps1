param(
    [ValidatePattern('^https://[a-zA-Z0-9.-]+/?$')]
    [string]$DashboardUrl = 'https://linkedin-automation-production-7964.up.railway.app',
    [string]$CredentialsFile = '.env.railway.local',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskCredentials = if ([IO.Path]::IsPathRooted($CredentialsFile)) {
    $CredentialsFile
} else { Join-Path $taskRoot $CredentialsFile }
if (-not (Test-Path -LiteralPath $taskCredentials -PathType Leaf)) {
    throw 'Dashboard credentials file is missing. Keep its contents private.'
}
if ($taskCredentials.Contains('"')) { throw 'Invalid credentials file path' }
if (-not $PythonPath) {
    $taskCandidates = @(
        (Join-Path $taskRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path -Parent $taskRoot) '.venv\Scripts\python.exe')
    )
    $PythonPath = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Pass -PythonPath with the Python executable where this project is installed.'
}
$taskLauncher = Join-Path $taskRoot 'scripts\worker-launcher.pyw'
$taskPythonW = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $taskPythonW -PathType Leaf) { $PythonPath = $taskPythonW }
$taskExisting = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($taskLauncher) }
if ($taskExisting) {
    Write-Output 'The LinkedIn worker helper is already running; it will keep the worker available.'
    return
}
$taskLogs = Join-Path $taskRoot 'data\worker-logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskOutput = Join-Path $taskLogs "$taskStamp.out.log"
$taskError = Join-Path $taskLogs "$taskStamp.err.log"
$taskEnv = @{
    LINKEDIN_AUTOMATION_DASHBOARD_URL = $DashboardUrl.TrimEnd('/')
    LINKEDIN_AUTOMATION_DASHBOARD_CREDENTIALS_FILE = $taskCredentials
}
$taskPrevious = @{}
try {
    foreach ($taskKey in $taskEnv.Keys) {
        $taskPrevious[$taskKey] = [Environment]::GetEnvironmentVariable($taskKey, 'Process')
        [Environment]::SetEnvironmentVariable($taskKey, $taskEnv[$taskKey], 'Process')
    }
    $taskArguments = '"{0}"' -f $taskLauncher
    $taskProcess = Start-Process -FilePath $PythonPath -ArgumentList $taskArguments `
        -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $taskOutput -RedirectStandardError $taskError
    Write-Output "LinkedIn worker helper started (PID $($taskProcess.Id))."
    Write-Output "Local worker log: $taskOutput"
    Write-Output 'Keep this computer awake and connected. LinkedIn verification still requires you.'
} finally {
    foreach ($taskKey in $taskPrevious.Keys) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskPrevious[$taskKey], 'Process')
    }
}
