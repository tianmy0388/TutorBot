$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs\dev"
$PidFile = Join-Path $LogDir "pids.json"

function Stop-ProcessTree {
  param([int]$ProcessId)
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($proc) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped PID $ProcessId"
  }
}

if (-not (Test-Path -LiteralPath $PidFile)) {
  Write-Host "No TutorBot dev PID file found at $PidFile"
  exit 0
}

$pids = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
foreach ($pidValue in @($pids.frontend, $pids.backend)) {
  if ($pidValue) {
    Stop-ProcessTree -ProcessId ([int]$pidValue)
  }
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "TutorBot dev services stopped." -ForegroundColor Green
