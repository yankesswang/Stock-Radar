"""機會追蹤表 — 把每天掃出來的機會累積成一張跨日的清單。

原本每天一個 `opportunities/{date}.json`，看得到「今天有哪些機會」，看不到
「這檔上週也入圍過、當時 $250、現在 $270」。要回答「掃描到底有沒有用」「哪一檔
從第一次入圍到現在漲了多少」「哪些還在等進場區」，就需要一張以 ticker 為主鍵、
跨日累積的表。

這張表只存事實（每次入圍的日期、setup、action、當時價、當時的進場/目標/停損），
狀態與報酬留到讀取時用現價算 —— 存進去的狀態隔天就是舊的。

與決策帳本（data/agent/ledger）的分工：帳本管**持倉決策**（War Room 對手上的部位
下 buy/trim/stop），這張表管**還沒進場的候選**。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.app.config import settings

TRACKER_PATH: Path = Path(settings.DATA_DIR) / "agent" / "opportunities" / "tracker.json"

# 超過這麼多天沒再入圍，就不再算「追蹤中」——掃描已經不覺得它是機會了
STALE_DAYS = 14
# 每檔留幾筆歷史。留太多會讓檔案無止境長大，而問「它第幾次入圍」只需要次數。
MAX_HISTORY = 30


def _load() -> dict:
    try:
        data = json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRACKER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(TRACKER_PATH)     # 原子換檔：掃描與 API 讀取可能同時發生


def _entry_zone(o: dict) -> list[float] | None:
    ez = o.get("entry_zone")
    if isinstance(ez, list) and len(ez) == 2:
        try:
            return [float(ez[0]), float(ez[1])]
        except (TypeError, ValueError):
            return None
    return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def upsert(scan_date: date | str, opportunities: list[dict]) -> dict:
    """把一次掃描的結果併進追蹤表，回傳 {new: [...], updated: [...]}。"""
    d = scan_date.isoformat() if isinstance(scan_date, date) else str(scan_date)
    data = _load()
    entries = data.setdefault("entries", {})
    new_tickers, updated = [], []

    for o in opportunities or []:
        ticker = str(o.get("ticker") or "").upper()
        if not ticker:
            continue
        rec = {
            "date": d,
            "setup": o.get("setup") or (o.get("setup_types") or [None])[0],
            "action": o.get("action"),
            "direction": o.get("direction") or "long",
            "conviction": o.get("conviction"),
            "signal_score": o.get("signal_score"),
            "entry_zone": _entry_zone(o),
            "target": _num(o.get("target")),
            "stop": _num(o.get("stop")),
            "rs_rank": o.get("rs_rank") or (o.get("key_numbers") or {}).get("rs_rank"),
            "theme": o.get("theme") or (o.get("key_numbers") or {}).get("theme"),
            "thesis": o.get("thesis"),
        }

        # 參考價：Stage-2 的機會 JSON 沒有當日價（它只給計畫），所以優先用掃描端補進來
        # 的價，沒有就退回進場區中值 —— 那是「照計畫會買在哪」，算報酬時基準要講明白。
        px = _num(o.get("price")) or _num((o.get("key_numbers") or {}).get("price"))
        if px is not None:
            rec["price"], rec["price_basis"] = px, "scan_close"
        else:
            zone = rec["entry_zone"]
            rec["price"] = round(sum(zone) / 2, 2) if zone else None
            rec["price_basis"] = "entry_mid" if zone else None

        e = entries.get(ticker)
        if e is None:
            entries[ticker] = {
                "ticker": ticker,
                "company": o.get("company"),
                "first_seen": d,
                "last_seen": d,
                "times_seen": 1,
                "first": rec,          # 第一次入圍的條件，用來算「從發現到現在」的報酬
                "latest": rec,
                "history": [rec],
            }
            new_tickers.append(ticker)
            continue

        e["company"] = e.get("company") or o.get("company")
        e["latest"] = rec
        e["last_seen"] = d
        # 同一天重跑（補跑、手動觸發）不該把次數灌水
        hist = [h for h in e.get("history", []) if h.get("date") != d]
        if len(hist) == len(e.get("history", [])):
            e["times_seen"] = int(e.get("times_seen") or 0) + 1
        e["history"] = (hist + [rec])[-MAX_HISTORY:]
        updated.append(ticker)

    data["updated_at"] = datetime.now().astimezone().isoformat()
    _save(data)
    return {"new": new_tickers, "updated": updated, "total": len(entries)}


def _status(entry: dict, price: float | None, today: date) -> str:
    """狀態一律用現價當場判定 —— 存起來的狀態隔天就過期了。"""
    latest = entry.get("latest") or {}
    short = (latest.get("direction") or "long") == "short"
    target, stop, zone = latest.get("target"), latest.get("stop"), latest.get("entry_zone")

    if price is not None:
        if target is not None and (price <= target if short else price >= target):
            return "hit_target"
        if stop is not None and (price >= stop if short else price <= stop):
            return "stopped"
        if zone and min(zone) <= price <= max(zone):
            return "in_zone"

    try:
        last = date.fromisoformat(str(entry.get("last_seen")))
    except ValueError:
        return "tracking"
    return "stale" if (today - last).days > STALE_DAYS else "tracking"


def build_view(prices: dict[str, float | None] | None = None, today: date | None = None) -> dict:
    """讀取用的視圖：加上現價、報酬與狀態。prices 由呼叫端提供（後端有批次報價）。"""
    data = _load()
    prices = prices or {}
    today = today or date.today()
    rows = []
    for ticker, e in (data.get("entries") or {}).items():
        price = prices.get(ticker)
        first, latest = e.get("first") or {}, e.get("latest") or {}
        first_price, last_price = first.get("price"), latest.get("price")

        def _chg(base):
            if price is None or not base:
                return None
            pct = (price - base) / base * 100
            return round(-pct if (latest.get("direction") == "short") else pct, 2)

        rows.append({
            **{k: e.get(k) for k in ("ticker", "company", "first_seen", "last_seen", "times_seen")},
            "setup": latest.get("setup"),
            "action": latest.get("action"),
            "direction": latest.get("direction") or "long",
            "theme": latest.get("theme"),
            "rs_rank": latest.get("rs_rank"),
            "conviction": latest.get("conviction"),
            "entry_zone": latest.get("entry_zone"),
            "target": latest.get("target"),
            "stop": latest.get("stop"),
            "thesis": latest.get("thesis"),
            "first_price": first_price,
            "first_price_basis": first.get("price_basis"),
            "last_price": last_price,
            "price": price,
            "return_since_first": _chg(first_price),
            "return_since_last": _chg(last_price),
            "status": _status(e, price, today),
            "history": e.get("history") or [],
        })

    # 能動手的排前面，其次是報酬 —— 「已達標／在進場區」才是今天要看的
    order = {"in_zone": 0, "hit_target": 1, "tracking": 2, "stopped": 3, "stale": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9), -(r["return_since_first"] or -999)))
    return {
        "updated_at": data.get("updated_at"),
        "count": len(rows),
        "stale_days": STALE_DAYS,
        "items": rows,
    }


def tracked_tickers() -> list[str]:
    return sorted((_load().get("entries") or {}).keys())


def backfill_from_journals(journal_dir: Path | None = None, days: int = 60) -> dict:
    """從既有的每日機會歸檔補建追蹤表（第一次啟用時用，之後由掃描自己 upsert）。"""
    journal_dir = journal_dir or (Path(settings.DATA_DIR) / "agent" / "opportunities")
    cutoff = date.today() - timedelta(days=days)
    files = sorted(p for p in journal_dir.glob("*.json") if p.name != "tracker.json")
    done = 0
    for f in files:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        upsert(d, rec.get("opportunities") or [])
        done += 1
    return {"files": done, "tracked": len(tracked_tickers())}
