"""arXiv 文献自动拉取工具。

通过 arXiv API（无需 key）搜索 4 个领域的论文，
将摘要保存为 knowledge_docs/*.md 文件（RAGFlow 或本地检索均可使用）。

用法：
    python -m geo_agent.knowledge.arxiv_fetcher
    python -m geo_agent.knowledge.arxiv_fetcher --domain stargazing --max 5
    python -m geo_agent.knowledge.arxiv_fetcher --all --max 8
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]   # 项目根目录
OUTPUT_DIR = ROOT / "data" / "knowledge_docs"

ARXIV_API = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}

# ── 每个领域的检索策略 ──────────────────────────────────────────────────────
DOMAIN_QUERIES: dict[str, list[dict[str, Any]]] = {
    "marine": [
        {"q": "sea surface temperature marine heatwave coral bleaching", "cat": "physics.ao-ph"},
        {"q": "ocean heat content salinity anomaly climate change", "cat": "physics.ao-ph"},
        {"q": "sea level rise coastal flooding storm surge", "cat": "physics.ao-ph"},
        {"q": "ocean acidification carbonate chemistry marine ecosystem", "cat": ""},
    ],
    "stargazing": [
        {"q": "astronomical seeing atmospheric turbulence night sky quality", "cat": "astro-ph.IM"},
        {"q": "light pollution artificial sky brightness Bortle dark sky", "cat": "astro-ph.IM"},
        {"q": "cloud cover sky transparency astronomical observation site", "cat": "astro-ph.IM"},
        {"q": "aerosol optical depth visibility atmospheric transparency", "cat": "physics.ao-ph"},
    ],
    "biology": [
        {"q": "coral reef bleaching thermal stress degree heating weeks", "cat": ""},
        {"q": "harmful algal bloom chlorophyll ocean color remote sensing", "cat": "physics.ao-ph"},
        {"q": "marine biodiversity fish habitat suitability temperature", "cat": ""},
        {"q": "phytoplankton bloom upwelling primary productivity", "cat": "physics.ao-ph"},
    ],
    "navigation": [
        {"q": "significant wave height ship routing maritime safety", "cat": "physics.ao-ph"},
        {"q": "ocean current forecast vessel trajectory optimization", "cat": "physics.ao-ph"},
        {"q": "sea state forecast wave period swell Beaufort scale", "cat": "physics.ao-ph"},
        {"q": "marine weather hazard fishing vessel risk assessment", "cat": ""},
    ],
}


def fetch_arxiv(
    query: str,
    category: str = "",
    max_results: int = 5,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    """从 arXiv API 获取论文元数据列表。"""
    search = f"all:{query}"
    if category:
        search = f"cat:{category} AND ({search})"
    params = urllib.parse.urlencode({
        "search_query": search,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GeoAgent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_data = resp.read().decode("utf-8")
    except Exception as exc:
        print(f"  [WARN] arXiv request failed: {exc}")
        return []

    return _parse_arxiv_xml(xml_data)


def _parse_arxiv_xml(xml_data: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        print(f"  [WARN] XML parse error: {exc}")
        return []

    entries = []
    for entry in root.findall("atom:entry", NS):
        def get(tag: str) -> str:
            el = entry.find(f"atom:{tag}", NS)
            return (el.text or "").strip() if el is not None else ""

        title = re.sub(r"\s+", " ", get("title"))
        summary = re.sub(r"\s+", " ", get("summary"))
        arxiv_id = get("id").split("/abs/")[-1].split("v")[0]
        published = get("published")[:10]

        authors = [
            (a.find("atom:name", NS).text or "").strip()
            for a in entry.findall("atom:author", NS)
            if a.find("atom:name", NS) is not None
        ][:5]

        categories = [
            link.get("term", "")
            for link in entry.findall("atom:category", NS)
        ]

        if title and summary:
            entries.append({
                "id": arxiv_id,
                "title": title,
                "authors": authors,
                "published": published,
                "summary": summary,
                "categories": categories,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
            })
    return entries


def save_as_markdown(paper: dict[str, Any], domain: str, output_dir: Path) -> Path:
    """将论文保存为 knowledge_docs 格式的 Markdown 文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^\w.-]", "_", paper["id"])
    filename = f"{domain}_{safe_id}.md"
    path = output_dir / filename

    if path.exists():
        return path  # 已存在，跳过

    authors_str = ", ".join(paper["authors"]) or "Unknown"
    cats_str = ", ".join(paper["categories"]) or domain
    year_match = re.search(r"(\d{4})", paper["published"])
    year = year_match.group(1) if year_match else "2024"

    content = f"""# {paper['title']}

类型：arxiv_paper
主题：{_domain_topics(domain)}
年份：{year}
摘要：{paper['summary'][:800]}

---

来源：{paper['url']}
作者：{authors_str}
发表日期：{paper['published']}
arXiv 分类：{cats_str}
领域标签：{domain}
"""
    path.write_text(content, encoding="utf-8")
    return path


def _domain_topics(domain: str) -> str:
    return {
        "marine":     "海表温度、海洋热浪、海洋酸化、海平面、海洋气候",
        "stargazing": "天文观测、暗天空、光污染、大气透明度、月相",
        "biology":    "珊瑚白化、有害藻华、渔业生态、海洋生物多样性",
        "navigation": "航行安全、海浪预报、船只风险、海洋气象",
    }.get(domain, "海洋科学")


def fetch_domain(
    domain: str,
    max_per_query: int = 5,
    output_dir: Path | None = None,
    verbose: bool = True,
    delay_seconds: float = 3.0,
    timeout: int = 20,
    queries_per_domain: int | None = None,
) -> list[Path]:
    """拉取指定领域的所有查询结果，保存为 md 文件。"""
    if output_dir is None:
        output_dir = OUTPUT_DIR
    queries = DOMAIN_QUERIES.get(domain, [])
    if queries_per_domain:
        queries = queries[:max(1, queries_per_domain)]
    saved_paths = []

    for i, q_config in enumerate(queries, 1):
        query = q_config["q"]
        cat = q_config.get("cat", "")
        if verbose:
            print(f"  [{i}/{len(queries)}] Query: {query[:60]}...")

        papers = fetch_arxiv(query, category=cat, max_results=max_per_query, timeout=timeout)
        for paper in papers:
            path = save_as_markdown(paper, domain, output_dir)
            if path not in saved_paths:
                saved_paths.append(path)
                if verbose:
                    print(f"    ✓ {paper['title'][:60]}")

        if delay_seconds > 0:
            time.sleep(delay_seconds)  # arXiv API 礼貌延迟

    return saved_paths


def fetch_all(
    max_per_query: int = 5,
    output_dir: Path | None = None,
    verbose: bool = True,
    delay_seconds: float = 3.0,
    timeout: int = 20,
    queries_per_domain: int | None = None,
) -> dict[str, list[Path]]:
    """拉取所有领域的文献。"""
    results = {}
    for domain in DOMAIN_QUERIES:
        if verbose:
            print(f"\n=== 领域：{domain} ===")
        paths = fetch_domain(
            domain,
            max_per_query,
            output_dir,
            verbose,
            delay_seconds=delay_seconds,
            timeout=timeout,
            queries_per_domain=queries_per_domain,
        )
        results[domain] = paths
        if verbose:
            print(f"  已保存 {len(paths)} 篇")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="GeoAgent arXiv 文献拉取工具")
    parser.add_argument("--domain", choices=list(DOMAIN_QUERIES.keys()),
                        help="指定拉取领域（留空拉取全部）")
    parser.add_argument("--all", action="store_true", help="拉取所有领域")
    parser.add_argument("--max", type=int, default=5,
                        help="每个 query 最多拉取论文数（默认 5）")
    parser.add_argument("--output", type=str, default="",
                        help="输出目录（默认 data/knowledge_docs）")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="query 之间的等待秒数（默认 3，诊断时可设为 0）")
    parser.add_argument("--timeout", type=int, default=20,
                        help="单次 arXiv 请求超时秒数（默认 20）")
    parser.add_argument("--queries-per-domain", type=int, default=0,
                        help="每个领域最多执行多少个 query（默认 0 表示全部）")
    args = parser.parse_args()

    out_dir = Path(args.output) if args.output else OUTPUT_DIR
    q_limit = args.queries_per_domain or None
    print(f"输出目录：{out_dir}")

    if args.all or not args.domain:
        results = fetch_all(
            max_per_query=args.max,
            output_dir=out_dir,
            delay_seconds=args.delay,
            timeout=args.timeout,
            queries_per_domain=q_limit,
        )
        total = sum(len(v) for v in results.values())
        print(f"\n完成，共保存 {total} 篇文献到 {out_dir}")
    elif args.domain:
        paths = fetch_domain(
            args.domain,
            max_per_query=args.max,
            output_dir=out_dir,
            delay_seconds=args.delay,
            timeout=args.timeout,
            queries_per_domain=q_limit,
        )
        print(f"\n完成，共保存 {len(paths)} 篇文献到 {out_dir}")


if __name__ == "__main__":
    main()
