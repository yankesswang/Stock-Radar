#!/usr/bin/env python3
"""Obsidian 投資筆記 → 觀點層索引（鏡像 + 重建）。

pipeline 每天跑 war_room 前會自己呼叫一次，所以正常情況你不用手動跑。
需要手動跑的唯一情況：**iCloud 把筆記踢成 dataless 佔位檔**。

  背景行程（launchd 起的 backend / 排程）叫不動 iCloud 下載檔案，讀 dataless 檔會
  拿到 OSError(11) Resource deadlock avoided，那些筆記就會留在 pending。
  在終端機（有 GUI session）跑這支就能觸發下載、把 pending 補進來。

用法:
    uv run python scripts/sync_vault_notes.py              # 鏡像 + 重建索引
    uv run python scripts/sync_vault_notes.py --status     # 只看現況，不動資料
    uv run python scripts/sync_vault_notes.py --days 180   # 換窗口（預設 90 天）
    uv run python scripts/sync_vault_notes.py --force      # 全部重鏡像（改了 parser 時用）
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.vault_notes import (  # noqa: E402
    INDEX_PATH, MIRROR_ROOT, STATE_PATH, VAULT_ROOT, WINDOW_DAYS,
    build_index, load_index, mirror, vault_tickers,
)


def _universe_delta() -> str:
    """筆記帶進 Stage-1 預篩的淨增檔數（扣掉本來就在追蹤股／Mimi 名單裡的）。"""
    vt = vault_tickers(60)
    if not vt:
        return "近 60 天筆記未提到任何個股"
    try:
        # 比較基準要是「沒有筆記時的 universe」——build_universe 現在已經把筆記併進去了，
        # 拿它當基準會得到 0 新增，所以這裡自己組追蹤股 ∪ Mimi。
        from src.config import STOCK_LIST
        from scripts.daily_analysis import parse_mimi_doc
        base = {t.upper() for ts in STOCK_LIST.values() for t in ts}
        mimi = {}
        try:
            mimi = parse_mimi_doc()
            base |= {t.upper() for t in mimi}
        except Exception:
            pass
        # 只比 curated 那一層：發現層是全市場自動撈的，混進來的話「筆記帶進幾檔」
        # 會被一千多檔淹掉，這句話就沒意義了
        from src.analysis.opportunity_scanner import build_curated_universe
        after = len(build_curated_universe(mimi))    # 實際掃描名單（已去 ETF 殼雜訊）
        return (f"近 60 天筆記 {len(vt)} 檔｜其中 {len(vt - base)} 檔是筆記才有的新名字"
                f"（universe {after - len(vt - base)} → {after}）")
    except Exception:
        return f"近 60 天筆記 {len(vt)} 檔（無法比對現有 universe）"


def _print_status() -> None:
    idx = load_index()
    print(f"vault  : {VAULT_ROOT}（{'存在' if VAULT_ROOT.exists() else '找不到'}）")
    print(f"鏡像   : {MIRROR_ROOT}｜{len(list(MIRROR_ROOT.rglob('*.md'))) if MIRROR_ROOT.exists() else 0} 篇")
    print(f"索引   : {INDEX_PATH}｜{len(idx.get('notes') or [])} 篇"
          f"｜{len(idx.get('by_ticker') or {})} 檔標的｜建於 {idx.get('built_at')}")
    print(f"universe 擴充：{_universe_delta()}")
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    pending = state.get("pending") or []
    print(f"上次同步：{state.get('synced_at')}｜新增 {state.get('copied')}"
          f"｜未變 {state.get('skipped')}｜待補 {len(pending)}")
    for p in pending[:10]:
        print(f"  ⏳ {p['rel']}（{p['reason']}）")
    if pending:
        print("  ↑ 這些是 iCloud 佔位檔。在終端機重跑這支腳本即可觸發下載補齊。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Obsidian 投資筆記 → 觀點層索引")
    ap.add_argument("--status", action="store_true", help="只顯示現況，不動資料")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS, help=f"索引窗口天數（預設 {WINDOW_DAYS}）")
    ap.add_argument("--force", action="store_true", help="忽略 mtime 快取，全部重鏡像")
    ap.add_argument("--no-materialize", action="store_true",
                    help="不嘗試下載 dataless 檔（背景排程用）")
    args = ap.parse_args()

    if args.status:
        _print_status()
        return

    print(f"[mirror] {VAULT_ROOT} → {MIRROR_ROOT}（近 {args.days} 天）...")
    m = mirror(window_days=args.days, force=args.force, materialize=not args.no_materialize)
    if not m["vault_ok"]:
        print(f"[mirror] ⚠️ 找不到 vault（{VAULT_ROOT}）——索引維持原狀")
        return
    print(f"[mirror] 新增/更新 {m['copied']}｜未變 {m['skipped']}｜待補 {len(m['pending'])}")
    for p in m["pending"][:10]:
        print(f"  ⏳ {p['rel']}（{p['reason']}）")

    idx = build_index(window_days=args.days)
    print(f"[index ] {len(idx['notes'])} 篇筆記｜{len(idx['by_ticker'])} 檔標的 → {INDEX_PATH}")
    print(f"[universe] {_universe_delta()}")


if __name__ == "__main__":
    main()
