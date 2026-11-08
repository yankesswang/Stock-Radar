#!/usr/bin/env python3
"""
每日完整性 manifest —— 讓「今天缺了什麼」變成一個明確的答案，而不是要使用者自己從畫面猜。

問題來源（2026-08-23 檢討）：
  主報告連續缺檔（8/19、8/20、8/22）、Market Scan 從未成功落地、War Room 也有缺日，
  但 portal 和 Telegram 都只是「少一塊」，沒有任何地方說「今天是部分完成」。

本模組掃描當日所有產物 + 管線狀態，輸出：
  - data/agent/manifest/{date}.json  （權威副本）
  - ~/.arthur-digest/stock/{date}/_manifest.json （portal 讀鏡像，繞開 TCC）
  - Telegram 一則「部分完成」通知（只有真的缺東西才發）

狀態：success / partial / failed / missing / stale

CLI：
    uv run python scripts/daily_manifest.py                 # 今天
    uv run python scripts/daily_manifest.py --date 2026-08-22 --telegram
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import pipeline_state  # noqa: E402

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = PROJECT_ROOT / "data"
MIRROR_DIR = Path.home() / ".arthur-digest" / "stock"
MANIFEST_DIR = DATA_DIR / "agent" / "manifest"

S_SUCCESS = "success"
S_PARTIAL = "partial"
S_FAILED = "failed"
S_MISSING = "missing"
S_STALE = "stale"

_ICON = {S_SUCCESS: "✅", S_PARTIAL: "🟡", S_FAILED: "🔴", S_MISSING: "⬜", S_STALE: "🟠"}
_ZH = {S_SUCCESS: "完成", S_PARTIAL: "部分完成", S_FAILED: "失敗",
       S_MISSING: "未產出", S_STALE: "資料過期"}

# 每個產物的最低可信長度（低於此值視為 partial —— 空殼檔比缺檔更誤導）
MIN_CHARS = {
    "war_room": 2000,
    "market_scan": 400,
    "digest_market": 1200,
    "digest_portfolio": 800,
    "digest_stocks": 800,
}


def _mtime(p: Path) -> str | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, TAIPEI_TZ).isoformat()
    except Exception:
        return None


def _file_check(key: str, path: Path, label: str, *, required: bool = True) -> dict:
    if not path.exists():
        return {"key": key, "label": label, "status": S_MISSING if required else S_MISSING,
                "path": str(path), "generated_at": None, "note": "檔案不存在"}
    try:
        size = len(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"key": key, "label": label, "status": S_FAILED, "path": str(path),
                "generated_at": _mtime(path), "note": f"讀取失敗：{e}"}
    floor = MIN_CHARS.get(key, 200)
    status = S_SUCCESS if size >= floor else S_PARTIAL
    note = None if status == S_SUCCESS else f"內容僅 {size} 字（門檻 {floor}），疑似產出中斷"
    return {"key": key, "label": label, "status": status, "path": str(path),
            "generated_at": _mtime(path), "chars": size, "note": note}


def _json_check(key: str, path: Path, label: str, *, list_field: str | None = None,
                min_items: int = 1) -> dict:
    if not path.exists():
        return {"key": key, "label": label, "status": S_MISSING, "path": str(path),
                "generated_at": None, "note": "檔案不存在"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"key": key, "label": label, "status": S_FAILED, "path": str(path),
                "generated_at": _mtime(path), "note": f"JSON 解析失敗：{e}"}
    row = {"key": key, "label": label, "path": str(path),
           "generated_at": data.get("generated_at") or _mtime(path)}
    if list_field:
        items = data.get(list_field)
        n = len(items) if isinstance(items, list) else 0
        row["count"] = n
        if n >= min_items:
            row["status"] = S_SUCCESS
        else:
            row["status"] = S_PARTIAL
            row["note"] = f"{list_field} 只有 {n} 筆（門檻 {min_items}）"
    else:
        row["status"] = S_SUCCESS
    return row


def _portfolio_as_of() -> dict:
    """持倉快照的實際日期 —— 報告裡的權重是哪一天的，要講清楚。"""
    pos_dir = DATA_DIR / "agent" / "positions"
    latest = None
    if pos_dir.exists():
        dates = []
        for p in pos_dir.glob("*.json"):
            try:
                dates.append(date.fromisoformat(p.stem))
            except ValueError:
                continue
        latest = max(dates) if dates else None
    if latest is None:
        try:
            from backend.app.database import SessionLocal
            from backend.app.models import PortfolioSnapshot
            db = SessionLocal()
            try:
                row = db.query(PortfolioSnapshot).order_by(
                    PortfolioSnapshot.snapshot_date.desc()).first()
                latest = getattr(row, "snapshot_date", None)
            finally:
                db.close()
        except Exception:
            latest = None
    return {"as_of": latest.isoformat() if latest else None}


def _vault_notes_check() -> dict:
    """觀點層（Obsidian 投資筆記索引）。

    索引不是每日產物，看的是「有沒有筆記背景行程讀不到」——iCloud 佔位檔在
    launchd 下讀會 OSError(11)，那些筆記等於在戰情官/獵手眼前消失，
    這種缺件不寫出來就沒人會發現。
    """
    from src.analysis import vault_notes as vn
    row = _json_check("vault_notes", vn.INDEX_PATH, "🧠 我的觀點庫（Obsidian 筆記）",
                      list_field="notes")
    if row["status"] == S_MISSING:
        row["note"] = "索引未建立——跑 scripts/sync_vault_notes.py"
        return row
    pending = vn.pending_count()
    if pending:
        row["status"] = S_PARTIAL
        row["note"] = (f"{pending} 篇筆記是 iCloud 佔位檔、背景行程讀不到；"
                       "在終端機跑 scripts/sync_vault_notes.py 可補齊")
    return row


def build_manifest(trade_date: date, *, freshness: dict | None = None) -> dict:
    """掃描當日所有產物，組出完整性 manifest。"""
    ds = trade_date.isoformat()
    day_dir = MIRROR_DIR / ds
    artifacts = [
        _file_check("war_room", day_dir / "🎖️ War Room.md", "🎖️ 盤後復盤"),
        _json_check("war_room_journal", DATA_DIR / "agent" / "journal" / f"{ds}.json",
                    "　└ 決策 journal", list_field="decisions"),
        _json_check("market_scan", DATA_DIR / "agent" / "opportunities" / f"{ds}.json",
                    "🔭 機會掃描", list_field="opportunities"),
        _json_check("market_news", DATA_DIR / "market-news" / f"{ds}.json",
                    "📰 市場新聞", list_field="items", min_items=3),
        _file_check("digest_market", day_dir / "📊 今日市場報告.md", "📊 今日市場報告"),
        _file_check("digest_portfolio", day_dir / "📊 Portfolio.md", "📊 Portfolio"),
        _file_check("digest_stocks", day_dir / "📋 個股與產業.md", "📋 個股與產業"),
        _json_check("ai_cards", DATA_DIR / "ai_reports" / f"{ds}.json", "🧑‍🍳 AI 持倉卡"),
        _vault_notes_check(),
    ]

    # 市場新聞未策展（claude 掛掉退回原始頭條）→ 降級成 partial
    for row in artifacts:
        if row["key"] != "market_news" or row["status"] != S_SUCCESS:
            continue
        try:
            data = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
            if not data.get("curated", True):
                row["status"] = S_PARTIAL
                row["note"] = "claude 策展失敗，只落地未策展頭條（curated=false）"
        except Exception:
            pass

    pipeline = pipeline_state.summary(trade_date)
    # 管線標 failed 但檔案存在的情況以檔案為準；管線 failed 且無檔案 → failed（不是 missing）
    by_stage = {s["key"]: s for s in pipeline["stages"]}
    stage_for_artifact = {
        "war_room": "war_room", "market_scan": "market_scan", "market_news": "market_news",
    }
    for row in artifacts:
        stage = stage_for_artifact.get(row["key"])
        if not stage:
            continue
        st = by_stage.get(stage) or {}
        row["attempts"] = st.get("attempts", 0)
        if row["status"] == S_MISSING and st.get("status") == pipeline_state.STATUS_FAILED:
            row["status"] = S_FAILED
            row["note"] = f"管線失敗（{st.get('attempts')} 次嘗試）：{st.get('detail') or '無細節'}"
        elif row["status"] == S_MISSING and st.get("status") == pipeline_state.STATUS_RUNNING:
            row["note"] = "管線執行中"

    counts: dict[str, int] = {}
    for row in artifacts:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    # 核心三份：復盤（決策）、新聞（事實）、市場報告（外部視角）。一份都沒成功＝當天等於沒報告。
    core_keys = {"war_room", "market_news", "digest_market"}
    core_success = sum(1 for r in artifacts if r["key"] in core_keys and r["status"] == S_SUCCESS)
    overall = (S_SUCCESS if counts.get(S_SUCCESS, 0) == len(artifacts)
               else S_FAILED if core_success == 0
               else S_PARTIAL)

    manifest = {
        "trade_date": ds,
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "overall": overall,
        "counts": counts,
        "artifacts": artifacts,
        "pipeline": pipeline,
        "portfolio": _portfolio_as_of(),
        "freshness": freshness,
        "missing": [r["label"].strip("　└ ") for r in artifacts
                    if r["status"] in (S_MISSING, S_FAILED)],
        "degraded": [r["label"].strip("　└ ") for r in artifacts if r["status"] == S_PARTIAL],
    }
    return manifest


def save_manifest(manifest: dict) -> list[Path]:
    written: list[Path] = []
    ds = manifest["trade_date"]
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    for target in (MANIFEST_DIR / f"{ds}.json", MIRROR_DIR / ds / "_manifest.json"):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            written.append(target)
        except Exception as e:
            print(f"[manifest] 寫入失敗 {target}：{e}")
    return written


def render_manifest_md(manifest: dict) -> str:
    lines = [
        f"### 📋 今日產出完整性　{_ICON.get(manifest['overall'], '')} "
        f"**{_ZH.get(manifest['overall'], manifest['overall'])}**",
        "",
        "| 產物 | 狀態 | 產出時間 | 備註 |",
        "|------|------|---------|------|",
    ]
    for row in manifest["artifacts"]:
        gen = (row.get("generated_at") or "—")[:16].replace("T", " ")
        note = row.get("note") or ""
        if row.get("count") is not None:
            note = (note + f"　{row['count']} 筆").strip()
        lines.append(f"| {row['label']} | {_ICON.get(row['status'],'')} {_ZH.get(row['status'], row['status'])} "
                     f"| {gen} | {note} |")
    pa = (manifest.get("portfolio") or {}).get("as_of")
    lines.append("")
    lines.append(f"持倉快照日期：**{pa or '未知'}**"
                 + ("　⚠️ 與交易日不同，報告中的權重／市值不是當日的"
                    if pa and pa != manifest["trade_date"] else ""))
    if manifest.get("missing"):
        lines.append(f"缺少：{'、'.join(manifest['missing'])}")
    if manifest.get("degraded"):
        lines.append(f"降級：{'、'.join(manifest['degraded'])}")
    return "\n".join(lines)


def render_manifest_telegram(manifest: dict) -> str:
    icon = _ICON.get(manifest["overall"], "")
    head = f"{icon} 每日產出完整性 {manifest['trade_date']}：{_ZH.get(manifest['overall'])}"
    rows = [f"{_ICON.get(r['status'],'')} {r['label'].strip('　└ ')}"
            for r in manifest["artifacts"] if not r["key"].endswith("journal")]
    body = "｜".join(rows)
    tail = []
    if manifest.get("missing"):
        tail.append("缺少：" + "、".join(manifest["missing"]))
    if manifest.get("degraded"):
        tail.append("降級：" + "、".join(manifest["degraded"]))
    pa = (manifest.get("portfolio") or {}).get("as_of")
    if pa and pa != manifest["trade_date"]:
        tail.append(f"⚠️ 持倉快照停在 {pa}")
    retry = [s for s in manifest["pipeline"]["stages"]
             if s["status"] == pipeline_state.STATUS_FAILED]
    if retry:
        tail.append("手動補跑：cd ~/Projects/Arthur_App/stock-radar && "
                    + "；".join(f"uv run python scripts/{s['key']}.py" for s in retry))
    return "\n".join([head, body] + tail)


def main() -> None:
    ap = argparse.ArgumentParser(description="每日完整性 manifest")
    ap.add_argument("--date", help="YYYY-MM-DD（預設今天）")
    ap.add_argument("--telegram", action="store_true", help="不完整時發 Telegram")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    d = date.fromisoformat(args.date) if args.date else date.today()

    manifest = build_manifest(d)
    save_manifest(manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2) if args.json
          else render_manifest_md(manifest))

    if args.telegram and manifest["overall"] != S_SUCCESS:
        try:
            from scripts.daily_analysis import send_telegram
            send_telegram(render_manifest_telegram(manifest))
        except Exception as e:
            print(f"[manifest] Telegram 發送失敗：{e}")


if __name__ == "__main__":
    main()
