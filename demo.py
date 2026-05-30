from __future__ import annotations

import argparse
import json

from ocean_agents_demo.core import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", default=["海洋热浪对渔业有什么风险？"])
    parser.add_argument("--backend", choices=["auto", "local", "ragflow"], default="auto")
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    result = run_pipeline(" ".join(args.question), backend=args.backend, trace=args.trace)
    print(result["report"])
    if args.trace:
        print(json.dumps(result["trace"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
