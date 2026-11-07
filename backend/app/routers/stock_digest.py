"""
Stock Digest — 每日市場報告.
直接讀 Obsidian vault 的 Digest/stock/{date}/（由 stock-digest.sh / horizon 每天產生），
供前端閱讀。不需修改 stock digest 腳本。

每日資料夾含：
  📊 今日市場報告.md  — 主報告（Macro/財報/AI半導體/盤面/Options/行動清單）
  📊 Portfolio.md     — 持倉總表
  📋 個股與產業.md     — 個股與產業細節

Endpoints:
  GET /api/stock-digest          — 可用日期清單（新到舊）
  GET /api/stock-digest/latest   — 最新一份
  GET /api/stock-digest/{date}   — 指定日期（YYYY-MM-DD）

每份回應都帶 `completeness`（與 `manifest`，若當日完整性檢查已跑過）：
前端必須據此顯示「部分完成／缺哪幾份」，不能讓使用者從少一個分頁自己推論。
"""
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# 讀「非保護目錄」的鏡像，而非 ~/Documents（macOS TCC 會擋 launchd 服務讀取，
# 表現為 OSError [Errno 11] Resource deadlock avoided）。鏡像由 stock-digest
# 產生流程同步寫入（見 stock-digest.sh），來源仍是 Obsidian vault。
STOCK_DIGEST_DIR = Path(
    os.environ.get("STOCK_DIGEST_DIR", "~/.arthur-digest/stock")
).expanduser()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 檔名 → 前端段落標籤（依存在與否動態納入）。前三份由戰情室「市場日報」面板讀，
# 後兩份（復盤／掃描）由 /review 讀 —— 2026-09-07 每日報告頁併進戰情室後的分工。
SECTIONS = [
    ("market", "今日市場報告", "📊 今日市場報告.md"),
    ("portfolio", "Portfolio", "📊 Portfolio.md"),
    ("stocks", "個股與產業", "📋 個股與產業.md"),
    ("warroom", "盤後復盤", "🎖️ War Room.md"),        # war_room.py 鏡像落地的六區塊完整復盤
    ("scan", "機會掃描", "🔭 Market Scan.md"),         # market_scan.py 鏡像落地的機會清單
]


def _list_dates() -> list[str]:
    if not STOCK_DIGEST_DIR.exists():
        return []
    dates = [p.name for p in STOCK_DIGEST_DIR.iterdir() if p.is_dir() and _DATE_RE.match(p.name)]
    dates.sort(reverse=True)
    return dates


def _load_manifest(day_dir: Path) -> dict | None:
    """當日完整性 manifest（scripts/daily_manifest.py 寫的 _manifest.json）。

    前端要能明確顯示「今天是部分完成、缺哪幾份」，而不是讓使用者從少一個分頁自己猜。
    """
    fp = day_dir / "_manifest.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load(date: str) -> dict:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="invalid date format")
    day_dir = STOCK_DIGEST_DIR / date
    if not day_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"no stock digest for {date}")
    sections = []
    for key, label, filename in SECTIONS:
        fp = day_dir / filename
        if fp.exists():
            sections.append({"key": key, "label": label, "content": fp.read_text(encoding="utf-8")})
    manifest = _load_manifest(day_dir)
    present = {s["key"] for s in sections}
    return {
        "date": date,
        "sections": sections,
        "manifest": manifest,
        # manifest 還沒產出時（舊日期、或完整性檢查沒跑）至少回報實際存在哪幾份，
        # 前端才不會把「沒有 manifest」誤讀成「一切正常」。
        "completeness": {
            "overall": (manifest or {}).get("overall")
                       or ("success" if len(present) >= len(SECTIONS) else "partial"),
            "present": sorted(present),
            "expected": [k for k, _, _ in SECTIONS],
            "missing_labels": (manifest or {}).get("missing") or
                              [label for k, label, _ in SECTIONS if k not in present],
            "degraded_labels": (manifest or {}).get("degraded") or [],
            "has_manifest": manifest is not None,
        },
    }


@router.get("")
def list_digests() -> dict:
    return {"dates": _list_dates()}


@router.get("/latest")
def latest_digest() -> dict:
    dates = _list_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="no stock digests yet")
    return _load(dates[0])


@router.get("/{date}")
def get_digest(date: str) -> dict:
    return _load(date)
