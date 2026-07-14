param(
    [switch]$IncludeRagflow
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $PSScriptRoot

$env:DOCKER_BUILDKIT = "0"
$env:COMPOSE_DOCKER_CLI_BUILD = "1"

$argsList = @("compose", "-f", (Join-Path $DeployDir "docker-compose.yml"), "--profile", "mcp")
if ($IncludeRagflow) {
    $argsList += @("--profile", "ragflow")
}
$argsList += @("build", "backend", "geo-api", "mcp-server")

& docker @argsList
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
