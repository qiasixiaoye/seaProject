from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def health_ok(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as res:
            return res.status == 200
    except Exception:
        return False


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["auto", "local", "ragflow"], default="auto")
    args = parser.parse_args()
    if health_ok(args.port):
        print(f"already listening: http://127.0.0.1:{args.port}")
        return 0
    if port_open(args.port):
        print(f"port {args.port} is occupied by another service", file=sys.stderr)
        return 1
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = (log_dir / f"demo_{args.port}_{stamp}.out.log").open("a", encoding="utf-8")
    err = (log_dir / f"demo_{args.port}_{stamp}.err.log").open("a", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if sys.platform.startswith("win") else 0
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "api_server.py"), "--port", str(args.port), "--backend", args.backend],
        cwd=ROOT,
        stdout=out,
        stderr=err,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    for _ in range(30):
        if health_ok(args.port):
            print(f"started pid={proc.pid} url=http://127.0.0.1:{args.port}")
            return 0
        if proc.poll() is not None:
            print(f"server exited code={proc.returncode}", file=sys.stderr)
            return 1
        time.sleep(0.2)
    print(f"server did not become healthy pid={proc.pid}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
