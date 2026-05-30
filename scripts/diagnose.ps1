param(
  [switch]$Deep,
  [switch]$SkipDocker,
  [switch]$SkipRagFlow
)

$ErrorActionPreference = "Continue"
$script:Failures = 0
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Write-Section {
  param([string]$Title)
  Write-Host ""
  Write-Host ("== {0} ==" -f $Title)
}

function Add-Failure {
  param([string]$Name, [string]$Message)
  $script:Failures += 1
  Write-Host ("[FAIL] {0} -> {1}" -f $Name, $Message)
}

function Test-CommandAvailable {
  param([string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    Add-Failure $Name "command not found"
    return $false
  }
  Write-Host ("[OK]   {0} command available" -f $Name)
  return $true
}

function Load-DotEnv {
  param([string]$Path)
  $map = @{}
  if (-not (Test-Path $Path)) { return $map }
  Get-Content -Encoding UTF8 $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
    $idx = $line.IndexOf("=")
    $key = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    if ($key) { $map[$key] = $value }
  }
  return $map
}

function Test-Endpoint {
  param(
    [string]$Name,
    [string]$Url,
    [string]$Method = "GET",
    [string]$Body = "",
    [hashtable]$Headers = @{},
    [int]$TimeoutSec = 30,
    [scriptblock]$Validate = $null
  )
  try {
    if ($Method -eq "POST") {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Method Post -ContentType "application/json; charset=utf-8" -Body $Body -Headers $Headers -TimeoutSec $TimeoutSec
    } else {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec $TimeoutSec
    }
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      Add-Failure $Name ("HTTP {0}" -f $response.StatusCode)
      return $null
    }
    $json = $null
    try { $json = $response.Content | ConvertFrom-Json } catch {}
    if ($Validate -and -not (& $Validate $json $response)) {
      Add-Failure $Name "response validation failed"
      return $json
    }
    Write-Host ("[OK]   {0} -> {1}" -f $Name, $response.StatusCode)
    return $json
  } catch {
    Add-Failure $Name $_.Exception.Message
    return $null
  }
}

Set-Location $Root

Write-Section "Prerequisites"
$dockerOk = Test-CommandAvailable "docker"
$composeOk = $false
if ($dockerOk) {
  try {
    docker compose version | Out-Null
    $composeOk = $true
    Write-Host "[OK]   docker compose available"
  } catch {
    Add-Failure "docker compose" $_.Exception.Message
  }
}

if (-not $SkipDocker -and $dockerOk -and $composeOk) {
  Write-Section "Main Docker Stack"
  docker compose ps
  try {
    docker compose config --quiet
    Write-Host "[OK]   docker-compose.yml is valid"
  } catch {
    Add-Failure "docker compose config" $_.Exception.Message
  }
}

if (-not $SkipRagFlow -and $dockerOk -and $composeOk -and (Test-Path "ragflow_stack\docker-compose.yml")) {
  Write-Section "RAGFlow Docker Stack"
  docker compose -f ragflow_stack\docker-compose.yml --env-file ragflow_stack\.env ps
}

Write-Section "HTTP Endpoints"
$null = Test-Endpoint "Demo Web" "http://127.0.0.1:8000/"
$null = Test-Endpoint "Backend Health" "http://127.0.0.1:8000/api/health" -Validate { param($j, $r) $j -and $j.status -eq "ok" }
$null = Test-Endpoint "Project Status" "http://127.0.0.1:8000/api/project/status" -Validate { param($j, $r) $j -and $j.status -eq "ok" }
$null = Test-Endpoint "RAG Status" "http://127.0.0.1:8000/api/rag/status" -Validate { param($j, $r) $j -and $j.local.document_count -ge 1 }
$null = Test-Endpoint "Ocean Datasets" "http://127.0.0.1:8000/api/ocean/datasets" -Validate { param($j, $r) $j -and $j.datasets.Count -ge 1 }
$null = Test-Endpoint "GeoAgent Health" "http://127.0.0.1:8000/geo-api/api/health" -Validate { param($j, $r) $j -and $j.status -eq "ok" }
$null = Test-Endpoint "Geo Domains" "http://127.0.0.1:8000/geo-api/api/geo/domains" -Validate { param($j, $r) $j -and $j.domains.Count -ge 4 }
$null = Test-Endpoint "GeoServer" "http://127.0.0.1:8081/geoserver/web/"

if (-not $SkipRagFlow) {
  $null = Test-Endpoint "RAGFlow Web" "http://127.0.0.1:8088/"
  $envMap = Load-DotEnv (Join-Path $Root ".env")
  $apiKey = $envMap["RAGFLOW_API_KEY"]
  if ($apiKey) {
    $headers = @{ "Authorization" = "Bearer $apiKey" }
    $null = Test-Endpoint "RAGFlow Datasets API" "http://127.0.0.1:9380/api/v1/datasets" -Headers $headers -Validate { param($j, $r) $j -and ($j.code -eq 0 -or $j.data) }
  } else {
    Write-Host "[SKIP] RAGFlow Datasets API -> RAGFLOW_API_KEY not set in .env"
  }
}

Write-Section "Agent Smoke Tests"
$localPayload = @{
  question = "marine heatwave coral reef risk"
  backend = "local"
  top_k = 2
  max_revisions = 0
  trace = $true
} | ConvertTo-Json -Depth 8
$null = Test-Endpoint "Agent Report Local" "http://127.0.0.1:8000/api/agents/report" "POST" $localPayload -TimeoutSec 60 -Validate { param($j, $r) $j -and $j.report -and $j.backend -eq "local" }

$ragStatus = Test-Endpoint "RAG Status Recheck" "http://127.0.0.1:8000/api/rag/status"
if ($ragStatus -and $ragStatus.ragflow.configured) {
  $ragPayload = @{
    question = "marine heatwave coral reef risk"
    backend = "ragflow"
    top_k = 2
    max_revisions = 0
    trace = $true
  } | ConvertTo-Json -Depth 8
  $null = Test-Endpoint "Agent Report RAGFlow" "http://127.0.0.1:8000/api/agents/report" "POST" $ragPayload -TimeoutSec 90 -Validate { param($j, $r) $j -and $j.backend -eq "ragflow" -and $j.kept_documents.Count -ge 1 }
} else {
  Write-Host "[SKIP] Agent Report RAGFlow -> RAGFlow not configured"
}

$geoPayload = @{
  question = "navigation risk around Taiwan"
  domain = "navigation"
  backend = "local"
  bbox = @{ west = 119; east = 123; south = 21; north = 26 }
  top_k = 2
  max_revisions = 0
  trace = $true
} | ConvertTo-Json -Depth 8
$null = Test-Endpoint "GeoAgent Report" "http://127.0.0.1:8000/geo-api/api/geo/report" "POST" $geoPayload -TimeoutSec 90 -Validate { param($j, $r) $j -and $j.report -and $j.domain -eq "navigation" }

if ($Deep) {
  Write-Section "Deep Checks"
  $litPayload = @{ domain = "marine"; max = 1; queries_per_domain = 1; delay = 0; timeout = 20 } | ConvertTo-Json
  $null = Test-Endpoint "Geo Literature Fetch" "http://127.0.0.1:8000/geo-api/api/geo/fetch-literature" "POST" $litPayload -TimeoutSec 120 -Validate { param($j, $r) $j -and $j.status -eq "ok" }
}

Write-Section "Summary"
if ($script:Failures -eq 0) {
  Write-Host "[OK]   all checks passed"
  exit 0
}

Write-Host ("[FAIL] {0} check(s) failed" -f $script:Failures)
exit 1
