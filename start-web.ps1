$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonArgs = @("scripts/start_web.py") + $args

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 -c "import sys" *> $null
    if ($LASTEXITCODE -eq 0) {
        & py -3.11 @pythonArgs
        exit $LASTEXITCODE
    }
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python @pythonArgs
    exit $LASTEXITCODE
}

Write-Error "[TransitScholar] Python 3.11+ was not found. Install Python 3.11 or newer and run again."
exit 1
