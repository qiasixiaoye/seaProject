param(
    [string]$TarPath
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $PSScriptRoot

if (-not $TarPath) {
    $latest = Get-ChildItem -Path (Join-Path $DeployDir "image-tars") -Filter "*.tar" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latest) {
        Write-Error "No tar file found in image-tars. Pass -TarPath explicitly."
        exit 1
    }
    $TarPath = $latest.FullName
}

& docker load -i $TarPath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Loaded image bundle: $TarPath"
