#!/usr/bin/env python3
"""補跑某一天 AI 持倉日報的「總體重點分析」。

逐檔決策卡不重跑 —— 直接讀 data/ai_reports/{date}.json 裡既有的卡片，
只跑一次 claude 產總覽，再把 overview + markdown 寫回同一份日報（json + md）。

用在總覽當初失敗的時候（例如撞到 claude 用量上限），不必為了一段總覽
重產整批卡片。

    uv run python scripts/rebuild_ai_overview.py --date 2026-08-22
    uv run python scripts/rebuild_ai_overview.py            # 預設今天
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.routers.ai_report import (  # noqa: E402
    REPORT_DIR,
    _build_markdown,
    _generate_overview,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="日報日期 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="已有總覽也重產")
    args = ap.parse_args()

    fp = REPORT_DIR / f"{args.date}.json"
    if not fp.exists():
        print(f"找不到 {fp}")
        return 1

    payload = json.loads(fp.read_text(encoding="utf-8"))
    if payload.get("overview") and not args.force:
        print(f"{args.date} 已有總覽（--force 可重產）")
        return 0

    holdings = payload.get("holdings") or []
    cards = {c["ticker"]: c for c in payload.get("cards") or []}
    if not cards:
        print(f"{args.date} 沒有任何卡片，無法產總覽")
        return 1

    print(f"[overview] {args.date}：{len(cards)} 檔卡片 → 跑 claude…")
    overview = _generate_overview(args.date, holdings, cards)
    if not overview:
        print("總覽產生失敗（原因見上方訊息），日報維持原樣未更動")
        return 1

    payload["overview"] = overview
    payload["markdown"] = _build_markdown(args.date, holdings, cards, overview)
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / f"{args.date}.md").write_text(payload["markdown"], encoding="utf-8")

    print(
        f"完成：重點 {len(overview['key_points'])} 條｜優先處理 {len(overview['priority_actions'])} 條"
        f"｜風險 {len(overview['top_risks'])} 條 → {fp}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
