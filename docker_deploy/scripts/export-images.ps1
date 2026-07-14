param(
    [switch]$IncludeRagflow
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $PSScriptRoot
$OutputDir = Join-Path $DeployDir "image-tars"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$env:DOCKER_BUILDKIT = "0"
$env:COMPOSE_DOCKER_CLI_BUILD = "1"

$composeArgs = @("compose", "-f", (Join-Path $DeployDir "docker-compose.yml"), "--profile", "mcp")
if ($IncludeRagflow) {
    $composeArgs += @("--profile", "ragflow")
}

& docker @($composeArgs + @("build", "backend", "geo-api", "mcp-server"))
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$pullServices = @("nginx", "geoserver")
if ($IncludeRagflow) {
    $pullServices += @("es01", "mysql", "minio", "redis", "ragflow-cpu")
}

foreach ($service in $pullServices) {
    & docker @($composeArgs + @("pull", $service))
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pull failed for service '$service'. Continuing; local image inspection will verify availability."
    }
}

$stackVersion = if ($env:STACK_VERSION) { $env:STACK_VERSION } else { "8.11.3" }
$ragflowImage = if ($env:RAGFLOW_IMAGE) { $env:RAGFLOW_IMAGE } else { "infiniflow/ragflow:v0.25.3" }

$images = @(
    "ocean-rag-agent/backend:latest",
    "ocean-rag-agent/geo-api:latest",
    "ocean-rag-agent/mcp-server:latest",
    "nginx:1.27-alpine",
    "docker.osgeo.org/geoserver:3.0.x"
)

if ($IncludeRagflow) {
    $images += @(
        "elasticsearch:$stackVersion",
        "mysql:8.0.39",
        "pgsty/minio:RELEASE.2026-03-25T00-00-00Z",
        "valkey/valkey:8",
        $ragflowImage
    )
}

$missing = @()
foreach ($image in $images) {
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        $missing += $image
    }
}

if ($missing.Count -gt 0) {
    Write-Error ("Missing images: " + ($missing -join ", "))
    exit 1
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tarPath = Join-Path $OutputDir "ocean-rag-agent-images-$stamp.tar"
& docker save -o $tarPath @images
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Saved image bundle: $tarPath"
