<#
.SYNOPSIS
  把 ragflow/volumes/*.tar.gz 恢复到 docker-compose 期望的绑定目录。
  在目标机器上、启动 RAGFlow 之前运行一次。

  数据来源：源机器 ragflow_stack_* 命名卷的快照。
  恢复目标：docker_deploy/ragflow/data/{elasticsearch,mysql,minio,redis} 与 ragflow/logs。
  用临时 alpine 容器解压，保留 Linux 文件权限（ES/MySQL 对权限敏感）。
#>
$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $PSScriptRoot
$VolDir = Join-Path $DeployDir "ragflow\volumes"

$map = @(
    @{ tar = "esdata01.tar.gz";     dest = "ragflow\data\elasticsearch" },
    @{ tar = "mysql_data.tar.gz";   dest = "ragflow\data\mysql" },
    @{ tar = "minio_data.tar.gz";   dest = "ragflow\data\minio" },
    @{ tar = "redis_data.tar.gz";   dest = "ragflow\data\redis" },
    @{ tar = "ragflow_logs.tar.gz"; dest = "ragflow\logs" }
)

foreach ($m in $map) {
    $tarPath = Join-Path $VolDir $m.tar
    $destPath = Join-Path $DeployDir $m.dest
    if (-not (Test-Path $tarPath)) {
        Write-Warning "缺少 $($m.tar)，跳过"
        continue
    }
    New-Item -ItemType Directory -Force -Path $destPath | Out-Null
    Write-Host "恢复 $($m.tar) -> $($m.dest) ..."
    $destVol = "${destPath}:/dest"
    $backupVol = "${VolDir}:/backup:ro"
    & docker run --rm -v $destVol -v $backupVol alpine sh -c "rm -rf /dest/* 2>/dev/null; tar xzf /backup/$($m.tar) -C /dest"
    if ($LASTEXITCODE -ne 0) { Write-Error "恢复 $($m.tar) 失败"; exit 1 }
}

Write-Host ""
Write-Host "RAGFlow 数据恢复完成。现在可以启动完整栈："
Write-Host "  docker compose --profile ragflow up -d"
