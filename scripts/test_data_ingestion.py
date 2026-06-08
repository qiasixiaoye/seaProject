from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ocean-ingestion-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        data_dir = root / "data"
        instance_dir = root / "instance"
        os.environ["OCEAN_DATA_DIR"] = str(data_dir)
        os.environ["OCEAN_INSTANCE_DIR"] = str(instance_dir)

        from ocean_agents_demo import ingestion, nc_data

        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "data_uploads").mkdir(parents=True, exist_ok=True)
        (data_dir / "nc_uploads").mkdir(parents=True, exist_ok=True)

        sample_json = data_dir / "data_uploads" / "sample_profile.json"
        sample_json.write_text(
            json.dumps({"station": "demo", "sst": [26.1, 26.4], "depth": 0}, ensure_ascii=False),
            encoding="utf-8",
        )
        sample_mat = data_dir / "data_uploads" / "sample_current.mat"
        sample_mat.write_bytes(
            b"MATLAB 5.0 MAT-file, Platform: Codex, Created for ingestion smoke test".ljust(128, b"\x00")
        )

        nc_path = nc_data.ensure_sample_nc()
        records = ingestion.scan_and_ingest(
            roots=[data_dir, data_dir / "data_uploads", data_dir / "nc_uploads"],
            db_path=instance_dir / "ocean_demo.sqlite3",
        )
        names = {item["name"] for item in records}
        assert sample_json.name in names, names
        assert sample_mat.name in names, names
        assert nc_path.name in names, names

        stored = ingestion.list_asset_records(instance_dir / "ocean_demo.sqlite3")
        assert len(stored) >= 3
        summary = ingestion.ingestion_summary(instance_dir / "ocean_demo.sqlite3")
        assert summary["by_kind"].get("json", 0) >= 1, summary
        assert summary["by_kind"].get("mat", 0) >= 1, summary
        assert summary["by_kind"].get("netcdf", 0) >= 1, summary

        api_checked = False
        try:
            from backend.app import create_app
        except ModuleNotFoundError:
            assets_payload = {"assets": stored, "summary": summary}
        else:
            app = create_app()
            client = app.test_client()
            resp = client.post("/api/data/sync")
            assert resp.status_code == 200, resp.get_data(as_text=True)
            payload = resp.get_json()
            assert payload["success"] is True
            assert payload["summary"]["asset_count"] >= 3

            upload_resp = client.post(
                "/api/data/upload",
                data={"file": (io.BytesIO(b'{"kind":"argo","value":1}'), "argo_profile.json")},
                content_type="multipart/form-data",
            )
            assert upload_resp.status_code == 200, upload_resp.get_data(as_text=True)
            upload_payload = upload_resp.get_json()
            assert upload_payload["asset"]["kind"] == "json"
            assert upload_payload["asset"]["status"] == "parsed"

            assets_resp = client.get("/api/data/assets")
            assert assets_resp.status_code == 200, assets_resp.get_data(as_text=True)
            assets_payload = assets_resp.get_json()
            assert assets_payload["summary"]["asset_count"] >= 4
            api_checked = True

        print(json.dumps({
            "status": "ok",
            "records": len(records),
            "stored": len(assets_payload["assets"]),
            "summary": assets_payload["summary"],
            "api_checked": api_checked,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
