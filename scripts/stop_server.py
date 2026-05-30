from __future__ import annotations

import argparse
import re
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
    pattern = re.compile(rf"^\s*TCP\s+127\.0\.0\.1:{args.port}\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)\s*$")
    pids = sorted({m.group(1) for line in result.stdout.splitlines() if (m := pattern.match(line))})
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, text=True)
        print(f"stopped pid={pid}")
    if not pids:
        print(f"no listener on port {args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
