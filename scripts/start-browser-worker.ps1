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
$taskExisting = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match '-m linkedin_automation worker' -and
        $_.CommandLine.Contains($DashboardUrl.TrimEnd('/')) }
if ($taskExisting) {
    Write-Output 'The browser worker for this dashboard is already running.'
    return
}
$taskLogs = Join-Path $taskRoot 'data\worker-logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskOutput = Join-Path $taskLogs "$taskStamp.out.log"
$taskError = Join-Path $taskLogs "$taskStamp.err.log"
$taskEnv = @{
    PYTHONPATH = (Join-Path $taskRoot 'src')
    LINKEDIN_AUTOMATION_BROWSER_DATA = (Join-Path $taskRoot 'browser-data-chrome')
    LINKEDIN_AUTOMATION_BROWSER_CHANNEL = 'chrome'
    LINKEDIN_AUTOMATION_DATABASE_URL = 'sqlite:///data/local-research.sqlite3'
}
$taskPrevious = @{}
try {
    foreach ($taskKey in $taskEnv.Keys) {
        $taskPrevious[$taskKey] = [Environment]::GetEnvironmentVariable($taskKey, 'Process')
        [Environment]::SetEnvironmentVariable($taskKey, $taskEnv[$taskKey], 'Process')
    }
    $taskArguments = '-m linkedin_automation worker --url {0} --credentials-file "{1}"' -f $DashboardUrl, $taskCredentials
    $taskProcess = Start-Process -FilePath $PythonPath -ArgumentList $taskArguments `
        -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $taskOutput -RedirectStandardError $taskError
    Write-Output "LinkedIn browser worker started (PID $($taskProcess.Id))."
    Write-Output "Local worker log: $taskOutput"
    Write-Output 'Keep this computer awake and connected. LinkedIn verification still requires you.'
} finally {
    foreach ($taskKey in $taskPrevious.Keys) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskPrevious[$taskKey], 'Process')
    }
}
