param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Install the development environment first with: uv sync --group dev'
}
if (-not $env:DJANGOOPS_WEB_DB) {
    $env:DJANGOOPS_WEB_DB = Join-Path $projectRoot '.venv\djangoops-local.sqlite3'
}
$env:DJANGOOPS_WEB_DEBUG = '1'
$env:DJANGOOPS_WEB_DEMO = '1'
Push-Location $projectRoot
try {
    & $projectPython manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Database migrations failed.' }
    & $projectPython manage.py runserver "127.0.0.1:$Port" --noreload
    if ($LASTEXITCODE -ne 0) { throw 'The local server exited with an error.' }
} finally {
    Pop-Location
}
