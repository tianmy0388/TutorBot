param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3010
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs\dev"
$PidFile = Join-Path $LogDir "pids.json"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env"))) {
  Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination (Join-Path $Root ".env")
  Write-Host "Created .env from .env.example. Fill API keys before live generation." -ForegroundColor Yellow
}

if (Test-Path -LiteralPath $PidFile) {
  $old = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
  $alive = @($old.backend, $old.frontend) | Where-Object {
    $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)
  }
  if ($alive.Count -gt 0) {
    Write-Host "TutorBot dev services already appear to be running. Use scripts\stop-dev.ps1 first." -ForegroundColor Yellow
    Write-Host "Frontend: http://localhost:$FrontendPort"
    Write-Host "Backend : http://localhost:$BackendPort/api/v1/health"
    exit 0
  }
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = "python"
}

$PowerShellExe = (Get-Process -Id $PID).Path

function Start-DevProcess {
  param(
    [string]$Name,
    [string]$CommandText,
    [string]$OutLog,
    [string]$ErrLog
  )
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($CommandText))
  Start-Process `
    -FilePath $PowerShellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru
}

$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$BackendOut = Join-Path $LogDir "backend.out.log"
$BackendErr = Join-Path $LogDir "backend.err.log"
$FrontendOut = Join-Path $LogDir "frontend.out.log"
$FrontendErr = Join-Path $LogDir "frontend.err.log"

$backendCommand = @"
Set-Location -LiteralPath '$BackendDir'
`$env:PYTHONPATH='.'
`$env:TUTOR_PORT='$BackendPort'
& '$Python' -m tutor api
"@

$frontendCommand = @"
Set-Location -LiteralPath '$FrontendDir'
`$env:BACKEND_PORT='$BackendPort'
npx next dev -p $FrontendPort
"@

$backend = Start-DevProcess -Name "backend" -CommandText $backendCommand -OutLog $BackendOut -ErrLog $BackendErr
$frontend = Start-DevProcess -Name "frontend" -CommandText $frontendCommand -OutLog $FrontendOut -ErrLog $FrontendErr

@{
  backend = $backend.Id
  frontend = $frontend.Id
  backend_port = $BackendPort
  frontend_port = $FrontendPort
  started_at = (Get-Date).ToString("o")
  logs = @{
    backend_out = $BackendOut
    backend_err = $BackendErr
    frontend_out = $FrontendOut
    frontend_err = $FrontendErr
  }
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host "TutorBot dev services started." -ForegroundColor Green
Write-Host "Frontend: http://localhost:$FrontendPort"
Write-Host "Backend health: http://localhost:$BackendPort/api/v1/health"
Write-Host "Logs: $LogDir"
