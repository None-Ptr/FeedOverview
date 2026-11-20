"""Report which configured feeds are reachable from the current network.

Run this from a GitHub Actions runner to test the real deployment network:

    python tools/check_feeds.py

It never fails the build unless ``--strict`` is passed; the point is the
report, not a pass/fail gate.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import requests

# Allow `python tools/check_feeds.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402

ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
    "text/xml;q=0.9, */*;q=0.8"
)
REPORT_PATH = "feed-check.md"


@dataclass
class Result:
    category: str
    label: str
    name: str
    url: str
    status: Optional[int] = None
    entries: int = 0
    final_url: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.entries > 0


def check_feed(feed: dict, extract_cfg: dict, timeout: float, retries: int) -> Result:
    result = Result(
        category=str(feed.get("category") or ""),
        label=str(feed.get("category_label") or feed.get("category") or ""),
        name=str(feed.get("name") or feed.get("url") or "unknown"),
        url=str(feed.get("url") or ""),
    )
    headers = {
        "User-Agent": str(extract_cfg.get("user_agent") or "FeedAggregator/1.0"),
        "Accept": ACCEPT,
    }

    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = requests.get(result.url, headers=headers, timeout=timeout, allow_redirects=True)
            result.status = response.status_code
            result.final_url = response.url
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
            else:
                parsed = feedparser.parse(response.content)
                result.entries = len(parsed.entries)
                if result.entries == 0:
                    last_error = f"no entries (bozo={getattr(parsed, 'bozo', None)})"
                else:
                    result.error = ""
                    return result
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}"
        if attempt < retries:
            time.sleep(1.0)

    result.error = last_error or "unknown error"
    return result


def build_report(results: list[Result], config_path: str) -> str:
    ok = [row for row in results if row.ok]
    failed = [row for row in results if not row.ok]
    runner = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local machine"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Feed reachability report",
        "",
        f"- Generated: {generated}",
        f"- Network: {runner}",
        f"- Config: `{config_path}`",
        f"- Feeds: {len(results)} · reachable: {len(ok)} · failed: {len(failed)}",
        "",
    ]

    if failed:
        lines += [
            "## Failed feeds",
            "",
            "| Category | Feed | HTTP | Note | URL |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in sorted(failed, key=lambda item: (item.category, item.name)):
            status = str(row.status) if row.status is not None else "—"
            lines.append(
                f"| {row.category} | {row.name} | {status} | {row.error or '—'} | {row.url} |"
            )
        lines.append("")
    else:
        lines += ["All configured feeds returned at least one entry.", ""]

    lines += [
        "<details>",
        f"<summary>All feeds ({len(results)})</summary>",
        "",
        "| Category | Feed | Entries | HTTP | Note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in sorted(results, key=lambda item: (item.category, item.name)):
        status = str(row.status) if row.status is not None else "—"
        note = "ok" if row.ok else (row.error or "failed")
        lines.append(f"| {row.category} | {row.name} | {row.entries} | {status} | {note} |")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check RSS feed reachability from this network.")
    parser.add_argument("--config", default=os.environ.get("FEED_CONFIG", "config.yaml"))
    parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per request")
    parser.add_argument("--retries", type=int, default=1, help="Retries after the first attempt")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent requests")
    parser.add_argument("--report", default=REPORT_PATH, help="Markdown report path")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when any feed fails")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    feeds = cfg.get("feeds") or []
    extract_cfg = cfg.get("extract") or {}

    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(lambda feed: check_feed(feed, extract_cfg, args.timeout, args.retries), feeds))

    report = build_report(results, args.config)
    Path(args.report).write_text(report, encoding="utf-8")
    print(report)

    failed = sum(1 for row in results if not row.ok)
    summary = f"Feed check: {len(results) - failed}/{len(results)} reachable in {time.time() - started:.1f}s"
    print(summary)
    print(f"::warning::{failed} feed(s) failed; see {args.report}" if failed else f"::notice::{summary}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")

    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
