<#
.SYNOPSIS
  在【源机器】把正在使用的 RAGFlow 命名卷快照导出为 ragflow/volumes/*.tar.gz。
  默认源是 ragflow_stack/ compose 创建的命名卷 ragflow_stack_*。
  导出前建议先停掉 RAGFlow 容器，得到一致性快照（DB 不在写入中）。

  用 -SourcePrefix 可改命名卷前缀（如换成 ocean-rag-agent 自身的卷）。
#>
param(
    [string]$SourcePrefix = "ragflow_stack"
)
$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $PSScriptRoot
$VolDir = Join-Path $DeployDir "ragflow\volumes"
New-Item -ItemType Directory -Force -Path $VolDir | Out-Null

# 命名卷后缀 -> 输出文件名
$vols = @("esdata01", "mysql_data", "minio_data", "redis_data", "ragflow_logs")

foreach ($v in $vols) {
    $srcVol = "${SourcePrefix}_${v}"
    $exists = & docker volume inspect $srcVol 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "命名卷 $srcVol 不存在，跳过"
        continue
    }
    $outFile = Join-Path $VolDir "$v.tar.gz"
    Write-Host "导出 $srcVol -> ragflow\volumes\$v.tar.gz ..."
    # tar 输出到 stdout，由宿主机重定向写文件（避免 Windows 路径挂载问题）
    $mount = "${srcVol}:/src:ro"
    & cmd /c "docker run --rm -v $mount alpine tar czf - -C /src . > `"$outFile`""
    if ($LASTEXITCODE -ne 0) { Write-Error "导出 $srcVol 失败"; exit 1 }
}

Write-Host ""
Write-Host "导出完成，文件在: $VolDir"
Get-ChildItem $VolDir | Format-Table Name, @{N="Size(MB)";E={[math]::Round($_.Length/1MB,1)}}
