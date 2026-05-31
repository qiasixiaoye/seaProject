from __future__ import annotations

import bisect
import json
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("OCEAN_DATA_DIR", ROOT / "data"))
SAMPLE_NC = DATA_DIR / "sample_ocean.nc"
NC_UPLOAD_DIR = DATA_DIR / "nc_uploads"
LAND_MASK_NC = NC_UPLOAD_DIR / "etopo2022_taiwan_30s_bathy.nc"
_LAND_MASK_CACHE: dict[str, Any] = {}

NC_DIMENSION = 10
NC_VARIABLE = 11
NC_ATTRIBUTE = 12
NC_CHAR = 2
NC_SHORT = 3
NC_INT = 4
NC_FLOAT = 5
NC_DOUBLE = 6
TYPE_SIZES = {NC_CHAR: 1, NC_SHORT: 2, NC_INT: 4, NC_FLOAT: 4, NC_DOUBLE: 8}


@dataclass
class NcVariable:
    name: str
    dimids: list[int]
    attrs: dict[str, Any]
    nc_type: int
    values: list[float]


def ensure_sample_nc() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    NC_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if SAMPLE_NC.exists():
        return SAMPLE_NC
    lats = [round(-80 + i * 4, 3) for i in range(41)]
    lons = [round(-180 + i * 4, 3) for i in range(91)]
    variables: dict[str, list[float]] = {"sst": [], "salinity": [], "chlorophyll": []}
    for lat in lats:
        for lon in lons:
            rad_lat = math.radians(lat)
            rad_lon = math.radians(lon)
            temp = 27 - 0.22 * abs(lat) + 1.8 * math.sin(2 * rad_lon) * math.cos(rad_lat)
            sal = 34.7 + 0.8 * math.cos(rad_lat) - 0.35 * math.sin(rad_lon)
            chl = 0.08 + 0.55 * math.exp(-((abs(lat) - 42) ** 2) / 260) + 0.08 * (1 + math.sin(3 * rad_lon))
            variables["sst"].append(round(temp, 4))
            variables["salinity"].append(round(sal, 4))
            variables["chlorophyll"].append(round(chl, 4))
    _write_classic_nc(
        SAMPLE_NC,
        lats,
        lons,
        variables,
        {
            "sst": {"units": "degC", "long_name": "Synthetic sea surface temperature"},
            "salinity": {"units": "psu", "long_name": "Synthetic sea surface salinity"},
            "chlorophyll": {"units": "mg m-3", "long_name": "Synthetic chlorophyll concentration"},
        },
    )
    return SAMPLE_NC


def dataset_summary() -> dict[str, Any]:
    ensure_sample_nc()
    datasets = []
    for path in _iter_nc_files():
        try:
            datasets.append(_summary_netcdf4(path))
        except Exception as exc:
            try:
                datasets.append(_summary_classic(path))
            except Exception as classic_exc:
                datasets.append(
                    {
                        "id": path.stem,
                        "path": str(path),
                        "error": f"{type(exc).__name__}: {exc}; fallback={type(classic_exc).__name__}: {classic_exc}",
                        "variables": [],
                    }
                )
    return {"datasets": datasets}


def list_variables() -> dict[str, Any]:
    variables = []
    for dataset in dataset_summary()["datasets"]:
        for var in dataset.get("variables", []):
            item = dict(var)
            item["dataset"] = dataset["id"]
            item["origin"] = dataset.get("origin", "")
            variables.append(item)
    return {"variables": variables}


def query_grid(payload: dict[str, Any]) -> dict[str, Any]:
    path = _dataset_path(str(payload.get("dataset") or ""), payload.get("path"))
    variable = str(payload.get("variable") or "sst")
    bounds = payload.get("bounds") or payload
    west = float(bounds.get("west", -60))
    east = float(bounds.get("east", 20))
    south = float(bounds.get("south", -30))
    north = float(bounds.get("north", 30))
    max_points = max(100, min(50000, int(payload.get("max_points", 9000))))
    requested_step = max(0, min(1000, int(payload.get("step") or payload.get("stride") or 0)))

    try:
        return _query_grid_netcdf4(path, variable, west, east, south, north, max_points, requested_step)
    except ImportError:
        pass
    except Exception:
        if path.read_bytes()[:3] != b"CDF":
            raise

    ds = _read_dataset(path)
    lat_name = _coord_name(ds, {"lat", "latitude"})
    lon_name = _coord_name(ds, {"lon", "longitude"})
    if not lat_name or not lon_name:
        raise ValueError("dataset does not contain recognizable latitude/longitude coordinates")
    lats = ds["variables"][lat_name].values
    lons = ds["variables"][lon_name].values
    if variable not in ds["variables"]:
        raise ValueError(f"variable not found: {variable}")
    var = ds["variables"][variable]
    lat_idx = [i for i, lat in enumerate(lats) if min(south, north) <= lat <= max(south, north)]
    lon_idx = [i for i, lon in enumerate(lons) if min(west, east) <= lon <= max(west, east)]
    if not lat_idx or not lon_idx:
        raise ValueError("selected bounds do not intersect the dataset grid")

    target_side = max(10, int(math.sqrt(max_points)))
    auto_step = max(1, math.ceil(max(len(lat_idx), len(lon_idx)) / target_side))
    stride = requested_step or auto_step
    lat_idx = lat_idx[::stride]
    lon_idx = lon_idx[::stride]
    grid: list[list[float | None]] = []
    flat: list[float] = []
    for i in lat_idx:
        row: list[float | None] = []
        for j in lon_idx:
            value = _value_at(ds, var, lat_name, lon_name, i, j)
            fill = var.attrs.get("_FillValue", var.attrs.get("missing_value"))
            if fill is not None and abs(float(value) - float(fill)) < 1e-6:
                row.append(None)
                continue
            if not math.isfinite(float(value)):
                row.append(None)
                continue
            row.append(round(float(value), 4))
            flat.append(float(value))
        grid.append(row)
    if not flat:
        raise ValueError("selected region has no valid ocean data")
    units = var.attrs.get("units", "")
    long_name = var.attrs.get("long_name", variable)
    dim_names = [ds["dim_list"][dimid][0] for dimid in var.dimids]
    render_meta = _variable_render_meta(path.stem, variable, units, long_name, dim_names, set(ds["variables"].keys()))
    lat_out = [float(lats[i]) for i in lat_idx]
    lon_out = [float(lons[i]) for i in lon_idx]
    grid, flat, masked_count = _apply_land_mask(path, render_meta, grid, lat_out, lon_out)
    if not flat:
        raise ValueError("selected region has no valid ocean data after land mask")
    return {
        "dataset": path.stem,
        "source": str(path),
        "variable": variable,
        "units": units,
        "long_name": long_name,
        **render_meta,
        "bounds": {
            "west": min(west, east),
            "east": max(west, east),
            "south": min(south, north),
            "north": max(south, north),
        },
        "lats": [round(x, 4) for x in lat_out],
        "lons": [round(x, 4) for x in lon_out],
        "values": grid,
        "land_mask_applied": masked_count,
        "land_mask_source": str(LAND_MASK_NC) if masked_count else "",
        "step": stride,
        "requested_step": requested_step,
        "auto_step": auto_step,
        "shape": {"lat": len(lat_idx), "lon": len(lon_idx)},
        "stats": {
            "min": round(min(flat), 4),
            "max": round(max(flat), 4),
            "mean": round(sum(flat) / len(flat), 4),
            "count": len(flat),
        },
    }


def _write_classic_nc(
    path: Path,
    lats: list[float],
    lons: list[float],
    fields: dict[str, list[float]],
    attrs: dict[str, dict[str, str]],
) -> None:
    dims = [("lat", len(lats)), ("lon", len(lons))]
    variables: list[dict[str, Any]] = [
        {"name": "lat", "dimids": [0], "attrs": {"units": "degrees_north"}, "type": NC_FLOAT, "data": _pack_floats(lats)},
        {"name": "lon", "dimids": [1], "attrs": {"units": "degrees_east"}, "type": NC_FLOAT, "data": _pack_floats(lons)},
    ]
    for name, values in fields.items():
        variables.append(
            {
                "name": name,
                "dimids": [0, 1],
                "attrs": attrs.get(name, {}),
                "type": NC_FLOAT,
                "data": _pack_floats(values),
            }
        )
    for var in variables:
        var["vsize"] = len(var["data"])
        var["begin"] = 0
    header = _build_header(dims, variables)
    begin = len(header)
    for var in variables:
        var["begin"] = begin
        begin += len(var["data"])
    header = _build_header(dims, variables)
    begin = len(header)
    for var in variables:
        var["begin"] = begin
        begin += len(var["data"])
    header = _build_header(dims, variables)
    with path.open("wb") as f:
        f.write(header)
        for var in variables:
            f.write(var["data"])


def _read_classic_nc(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:3] != b"CDF" or data[3] not in {1, 2}:
        raise ValueError("only NetCDF classic/CDF1 sample files are supported in this lightweight demo")
    pos = 4
    _numrecs, pos = _unpack_i(data, pos)
    dim_tag, pos = _unpack_i(data, pos)
    dims: list[tuple[str, int]] = []
    if dim_tag == NC_DIMENSION:
        dim_count, pos = _unpack_i(data, pos)
        for _ in range(dim_count):
            name, pos = _read_name(data, pos)
            size, pos = _unpack_i(data, pos)
            dims.append((name, size))
    else:
        pos += 4
    _global_attrs, pos = _read_attrs(data, pos)
    var_tag, pos = _unpack_i(data, pos)
    variables: dict[str, NcVariable] = {}
    if var_tag == NC_VARIABLE:
        var_count, pos = _unpack_i(data, pos)
        metas = []
        for _ in range(var_count):
            name, pos = _read_name(data, pos)
            ndims, pos = _unpack_i(data, pos)
            dimids = []
            for _ in range(ndims):
                dimid, pos = _unpack_i(data, pos)
                dimids.append(dimid)
            var_attrs, pos = _read_attrs(data, pos)
            nc_type, pos = _unpack_i(data, pos)
            _vsize, pos = _unpack_i(data, pos)
            begin, pos = _unpack_i(data, pos)
            metas.append((name, dimids, var_attrs, nc_type, begin))
        for name, dimids, var_attrs, nc_type, begin in metas:
            count = 1
            for dimid in dimids:
                count *= dims[dimid][1]
            size = TYPE_SIZES.get(nc_type)
            if not size:
                raise ValueError(f"unsupported NetCDF type: {nc_type}")
            raw = data[begin : begin + count * size]
            if nc_type == NC_FLOAT:
                values = list(struct.unpack(">" + "f" * count, raw))
            elif nc_type == NC_DOUBLE:
                values = list(struct.unpack(">" + "d" * count, raw))
            elif nc_type == NC_INT:
                values = list(struct.unpack(">" + "i" * count, raw))
            elif nc_type == NC_SHORT:
                values = list(struct.unpack(">" + "h" * count, raw))
            else:
                values = []
            variables[name] = NcVariable(name=name, dimids=dimids, attrs=var_attrs, nc_type=nc_type, values=values)
    return {
        "dim_list": dims,
        "dimensions": {name: variables[name].values if name in variables else size for name, size in dims},
        "variables": variables,
    }


def _read_dataset(path: Path) -> dict[str, Any]:
    try:
        return _read_netcdf4(path)
    except ImportError:
        return _read_classic_nc(path)
    except Exception:
        if path.read_bytes()[:3] == b"CDF":
            return _read_classic_nc(path)
        raise


def _read_netcdf4(path: Path) -> dict[str, Any]:
    from netCDF4 import Dataset

    with Dataset(path) as nc:
        dims = [(name, len(dim)) for name, dim in nc.dimensions.items()]
        dim_index = {name: i for i, (name, _size) in enumerate(dims)}
        variables: dict[str, NcVariable] = {}
        for name, var in nc.variables.items():
            attrs = {attr: _jsonable(getattr(var, attr)) for attr in var.ncattrs()}
            dimids = [dim_index[dim] for dim in var.dimensions]
            values = var[:]
            try:
                values = values.filled(float("nan"))
            except AttributeError:
                pass
            variables[name] = NcVariable(
                name=name,
                dimids=dimids,
                attrs=attrs,
                nc_type=NC_FLOAT,
                values=[float(x) for x in values.reshape(-1).tolist()],
            )
    return {
        "dim_list": dims,
        "dimensions": {name: variables[name].values if name in variables else size for name, size in dims},
        "variables": variables,
    }


def _jsonable(value: Any) -> Any:
    try:
        if hasattr(value, "item"):
            return value.item()
        if hasattr(value, "tolist"):
            return value.tolist()
    except Exception:
        pass
    return value


def _iter_nc_files() -> list[Path]:
    ensure_sample_nc()
    files: dict[str, Path] = {}
    for directory in (DATA_DIR, NC_UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(directory.glob("*.nc")):
            files.setdefault(path.stem, path)
    return sorted(files.values(), key=lambda p: (p.parent != DATA_DIR, p.name))


def _origin_for(path: Path) -> str:
    if path == SAMPLE_NC:
        return "synthetic demo sample"
    if path.name.startswith("noaa_"):
        return "NOAA/NCEI ERDDAP real subset"
    if NC_UPLOAD_DIR in path.parents or path.parent == NC_UPLOAD_DIR:
        return "local uploaded NetCDF"
    return "local NetCDF"


def _file_size_mb(path: Path) -> float:
    return round(path.stat().st_size / 1024 / 1024, 3)


def _coord_step(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    diffs = [abs(values[i + 1] - values[i]) for i in range(min(len(values) - 1, 50))]
    diffs = [d for d in diffs if math.isfinite(d) and d > 0]
    if not diffs:
        return None
    return round(sum(diffs) / len(diffs), 6)


def _recommended_step(lat_count: int, lon_count: int, max_points: int = 9000) -> int:
    target_side = max(10, int(math.sqrt(max_points)))
    return max(1, math.ceil(max(lat_count, lon_count) / target_side))


def _variable_render_meta(
    dataset_id: str,
    name: str,
    units: Any,
    long_name: Any,
    dims: list[str],
    all_names: set[str],
) -> dict[str, Any]:
    text = f"{dataset_id} {name} {long_name} {units}".lower()
    modes = ["heatmap", "contour", "points"]
    category = "scalar"
    land_mask = ""

    if any(key in text for key in ("etopo", "elevation", "bathymetry", "bedrock", "topography")):
        category = "relief"
        land_mask = "positive"

    role = _vector_role(name, text)
    vector_pair = _vector_pair_name(name, role, all_names) if role else ""
    if vector_pair:
        category = "vector_component"
        modes = ["heatmap", "particles", "contour", "points"]

    return {
        "category": category,
        "render_modes": modes,
        "land_mask": land_mask,
        "vector_role": role,
        "vector_pair": vector_pair,
        "particle_ready": bool(vector_pair),
    }


def _vector_role(name: str, text: str) -> str:
    lower = name.lower()
    if lower in {"u", "uo", "u10", "uwnd", "ugrd", "ugos", "water_u", "eastward_current", "eastward_wind"}:
        return "u"
    if lower in {"v", "vo", "v10", "vwnd", "vgrd", "vgos", "water_v", "northward_current", "northward_wind"}:
        return "v"
    if "eastward" in text or "zonal" in text:
        return "u"
    if "northward" in text or "meridional" in text:
        return "v"
    return ""


def _vector_pair_name(name: str, role: str, all_names: set[str]) -> str:
    if not role:
        return ""
    lower_to_name = {n.lower(): n for n in all_names}
    pair_map = {
        "u": ["v", "vo", "v10", "vwnd", "vgrd", "vgos", "water_v", "water_v_bottom", "northward_current", "northward_wind"],
        "v": ["u", "uo", "u10", "uwnd", "ugrd", "ugos", "water_u", "water_u_bottom", "eastward_current", "eastward_wind"],
    }
    lower = name.lower()
    candidates = [lower.replace("u", "v", 1)] if role == "u" and lower.startswith("u") else []
    candidates += [lower.replace("v", "u", 1)] if role == "v" and lower.startswith("v") else []
    candidates += pair_map[role]
    for candidate in candidates:
        if candidate in lower_to_name and lower_to_name[candidate] != name:
            return lower_to_name[candidate]
    return ""


def _summary_netcdf4(path: Path) -> dict[str, Any]:
    from netCDF4 import Dataset

    with Dataset(path) as nc:
        lat_name = _find_nc_coord(nc, {"lat", "latitude"})
        lon_name = _find_nc_coord(nc, {"lon", "longitude"})
        if not lat_name or not lon_name:
            raise ValueError("dataset does not contain recognizable latitude/longitude coordinates")
        lat_values = _to_float_list(nc.variables[lat_name][:])
        lon_values = _to_float_list(nc.variables[lon_name][:])
        lat_dim = _coord_dim(nc.variables[lat_name], lat_name)
        lon_dim = _coord_dim(nc.variables[lon_name], lon_name)
        all_names = set(nc.variables.keys())
        variables = []
        for name, var in nc.variables.items():
            lower = name.lower()
            if lower in {lat_name.lower(), lon_name.lower(), "time", "depth", "altitude", "zlev"}:
                continue
            dims = list(getattr(var, "dimensions", ()))
            if lat_dim not in dims or lon_dim not in dims:
                continue
            units = _jsonable(getattr(var, "units", ""))
            long_name = _jsonable(getattr(var, "long_name", name))
            variables.append(
                {
                    "name": name,
                    "units": units,
                    "long_name": long_name,
                    "dims": dims,
                    "shape": [int(size) for size in var.shape],
                    "recommended_step": _recommended_step(len(lat_values), len(lon_values)),
                    **_variable_render_meta(path.stem, name, units, long_name, dims, all_names),
                }
            )
        if not variables:
            raise ValueError("dataset has coordinates but no plottable geospatial variables")
        return {
            "id": path.stem,
            "path": str(path),
            "format": "NetCDF",
            "origin": _origin_for(path),
            "size_mb": _file_size_mb(path),
            "lat_name": lat_name,
            "lon_name": lon_name,
            "lat_count": len(lat_values),
            "lon_count": len(lon_values),
            "resolution": {"lat": _coord_step(lat_values), "lon": _coord_step(lon_values)},
            "recommended_step": _recommended_step(len(lat_values), len(lon_values)),
            "variables": variables,
        }


def _summary_classic(path: Path) -> dict[str, Any]:
    ds = _read_dataset(path)
    lat_name = _coord_name(ds, {"lat", "latitude"})
    lon_name = _coord_name(ds, {"lon", "longitude"})
    all_names = set(ds["variables"].keys())
    variables = []
    for name, var in ds["variables"].items():
        if name in {lat_name, lon_name, "time", "depth", "altitude", "zlev"}:
            continue
        if lat_name and lon_name and _var_has_coords(ds, var, lat_name, lon_name):
            dim_names = [ds["dim_list"][dimid][0] for dimid in var.dimids]
            dim_sizes = [ds["dim_list"][dimid][1] for dimid in var.dimids]
            units = var.attrs.get("units", "")
            long_name = var.attrs.get("long_name", name)
            variables.append(
                {
                    "name": name,
                    "units": units,
                    "long_name": long_name,
                    "dims": dim_names,
                    "shape": dim_sizes,
                    "recommended_step": _recommended_step(len(ds["variables"][lat_name].values), len(ds["variables"][lon_name].values)),
                    **_variable_render_meta(path.stem, name, units, long_name, dim_names, all_names),
                }
            )
    if not variables:
        raise ValueError("dataset has no plottable geospatial variables")
    lat_values = ds["variables"][lat_name].values if lat_name else []
    lon_values = ds["variables"][lon_name].values if lon_name else []
    return {
        "id": path.stem,
        "path": str(path),
        "format": "NetCDF classic",
        "origin": _origin_for(path),
        "size_mb": _file_size_mb(path),
        "lat_name": lat_name,
        "lon_name": lon_name,
        "lat_count": len(lat_values),
        "lon_count": len(lon_values),
        "resolution": {"lat": _coord_step(lat_values), "lon": _coord_step(lon_values)},
        "recommended_step": _recommended_step(len(lat_values), len(lon_values)),
        "variables": variables,
    }


def _find_nc_coord(nc: Any, candidates: set[str]) -> str:
    for name in nc.variables:
        if name.lower() in candidates:
            return name
    for dim_name in nc.dimensions:
        if dim_name.lower() in candidates and dim_name in nc.variables:
            return dim_name
    return ""


def _coord_dim(var: Any, fallback: str) -> str:
    dims = list(getattr(var, "dimensions", ()))
    return dims[0] if dims else fallback


def _to_float_list(values: Any) -> list[float]:
    try:
        values = values.filled(float("nan"))
    except AttributeError:
        pass
    try:
        flat = values.reshape(-1).tolist()
    except AttributeError:
        flat = list(values)
    return [float(x) for x in flat]


def _subset_indices(values: list[float], lower: float, upper: float) -> list[int]:
    lo = min(lower, upper)
    hi = max(lower, upper)
    return [i for i, value in enumerate(values) if lo <= value <= hi]


def _normalize_lon_bounds(lons: list[float], west: float, east: float) -> tuple[float, float]:
    if not lons:
        return west, east
    lon_min = min(lons)
    lon_max = max(lons)
    if lon_min >= 0 and lon_max > 180:
        west = west + 360 if west < 0 else west
        east = east + 360 if east < 0 else east
    return west, east


def _value_is_missing(value: Any, fill_values: list[Any]) -> bool:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(value_f):
        return True
    for fill in fill_values:
        try:
            if abs(value_f - float(fill)) < 1e-6:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _apply_land_mask(
    current_path: Path,
    render_meta: dict[str, Any],
    grid: list[list[float | None]],
    lats: list[float],
    lons: list[float],
) -> tuple[list[list[float | None]], list[float], int]:
    masked = 0
    flat: list[float] = []
    mask = _load_land_mask(current_path)
    for i, row in enumerate(grid):
        for j, value in enumerate(row):
            if value is None:
                continue
            if render_meta.get("land_mask") == "positive" and value > 0:
                row[j] = None
                masked += 1
                continue
            if mask and _cell_overlaps_land(mask, lats, lons, i, j):
                row[j] = None
                masked += 1
                continue
            flat.append(float(value))
    return grid, flat, masked


def _load_land_mask(current_path: Path) -> dict[str, Any] | None:
    if not LAND_MASK_NC.exists():
        return None
    key = str(LAND_MASK_NC)
    if key in _LAND_MASK_CACHE:
        return _LAND_MASK_CACHE[key]
    try:
        from netCDF4 import Dataset

        with Dataset(LAND_MASK_NC) as nc:
            lat_name = _find_nc_coord(nc, {"lat", "latitude"})
            lon_name = _find_nc_coord(nc, {"lon", "longitude"})
            var_name = "elevation" if "elevation" in nc.variables else "z"
            if not lat_name or not lon_name or var_name not in nc.variables:
                return None
            values = nc.variables[var_name][:]
            try:
                values = values.filled(float("nan"))
            except AttributeError:
                pass
            mask = {
                "path": current_path,
                "lats": _to_float_list(nc.variables[lat_name][:]),
                "lons": _to_float_list(nc.variables[lon_name][:]),
                "values": values.tolist() if hasattr(values, "tolist") else values,
            }
            _LAND_MASK_CACHE[key] = mask
            return mask
    except Exception:
        return None


def _is_land(mask: dict[str, Any], lat: float, lon: float) -> bool:
    lats = mask["lats"]
    lons = mask["lons"]
    if not lats or not lons:
        return False
    if lat < min(lats) or lat > max(lats) or lon < min(lons) or lon > max(lons):
        return False
    i = _nearest_index(lats, lat)
    j = _nearest_index(lons, lon)
    try:
        elevation = float(mask["values"][i][j])
    except (TypeError, ValueError, IndexError):
        return False
    return math.isfinite(elevation) and elevation > 0


def _cell_overlaps_land(mask: dict[str, Any], lats: list[float], lons: list[float], i: int, j: int) -> bool:
    south, north = _axis_cell_bounds(lats, i)
    west, east = _axis_cell_bounds(lons, j)
    for lat in (south, (south + north) / 2, north):
        for lon in (west, (west + east) / 2, east):
            if _is_land(mask, lat, lon):
                return True
    return False


def _axis_cell_bounds(values: list[float], index: int) -> tuple[float, float]:
    value = values[index]
    if len(values) == 1:
        return value - 0.5, value + 0.5
    if index == 0:
        delta = abs(values[1] - value) / 2
    elif index == len(values) - 1:
        delta = abs(value - values[index - 1]) / 2
    else:
        delta = abs(values[index + 1] - values[index - 1]) / 4
    return value - delta, value + delta


def _nearest_index(values: list[float], target: float) -> int:
    if len(values) == 1:
        return 0
    ascending = values[0] <= values[-1]
    search_values = values if ascending else list(reversed(values))
    pos = bisect.bisect_left(search_values, target)
    if pos <= 0:
        idx = 0
    elif pos >= len(search_values):
        idx = len(search_values) - 1
    else:
        before = search_values[pos - 1]
        after = search_values[pos]
        idx = pos - 1 if abs(target - before) <= abs(after - target) else pos
    return idx if ascending else len(values) - 1 - idx


def _nc_fill_values(var: Any) -> list[Any]:
    fill_values = []
    for attr in ("_FillValue", "missing_value"):
        if hasattr(var, attr):
            fill_values.append(getattr(var, attr))
    return fill_values


def _selected_grid_values(var: Any, selectors: list[Any], slice_axes: list[str]) -> list[list[Any]]:
    values = var[tuple(selectors)]
    try:
        values = values.filled(float("nan"))
    except AttributeError:
        pass
    if slice_axes == ["lon", "lat"]:
        values = values.T
    if hasattr(values, "tolist"):
        values = values.tolist()
    if values and not isinstance(values[0], list):
        values = [values]
    return values


def _align_vectors_to_mask(
    mask_grid: list[list[float | None]],
    u_grid: list[list[float | None]],
    v_grid: list[list[float | None]],
) -> tuple[list[list[float | None]], list[list[float | None]]]:
    for i, row in enumerate(mask_grid):
        for j, value in enumerate(row):
            if value is None:
                u_grid[i][j] = None
                v_grid[i][j] = None
    return u_grid, v_grid


def _query_grid_netcdf4(
    path: Path,
    variable: str,
    west: float,
    east: float,
    south: float,
    north: float,
    max_points: int,
    requested_step: int,
) -> dict[str, Any]:
    from netCDF4 import Dataset

    with Dataset(path) as nc:
        lat_name = _find_nc_coord(nc, {"lat", "latitude"})
        lon_name = _find_nc_coord(nc, {"lon", "longitude"})
        if not lat_name or not lon_name:
            raise ValueError("dataset does not contain recognizable latitude/longitude coordinates")
        if variable not in nc.variables:
            raise ValueError(f"variable not found: {variable}")
        var = nc.variables[variable]
        lat_values = _to_float_list(nc.variables[lat_name][:])
        lon_values = _to_float_list(nc.variables[lon_name][:])
        lat_dim = _coord_dim(nc.variables[lat_name], lat_name)
        lon_dim = _coord_dim(nc.variables[lon_name], lon_name)
        dims = list(var.dimensions)
        if lat_dim not in dims or lon_dim not in dims:
            raise ValueError(f"variable {variable} is not aligned to latitude/longitude dimensions")

        west_norm, east_norm = _normalize_lon_bounds(lon_values, west, east)
        lat_idx = _subset_indices(lat_values, south, north)
        lon_idx = _subset_indices(lon_values, west_norm, east_norm)
        if not lat_idx or not lon_idx:
            raise ValueError("selected bounds do not intersect the dataset grid")

        target_side = max(10, int(math.sqrt(max_points)))
        auto_step = max(1, math.ceil(max(len(lat_idx), len(lon_idx)) / target_side))
        step = requested_step or auto_step
        lat_slice = slice(lat_idx[0], lat_idx[-1] + 1, step)
        lon_slice = slice(lon_idx[0], lon_idx[-1] + 1, step)
        lat_out = lat_values[lat_slice]
        lon_out = lon_values[lon_slice]

        selectors: list[Any] = []
        slice_axes: list[str] = []
        for dim in dims:
            if dim == lat_dim:
                selectors.append(lat_slice)
                slice_axes.append("lat")
            elif dim == lon_dim:
                selectors.append(lon_slice)
                slice_axes.append("lon")
            else:
                selectors.append(0)
        units = _jsonable(getattr(var, "units", ""))
        long_name = _jsonable(getattr(var, "long_name", variable))
        render_meta = _variable_render_meta(path.stem, variable, units, long_name, dims, set(nc.variables.keys()))
        lat_out = [float(x) for x in lat_out]
        lon_out = [float(x) for x in lon_out]

        pair_name = str(render_meta.get("vector_pair") or "")
        if pair_name and pair_name in nc.variables:
            pair_var = nc.variables[pair_name]
            pair_dims = list(pair_var.dimensions)
            if lat_dim in pair_dims and lon_dim in pair_dims:
                pair_selectors: list[Any] = []
                pair_axes: list[str] = []
                for dim in pair_dims:
                    if dim == lat_dim:
                        pair_selectors.append(lat_slice)
                        pair_axes.append("lat")
                    elif dim == lon_dim:
                        pair_selectors.append(lon_slice)
                        pair_axes.append("lon")
                    else:
                        pair_selectors.append(0)
                selected = _selected_grid_values(var, selectors, slice_axes)
                paired = _selected_grid_values(pair_var, pair_selectors, pair_axes)
                selected_fill = _nc_fill_values(var)
                paired_fill = _nc_fill_values(pair_var)
                role = render_meta.get("vector_role")
                u_raw = selected if role == "u" else paired
                v_raw = paired if role == "u" else selected
                u_fill = selected_fill if role == "u" else paired_fill
                v_fill = paired_fill if role == "u" else selected_fill
                grid: list[list[float | None]] = []
                u_grid: list[list[float | None]] = []
                v_grid: list[list[float | None]] = []
                flat: list[float] = []
                for i, u_row_raw in enumerate(u_raw):
                    out_row: list[float | None] = []
                    out_u_row: list[float | None] = []
                    out_v_row: list[float | None] = []
                    for j, u_raw_value in enumerate(u_row_raw):
                        v_raw_value = v_raw[i][j] if i < len(v_raw) and j < len(v_raw[i]) else None
                        if _value_is_missing(u_raw_value, u_fill) or _value_is_missing(v_raw_value, v_fill):
                            out_row.append(None)
                            out_u_row.append(None)
                            out_v_row.append(None)
                            continue
                        u_value = float(u_raw_value)
                        v_value = float(v_raw_value)
                        speed = math.hypot(u_value, v_value)
                        out_row.append(round(speed, 4))
                        out_u_row.append(round(u_value, 4))
                        out_v_row.append(round(v_value, 4))
                        flat.append(speed)
                    grid.append(out_row)
                    u_grid.append(out_u_row)
                    v_grid.append(out_v_row)
                if not flat:
                    raise ValueError("selected region has no valid ocean vector data")
                render_meta = {
                    **render_meta,
                    "category": "vector",
                    "render_modes": ["heatmap", "particles", "contour", "points"],
                    "particle_ready": True,
                    "vector_components": {"u": variable if role == "u" else pair_name, "v": pair_name if role == "u" else variable},
                }
                grid, flat, masked_count = _apply_land_mask(path, render_meta, grid, lat_out, lon_out)
                if not flat:
                    raise ValueError("selected region has no valid ocean vector data after land mask")
                u_grid, v_grid = _align_vectors_to_mask(grid, u_grid, v_grid)
                return {
                    "dataset": path.stem,
                    "source": str(path),
                    "variable": "speed",
                    "source_variable": variable,
                    "units": "m/s",
                    "long_name": "Current speed",
                    **render_meta,
                    "bounds": {
                        "west": min(west, east),
                        "east": max(west, east),
                        "south": min(south, north),
                        "north": max(south, north),
                    },
                    "lats": [round(float(x), 4) for x in lat_out],
                    "lons": [round(float(x), 4) for x in lon_out],
                    "values": grid,
                    "u_grid": u_grid,
                    "v_grid": v_grid,
                    "land_mask_applied": masked_count,
                    "land_mask_source": str(LAND_MASK_NC) if masked_count else "",
                    "step": step,
                    "requested_step": requested_step,
                    "auto_step": auto_step,
                    "shape": {"lat": len(lat_out), "lon": len(lon_out)},
                    "stats": {
                        "min": round(min(flat), 4),
                        "max": round(max(flat), 4),
                        "mean": round(sum(flat) / len(flat), 4),
                        "count": len(flat),
                    },
                }

        values = _selected_grid_values(var, selectors, slice_axes)
        fill_values = _nc_fill_values(var)

        grid: list[list[float | None]] = []
        flat: list[float] = []
        for row in values:
            out_row: list[float | None] = []
            for raw in row:
                if _value_is_missing(raw, fill_values):
                    out_row.append(None)
                    continue
                value = float(raw)
                out_row.append(round(value, 4))
                flat.append(value)
            grid.append(out_row)
        if not flat:
            raise ValueError("selected region has no valid ocean data")
        grid, flat, masked_count = _apply_land_mask(path, render_meta, grid, lat_out, lon_out)
        if not flat:
            raise ValueError("selected region has no valid ocean data after land mask")
        return {
            "dataset": path.stem,
            "source": str(path),
            "variable": variable,
            "units": units,
            "long_name": long_name,
            **render_meta,
            "bounds": {
                "west": min(west, east),
                "east": max(west, east),
                "south": min(south, north),
                "north": max(south, north),
            },
            "lats": [round(float(x), 4) for x in lat_out],
            "lons": [round(float(x), 4) for x in lon_out],
            "values": grid,
            "land_mask_applied": masked_count,
            "land_mask_source": str(LAND_MASK_NC) if masked_count else "",
            "step": step,
            "requested_step": requested_step,
            "auto_step": auto_step,
            "shape": {"lat": len(lat_out), "lon": len(lon_out)},
            "stats": {
                "min": round(min(flat), 4),
                "max": round(max(flat), 4),
                "mean": round(sum(flat) / len(flat), 4),
                "count": len(flat),
            },
        }


def _build_header(dims: list[tuple[str, int]], variables: list[dict[str, Any]]) -> bytes:
    out = bytearray(b"CDF\x01")
    out += struct.pack(">i", 0)
    out += struct.pack(">ii", NC_DIMENSION, len(dims))
    for name, size in dims:
        out += _pack_name(name)
        out += struct.pack(">i", size)
    out += struct.pack(">ii", 0, 0)
    out += struct.pack(">ii", NC_VARIABLE, len(variables))
    for var in variables:
        out += _pack_name(var["name"])
        out += struct.pack(">i", len(var["dimids"]))
        for dimid in var["dimids"]:
            out += struct.pack(">i", dimid)
        out += _pack_attrs(var.get("attrs", {}))
        out += struct.pack(">iii", var["type"], var["vsize"], var["begin"])
    return bytes(out)


def _pack_floats(values: list[float]) -> bytes:
    return _pad4(struct.pack(">" + "f" * len(values), *values))


def _pack_attrs(attrs: dict[str, str]) -> bytes:
    if not attrs:
        return struct.pack(">ii", 0, 0)
    out = bytearray(struct.pack(">ii", NC_ATTRIBUTE, len(attrs)))
    for name, value in attrs.items():
        raw = str(value).encode("utf-8")
        out += _pack_name(name)
        out += struct.pack(">ii", NC_CHAR, len(raw))
        out += _pad4(raw)
    return bytes(out)


def _read_attrs(data: bytes, pos: int) -> tuple[dict[str, Any], int]:
    tag, pos = _unpack_i(data, pos)
    count, pos = _unpack_i(data, pos)
    attrs: dict[str, Any] = {}
    if tag == 0:
        return attrs, pos
    if tag != NC_ATTRIBUTE:
        raise ValueError(f"bad attribute tag: {tag}")
    for _ in range(count):
        name, pos = _read_name(data, pos)
        nc_type, pos = _unpack_i(data, pos)
        n, pos = _unpack_i(data, pos)
        byte_count = TYPE_SIZES[nc_type] * n
        raw = data[pos : pos + byte_count]
        pos += byte_count + ((4 - byte_count % 4) % 4)
        if nc_type == NC_CHAR:
            attrs[name] = raw.decode("utf-8", errors="replace")
        elif nc_type == NC_SHORT:
            values = list(struct.unpack(">" + "h" * n, raw))
            attrs[name] = values[0] if n == 1 else values
        elif nc_type == NC_INT:
            values = list(struct.unpack(">" + "i" * n, raw))
            attrs[name] = values[0] if n == 1 else values
        elif nc_type == NC_FLOAT:
            values = list(struct.unpack(">" + "f" * n, raw))
            attrs[name] = values[0] if n == 1 else values
        elif nc_type == NC_DOUBLE:
            values = list(struct.unpack(">" + "d" * n, raw))
            attrs[name] = values[0] if n == 1 else values
    return attrs, pos


def _pack_name(name: str) -> bytes:
    raw = name.encode("utf-8")
    return struct.pack(">i", len(raw)) + _pad4(raw)


def _read_name(data: bytes, pos: int) -> tuple[str, int]:
    n, pos = _unpack_i(data, pos)
    raw = data[pos : pos + n]
    pos += n + ((4 - n % 4) % 4)
    return raw.decode("utf-8"), pos


def _unpack_i(data: bytes, pos: int) -> tuple[int, int]:
    return struct.unpack(">i", data[pos : pos + 4])[0], pos + 4


def _pad4(raw: bytes) -> bytes:
    return raw + b"\x00" * ((4 - len(raw) % 4) % 4)


def export_geojson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _dataset_path(dataset_id: str, explicit_path: Any = None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    ensure_sample_nc()
    nc_files = {path.stem: path for path in _iter_nc_files()}
    if dataset_id and dataset_id in nc_files:
        return nc_files[dataset_id]
    real = sorted(path for path in nc_files.values() if path.name.startswith("noaa_"))
    if real:
        return real[0]
    return SAMPLE_NC


def _coord_name(ds: dict[str, Any], candidates: set[str]) -> str:
    for name in ds["variables"]:
        if name.lower() in candidates:
            return name
    return ""


def _var_has_coords(ds: dict[str, Any], var: NcVariable, lat_name: str, lon_name: str) -> bool:
    dims = [ds["dim_list"][dimid][0] for dimid in var.dimids]
    return lat_name in dims and lon_name in dims


def _value_at(ds: dict[str, Any], var: NcVariable, lat_name: str, lon_name: str, lat_i: int, lon_i: int) -> float:
    dim_sizes = [ds["dim_list"][dimid][1] for dimid in var.dimids]
    dim_names = [ds["dim_list"][dimid][0] for dimid in var.dimids]
    indexes = []
    for name in dim_names:
        if name == lat_name:
            indexes.append(lat_i)
        elif name == lon_name:
            indexes.append(lon_i)
        else:
            indexes.append(0)
    flat_index = 0
    stride = 1
    for size, idx in reversed(list(zip(dim_sizes, indexes))):
        flat_index += idx * stride
        stride *= size
    return float(var.values[flat_index])
