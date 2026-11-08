#!/usr/bin/env python
"""重建發現層 universe 快取（S&P 500 + S&P 400 成分股 → 三道品質門檻）。

    uv run python scripts/refresh_discovery_universe.py            # 重建
    uv run python scripts/refresh_discovery_universe.py --status   # 只看現況

快取 7 天過期，Stage-1 預篩會自己在過期時重抓，所以正常不用手動跑；
想立刻換一批、或改了門檻要重算時才用得上。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis import discovery_universe as du     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只顯示快取現況，不重抓")
    args = ap.parse_args()

    if args.status:
        cache = du._read_cache()
        if not cache:
            print("尚未建立快取，跑一次 refresh_discovery_universe.py 即可")
            return 1
        fresh = du._cache_is_fresh(cache, du.CACHE_MAX_AGE_DAYS)
        print(f"建立日期  {cache.get('built_on')}  ({'新鮮' if fresh else '已過期'})")
        print(f"門檻      市值 ≥ {du.MIN_MARKET_CAP/1e9:.0f}B｜日均成交額 ≥ "
              f"{du.MIN_DOLLAR_VOLUME/1e6:.0f}M｜分析師 ≥ {du.MIN_ANALYSTS}")
        f = cache.get("funnel") or {}
        print(f"漏斗      全市場 {f.get('listed')} →（市值）{f.get('after_market_cap')} "
              f"→（成交額）{f.get('after_dollar_volume')} →（分析師）{f.get('after_analysts')}")
        themes: dict[str, int] = {}
        for meta in (cache.get("stocks") or {}).values():
            themes[meta.get("theme") or "?"] = themes.get(meta.get("theme") or "?", 0) + 1
        for theme, n in sorted(themes.items(), key=lambda kv: -kv[1]):
            print(f"  {theme:8s} {n:4d}")
        return 0

    du.rebuild()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
