param(
  [string]$OutFile = "data\nc_uploads\hycom_luzon_uv_surface_20240905.nc"
)

$ErrorActionPreference = "Stop"

$url = "https://ncss.hycom.org/thredds/ncss/grid/GLBy0.08/expt_93.0/uv3z?var=water_u&var=water_v&north=26&west=117&east=127&south=18&disableProjSubset=on&horizStride=3&time=2024-09-05T09%3A00%3A00Z&vertCoord=0&accept=netcdf4"
$outDir = Split-Path -Parent $OutFile
if ($outDir) {
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

curl.exe -L $url -o $OutFile
Get-Item $OutFile | Select-Object FullName, Length, LastWriteTime
