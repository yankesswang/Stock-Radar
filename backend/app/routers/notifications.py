from pathlib import Path
import asyncio
import csv
import json
import os
import sys
import time
from datetime import date, datetime, time as dt_time
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings

# 全系統共用的 claude 額度守門員（Arthur_App/scripts/lib/claude_guard.py）
_GUARD_LIB = Path(__file__).resolve().parents[4] / "scripts" / "lib"
if str(_GUARD_LIB) not in sys.path:
    sys.path.insert(0, str(_GUARD_LIB))
import claude_guard as _guard  # noqa: E402
from ..database import SessionLocal, get_db
from ..models import Stock
from .earnings import get_earnings_calendar

router = APIRouter()

TELEGRAM_ENV_FALLBACKS = [
    "/Users/yankesswang/Desktop/Projects/Arthur_App/.env",
    "/Users/yankesswang/openclaw_backup/.env",
]
LINE_ENV_FALLBACKS = [
    "/Users/yankesswang/Desktop/Projects/Arthur_App/.env",
    "/Users/yankesswang/openclaw_backup/.env",
    "/Users/yankesswang/.config/digest/.env",
    "/Users/yankesswang/.hn-daily-digest/.env",
]
MARKET_MOVE_CACHE_TTL = 10 * 60
PRICE_MOVE_THRESHOLD_PCT = 5.0
VOLUME_CONFIRMED_MOVE_THRESHOLD_PCT = 3.0
VOLUME_RATIO_THRESHOLD = 2.5
_MARKET_MOVE_CACHE: dict = {"data": None, "ts": 0}
_MARKET_RANKING_CACHE: dict = {"data": None, "ts": 0}
_SEEN_PATH: Path = settings.DATA_DIR / "telegram_notifications_seen.json"
TELEGRAM_NOTIFICATION_INTERVAL_SECONDS = 5 * 60
TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS = 24 * 60 * 60
EARNINGS_MILESTONES = {0, 1, 3, 7}
PORTFOLIO_CSV_PATH: Path = settings.DATA_DIR / "portfolio.csv"
PORTFOLIO_SKIP_TICKERS = {"", "CASH", "ETORO"}
MAX_MOVERS_PER_DIRECTION = 5
MARKET_RANKING_LIMIT = 10
DAILY_PORTFOLIO_REPORT_TIME = dt_time(5, 30)
DAILY_PORTFOLIO_REPORT_WEEKDAYS = {1, 2, 3, 4}  # Tue-Fri Taipei, after US Mon-Thu market close.
WEEKLY_PORTFOLIO_REPORT_WEEKDAYS = {5, 6}  # Sat-Sun Taipei, review the completed US trading week once.
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
MARKET_SNAPSHOT_SYMBOLS = ["SPY", "QQQ", "IWM", "^DJI", "^VIX", "TLT", "UUP", "GLD", "BTC-USD", "ETH-USD"]
MARKET_TECHNICAL_SYMBOLS = ["SPY", "QQQ", "IWM", "TLT", "UUP", "^VIX"]


class TelegramNotification(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=1000)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _telegram_config() -> tuple[str, str]:
    token = settings.TELEGRAM_TOKEN.strip()
    chat_id = settings.TELEGRAM_CHAT_ID.strip() or settings.TELEGRAM_ADMIN_ID.strip()

    if not token or not chat_id:
        env_paths = [Path(settings.TELEGRAM_ENV_FILE).expanduser()]
        env_paths.extend(Path(path) for path in TELEGRAM_ENV_FALLBACKS)

        env_values: dict[str, str] = {}
        for env_path in env_paths:
            env_values = _read_env_file(env_path)
            if env_values:
                break

        token = token or env_values.get("TELEGRAM_TOKEN", "").strip()
        chat_id = (
            chat_id
            or env_values.get("TELEGRAM_CHAT_ID", "").strip()
            or env_values.get("TELEGRAM_ADMIN_ID", "").strip()
            or env_values.get("ADMIN_ID", "").strip()
        )

    return token, chat_id


def _line_access_token() -> str:
    token = (
        os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        or os.environ.get("LINE_ACCESS_TOKEN", "").strip()
        or os.environ.get("CHANNEL_ACCESS_TOKEN", "").strip()
    )
    if token:
        return token

    env_paths = [Path(settings.TELEGRAM_ENV_FILE).expanduser()]
    env_paths.extend(Path(path).expanduser() for path in LINE_ENV_FALLBACKS)

    for env_path in env_paths:
        env_values = _read_env_file(env_path)
        token = (
            env_values.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
            or env_values.get("LINE_ACCESS_TOKEN", "").strip()
            or env_values.get("CHANNEL_ACCESS_TOKEN", "").strip()
        )
        if token:
            return token

    return ""


def _send_telegram_message(text: str) -> None:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Telegram is not configured.")

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Telegram send failed: {exc}") from exc


def _send_line_message(text: str, *, important_report: bool = False) -> None:
    # StockRadar's routine earnings/mover/manual notifications remain on
    # Telegram.  The reader-facing War Room report is sent by war_room.py.
    if not important_report:
        print("[notifications] LINE skipped: non-report notification")
        return
    token = _line_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="LINE is not configured.")

    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"type": "text", "text": text[:5000]}]},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"LINE send failed: {exc}") from exc


def _send_notification_message(text: str) -> None:
    """Send to Telegram and LINE, treating either successful channel as delivered."""
    telegram_error: Exception | None = None
    line_error: Exception | None = None

    try:
        _send_telegram_message(text)
    except Exception as exc:
        telegram_error = exc
        print(f"[notifications] Telegram send failed: {exc}")

    try:
        _send_line_message(text)
    except Exception as exc:
        line_error = exc
        print(f"[notifications] LINE send failed: {exc}")

    if telegram_error and line_error:
        raise telegram_error


def _send_long_line_message(text: str) -> None:
    limit = 4900
    if len(text) <= limit:
        _send_line_message(text)
        return

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in text.split("\n\n"):
        para_len = len(para) + 2
        if current_len + para_len > limit and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len
    if current:
        chunks.append("\n\n".join(current))

    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(1)
        suffix = f"\n\n-- {i + 1}/{len(chunks)} --" if len(chunks) > 1 else ""
        _send_line_message(chunk + suffix)


def _send_long_notification_message(text: str) -> None:
    telegram_error: Exception | None = None
    line_error: Exception | None = None

    try:
        _send_long_telegram_message(text)
    except Exception as exc:
        telegram_error = exc
        print(f"[notifications] Telegram long send failed: {exc}")

    try:
        _send_long_line_message(text)
    except Exception as exc:
        line_error = exc
        print(f"[notifications] LINE long send failed: {exc}")

    if telegram_error and line_error:
        raise telegram_error


def _load_seen() -> dict[str, float]:
    try:
        if _SEEN_PATH.exists():
            data = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _save_seen(seen: dict[str, float]) -> None:
    _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_PATH.write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")


def _seen_recently(seen: dict[str, float], event_id: str, cooldown_seconds: int) -> bool:
    last = float(seen.get(event_id, 0) or 0)
    return time.time() - last < cooldown_seconds


def _mark_seen(seen: dict[str, float], event_id: str) -> None:
    seen[event_id] = time.time()


def _taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def _is_daily_market_notification_day(now: datetime | None = None) -> bool:
    current = now or _taipei_now()
    return current.weekday() in DAILY_PORTFOLIO_REPORT_WEEKDAYS


def _is_weekly_review_day(now: datetime | None = None) -> bool:
    current = now or _taipei_now()
    return (
        current.weekday() in WEEKLY_PORTFOLIO_REPORT_WEEKDAYS
        and current.time() >= DAILY_PORTFOLIO_REPORT_TIME
    )


def _iso_week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _portfolio_tickers() -> set[str]:
    if not PORTFOLIO_CSV_PATH.exists():
        return set()

    tickers: set[str] = set()
    try:
        with PORTFOLIO_CSV_PATH.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ticker = str(row.get("ticker") or "").upper().strip()
                if ticker not in PORTFOLIO_SKIP_TICKERS:
                    tickers.add(ticker)
    except Exception:
        return set()
    return tickers


@router.post("/telegram")
def send_telegram_notification(payload: TelegramNotification):
    text = f"{payload.title}\n\n{payload.body}"
    _send_notification_message(text)
    return {"ok": True}


@router.post("/claude-analysis")
def trigger_claude_analysis(db: Session = Depends(get_db)):
    """Manually trigger a Claude post-market analysis and send via Telegram."""
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    # Pass an empty seen dict so it always runs regardless of schedule
    event_id = _generate_claude_daily_analysis(db, seen={})
    if event_id is None:
        raise HTTPException(status_code=500, detail="Claude analysis failed — check server logs")
    return {"ok": True, "event_id": event_id}


def _collect_due_earnings(db: Session, seen: dict[str, float], portfolio_tickers: set[str]) -> list[dict]:
    payload = get_earnings_calendar(db)
    events = payload.get("events", []) if isinstance(payload, dict) else []
    today = _taipei_now().date()
    due: list[dict] = []

    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        if ticker not in portfolio_tickers:
            continue
        # /api/earnings/calendar also carries already-reported events for the
        # calendar UI — only the scheduled ones are a reminder.
        if event.get("status") == "reported":
            continue

        try:
            earnings_date = date.fromisoformat(event["date"])
        except Exception:
            continue

        diff_days = (earnings_date - today).days
        if diff_days not in EARNINGS_MILESTONES:
            continue

        event_id = f"telegram:earnings:{event['ticker']}:{event['date']}:d{diff_days}"
        if _seen_recently(seen, event_id, TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS):
            continue

        when_text = "今天" if diff_days == 0 else "明天" if diff_days == 1 else f"{diff_days} 天後"
        session = {"BMO": "盤前", "AMC": "盤後"}.get(event.get("time_of_day") or "")
        if session:
            when_text = f"{when_text}{session}"
        due.append({**event, "event_id": event_id, "when_text": when_text, "diff_days": diff_days})

    due.sort(key=lambda item: (item["diff_days"], item["ticker"]))
    return due


def _scan_market_move(ticker: str, company_name: str | None) -> dict | None:
    try:
        hist = yf.Ticker(ticker).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 2:
            return None

        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna().astype(float)
        if len(close) < 2:
            return None

        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        if prev_close <= 0:
            return None

        change_pct = (price - prev_close) / prev_close * 100
        volume_today = float(volume.iloc[-1]) if len(volume) else None
        avg_volume_20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else (
            float(volume.iloc[:-1].mean()) if len(volume) > 1 else None
        )
        volume_ratio = (
            volume_today / avg_volume_20
            if volume_today is not None and avg_volume_20 and avg_volume_20 > 0
            else None
        )

        price_triggered = abs(change_pct) >= PRICE_MOVE_THRESHOLD_PCT
        volume_triggered = (
            abs(change_pct) >= VOLUME_CONFIRMED_MOVE_THRESHOLD_PCT
            and volume_ratio is not None
            and volume_ratio >= VOLUME_RATIO_THRESHOLD
        )
        if not price_triggered and not volume_triggered:
            return None

        reasons = []
        if price_triggered:
            reasons.append(f"單日漲跌 {change_pct:+.2f}%")
        if volume_triggered:
            reasons.append(f"量能 {volume_ratio:.1f}x")

        return {
            "ticker": ticker,
            "company_name": company_name or ticker,
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(volume_today) if volume_today is not None else None,
            "avg_volume_20": int(avg_volume_20) if avg_volume_20 else None,
            "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
            "reasons": reasons,
        }
    except Exception:
        return None


@router.get("/market-movers")
def get_market_movers(db: Session = Depends(get_db)):
    global _MARKET_MOVE_CACHE

    now_taipei = _taipei_now()
    if not _is_daily_market_notification_day(now_taipei):
        return {
            "events": [],
            "mode": "weekly_review" if _is_weekly_review_day(now_taipei) else "market_closed",
            "message": "非單日通知日，避免週末重送上一個交易日資訊。",
            "thresholds": {
                "price_move_pct": PRICE_MOVE_THRESHOLD_PCT,
                "volume_confirmed_move_pct": VOLUME_CONFIRMED_MOVE_THRESHOLD_PCT,
                "volume_ratio": VOLUME_RATIO_THRESHOLD,
            },
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    now = time.time()
    if _MARKET_MOVE_CACHE["data"] is not None and now - _MARKET_MOVE_CACHE["ts"] < MARKET_MOVE_CACHE_TTL:
        return _MARKET_MOVE_CACHE["data"]

    stocks = db.query(Stock.ticker, Stock.company_name).order_by(Stock.ticker).all()
    movers: list[dict] = []

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_scan_market_move, stock.ticker, stock.company_name): stock.ticker
            for stock in stocks
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                movers.append(result)

    movers.sort(key=lambda item: abs(item["change_pct"]), reverse=True)
    payload = {
        "events": movers,
        "thresholds": {
            "price_move_pct": PRICE_MOVE_THRESHOLD_PCT,
            "volume_confirmed_move_pct": VOLUME_CONFIRMED_MOVE_THRESHOLD_PCT,
            "volume_ratio": VOLUME_RATIO_THRESHOLD,
        },
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _MARKET_MOVE_CACHE = {"data": payload, "ts": now}
    return payload


def _scan_market_snapshot(ticker: str, company_name: str | None) -> dict | None:
    try:
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
        if hist is None or hist.empty:
            return None

        close = hist["Close"].dropna()
        if len(close) < 2:
            return None

        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        if prev_close <= 0:
            return None

        change_pct = (price - prev_close) / prev_close * 100
        return {
            "ticker": ticker,
            "company_name": company_name or ticker,
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception:
        return None


def _get_market_rankings(db: Session) -> dict:
    global _MARKET_RANKING_CACHE

    now = time.time()
    if _MARKET_RANKING_CACHE["data"] is not None and now - _MARKET_RANKING_CACHE["ts"] < MARKET_MOVE_CACHE_TTL:
        return _MARKET_RANKING_CACHE["data"]

    stocks = db.query(Stock.ticker, Stock.company_name).order_by(Stock.ticker).all()
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_scan_market_snapshot, stock.ticker, stock.company_name): stock.ticker
            for stock in stocks
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                rows.append(result)

    gainers = sorted(
        [item for item in rows if item["change_pct"] > 0],
        key=lambda item: item["change_pct"],
        reverse=True,
    )[:MARKET_RANKING_LIMIT]
    decliners = sorted(
        [item for item in rows if item["change_pct"] < 0],
        key=lambda item: item["change_pct"],
    )[:MARKET_RANKING_LIMIT]
    payload = {
        "gainers": gainers,
        "decliners": decliners,
        "count": len(rows),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _MARKET_RANKING_CACHE = {"data": payload, "ts": now}
    return payload


def _collect_market_movers(db: Session, seen: dict[str, float], portfolio_tickers: set[str]) -> tuple[list[dict], list[dict]]:
    if not _is_daily_market_notification_day():
        return [], []

    payload = get_market_movers(db)
    events = payload.get("events", []) if isinstance(payload, dict) else []
    today_key = _taipei_now().date().isoformat()
    portfolio_movers: list[dict] = []
    valuation_movers: list[dict] = []

    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        event_id = f"telegram:market:{ticker}:{today_key}"
        if _seen_recently(seen, event_id, TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS):
            continue

        item = {**event, "ticker": ticker, "event_id": event_id}
        if ticker in portfolio_tickers:
            portfolio_movers.append(item)
        else:
            valuation_movers.append(item)

    portfolio_movers.sort(key=lambda item: abs(item["change_pct"]), reverse=True)
    valuation_movers.sort(key=lambda item: abs(item["change_pct"]), reverse=True)
    return portfolio_movers, valuation_movers


def _format_mover(item: dict) -> str:
    direction = "漲" if item["change_pct"] >= 0 else "跌"
    volume_text = f"，量 {item['volume_ratio']}x" if item.get("volume_ratio") else ""
    return f"{item['ticker']} {direction} {item['change_pct']:+.2f}% @ ${item['price']}{volume_text}"


def _format_mover_section(title: str, movers: list[dict]) -> str | None:
    gainers = sorted(
        [item for item in movers if item["change_pct"] >= 0],
        key=lambda item: item["change_pct"],
        reverse=True,
    )
    decliners = sorted(
        [item for item in movers if item["change_pct"] < 0],
        key=lambda item: item["change_pct"],
    )

    lines: list[str] = []
    if gainers:
        lines.append("上漲（依漲幅排序）")
        lines.extend(_format_mover(item) for item in gainers[:MAX_MOVERS_PER_DIRECTION])
    if decliners:
        if lines:
            lines.append("")
        lines.append("下跌（依跌幅排序）")
        lines.extend(_format_mover(item) for item in decliners[:MAX_MOVERS_PER_DIRECTION])

    if not lines:
        return None
    return title + "\n" + "\n".join(lines)


def _fmt_money(value: float | int | None, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if signed and num > 0:
        return f"+${num:,.0f}"
    if signed and num < 0:
        return f"-${abs(num):,.0f}"
    if num < 0:
        return f"-${abs(num):,.0f}"
    return f"${num:,.0f}"


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.2f}%"


def _fmt_plain_number(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{num:.1f}{suffix}"


def _move_icon(value: float | int | None) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "•"
    if num > 0:
        return "▲"
    if num < 0:
        return "▼"
    return "•"


def _should_send_daily_portfolio_report(now: datetime) -> bool:
    return (
        now.weekday() in DAILY_PORTFOLIO_REPORT_WEEKDAYS
        and now.time() >= DAILY_PORTFOLIO_REPORT_TIME
    )


def _should_send_weekly_portfolio_report(now: datetime) -> bool:
    return _is_weekly_review_day(now)


def _format_holding_mover(item: dict) -> str:
    pnl_text = _fmt_money(item.get("day_pnl"), signed=True)
    weight_text = _fmt_plain_number(item.get("weight_pct"), "%")
    return (
        f"{_move_icon(item.get('day_change_pct'))} {item['ticker']} "
        f"{_fmt_pct(item.get('day_change_pct'))} | {pnl_text} | 權重 {weight_text}"
    )


def _aggregate_holding_movers(holdings: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for item in holdings:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker or ticker == "CASH":
            continue
        day_pnl = float(item.get("day_pnl") or 0)
        market_value = float(item.get("market_value") or 0)
        row = rows.setdefault(
            ticker,
            {
                "ticker": ticker,
                "day_pnl": 0.0,
                "market_value": 0.0,
                "weight_pct": 0.0,
            },
        )
        row["day_pnl"] += day_pnl
        row["market_value"] += market_value
        row["weight_pct"] += float(item.get("weight_pct") or 0)

    for row in rows.values():
        previous_value = row["market_value"] - row["day_pnl"]
        row["day_change_pct"] = (
            row["day_pnl"] / previous_value * 100
            if previous_value > 0
            else None
        )
    return list(rows.values())


def _format_market_rank(item: dict) -> str:
    return f"{_move_icon(item.get('change_pct'))} {item['ticker']} {_fmt_pct(item.get('change_pct'))} @ ${item.get('price')}"


def _format_market_snapshot_item(item: dict) -> str:
    label = item.get("label") or item.get("symbol")
    return f"{label} {_fmt_pct(item.get('change_pct'))} @ ${item.get('price')}"


def _scan_weekly_market_move(ticker: str, company_name: str | None) -> dict | None:
    try:
        hist = yf.Ticker(ticker).history(period="10d", auto_adjust=True)
        if hist is None or hist.empty:
            return None

        close = hist["Close"].dropna().astype(float)
        if len(close) < 2:
            return None

        price = float(close.iloc[-1])
        baseline = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
        if baseline <= 0:
            return None

        change_pct = (price - baseline) / baseline * 100
        return {
            "ticker": ticker,
            "company_name": company_name or ticker,
            "price": round(price, 2),
            "weekly_change_pct": round(change_pct, 2),
        }
    except Exception:
        return None


def _get_weekly_market_rankings(db: Session) -> dict:
    stocks = db.query(Stock.ticker, Stock.company_name).order_by(Stock.ticker).all()
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_scan_weekly_market_move, stock.ticker, stock.company_name): stock.ticker
            for stock in stocks
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                rows.append(result)

    gainers = sorted(
        [item for item in rows if item["weekly_change_pct"] > 0],
        key=lambda item: item["weekly_change_pct"],
        reverse=True,
    )[:MARKET_RANKING_LIMIT]
    decliners = sorted(
        [item for item in rows if item["weekly_change_pct"] < 0],
        key=lambda item: item["weekly_change_pct"],
    )[:MARKET_RANKING_LIMIT]
    return {
        "gainers": gainers,
        "decliners": decliners,
        "count": len(rows),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _format_weekly_market_rank(item: dict) -> str:
    return f"{item['ticker']} {_fmt_pct(item.get('weekly_change_pct'))} @ ${item.get('price')}"


def _fetch_weekly_market_snapshot_lines() -> list[str]:
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_scan_weekly_market_move, symbol, symbol): symbol for symbol in MARKET_SNAPSHOT_SYMBOLS}
        for future in as_completed(futures):
            item = future.result()
            if item:
                items.append(item)

    if not items:
        return []

    order = {symbol.upper(): idx for idx, symbol in enumerate(MARKET_SNAPSHOT_SYMBOLS)}
    items.sort(key=lambda item: order.get(str(item.get("ticker", "")).upper(), 999))
    positive_count = sum(1 for item in items if float(item.get("weekly_change_pct") or 0) > 0)
    negative_count = sum(1 for item in items if float(item.get("weekly_change_pct") or 0) < 0)
    tone = _market_tone_label(positive_count, negative_count, len(items))
    lines = [f"本週大盤狀況：{tone}（{positive_count} 漲 / {negative_count} 跌 / {len(items)} 項）"]
    lines.extend(_format_weekly_market_rank(item) for item in items)
    return lines


def _market_tone_label(positive_count: int, negative_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "資料不足"
    if positive_count >= negative_count + 3:
        return "Risk-on 偏多"
    if negative_count >= positive_count + 3:
        return "Risk-off 偏空"
    return "多空混合"


def _fetch_market_snapshot_lines() -> list[str]:
    try:
        from .market import _fetch_market_symbol

        items: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_market_symbol, symbol): symbol for symbol in MARKET_SNAPSHOT_SYMBOLS}
            for future in as_completed(futures):
                item = future.result()
                if item.get("ok"):
                    items.append(item)

        order = {symbol.upper(): idx for idx, symbol in enumerate(MARKET_SNAPSHOT_SYMBOLS)}
        items.sort(key=lambda item: order.get(str(item.get("symbol", "")).upper(), 999))
        valid = [item for item in items if item.get("change_pct") is not None]
        if not valid:
            return []

        positive_count = sum(1 for item in valid if float(item.get("change_pct") or 0) > 0)
        negative_count = sum(1 for item in valid if float(item.get("change_pct") or 0) < 0)
        tone = _market_tone_label(positive_count, negative_count, len(valid))
        item_by_symbol = {str(item.get("symbol") or "").upper(): item for item in valid}
        spy = item_by_symbol.get("SPY")
        qqq = item_by_symbol.get("QQQ")
        vix = item_by_symbol.get("^VIX")

        lines = [
            f"📍 市場溫度：{tone}（{positive_count} 漲 / {negative_count} 跌 / {len(valid)} 項）"
        ]
        if spy or qqq or vix:
            summary_bits = []
            if spy:
                summary_bits.append(f"SPY {_fmt_pct(spy.get('change_pct'))}")
            if qqq:
                summary_bits.append(f"QQQ {_fmt_pct(qqq.get('change_pct'))}")
            if vix:
                summary_bits.append(f"VIX {_fmt_pct(vix.get('change_pct'))}")
            lines.append("重點：" + " | ".join(summary_bits))

        groups = [
            ("📊 指數", ["SPY", "QQQ", "IWM", "^DJI"]),
            ("🧯 波動 / 債券 / 美元", ["^VIX", "TLT", "UUP"]),
            ("🪙 避險 / 加密", ["GLD", "BTC-USD", "ETH-USD"]),
        ]
        for label, symbols in groups:
            group_items = [item_by_symbol[symbol] for symbol in symbols if symbol in item_by_symbol]
            if group_items:
                lines.append(f"{label}：" + " | ".join(_format_market_snapshot_item(item) for item in group_items))
        return lines
    except Exception as exc:
        return [f"📍 市場溫度：資料取得失敗（{exc}）"]


def _compute_market_technical(symbol: str) -> dict | None:
    try:
        # 2y — a 250-day MA needs 250 bars, and "1y" only ever returns ~251.
        hist = yf.Ticker(symbol).history(period="2y", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 50:
            return None

        close = hist["Close"].dropna().astype(float)
        volume = hist["Volume"].dropna().astype(float)
        if len(close) < 50:
            return None

        price = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        ma250 = float(close.rolling(250).mean().iloc[-1]) if len(close) >= 250 else None

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = float((macd - signal).iloc[-1])

        high20 = float(hist["High"].dropna().astype(float).iloc[-20:].max()) if len(hist) >= 20 else None
        low20 = float(hist["Low"].dropna().astype(float).iloc[-20:].min()) if len(hist) >= 20 else None
        avg_volume_20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else None
        volume_ratio = float(volume.iloc[-1]) / avg_volume_20 if avg_volume_20 and avg_volume_20 > 0 else None

        trend_parts: list[str] = []
        if ma20:
            trend_parts.append("站上20日" if price >= ma20 else "跌破20日")
        if ma50:
            trend_parts.append("站上50日" if price >= ma50 else "跌破50日")
        if ma250:
            trend_parts.append("站上250日" if price >= ma250 else "跌破250日")

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "rsi": round(float(rsi.iloc[-1]), 1) if not rsi.empty else None,
            "macd_hist": round(macd_hist, 3),
            "price_vs_ma20_pct": round((price - ma20) / ma20 * 100, 2) if ma20 else None,
            "price_vs_ma50_pct": round((price - ma50) / ma50 * 100, 2) if ma50 else None,
            "trend": "、".join(trend_parts) if trend_parts else "趨勢資料不足",
            "range20_position_pct": round((price - low20) / (high20 - low20) * 100, 0)
            if high20 and low20 and high20 > low20 else None,
            "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        }
    except Exception:
        return None


def _format_market_technical_line(item: dict) -> str:
    symbol = item["symbol"]
    rsi = item.get("rsi")
    rsi_text = f"RSI {rsi:.0f}" if rsi is not None else "RSI N/A"
    macd_text = "MACD偏多" if (item.get("macd_hist") or 0) > 0 else "MACD偏空"
    position = item.get("range20_position_pct")
    position_text = f"20日區間 {position:.0f}%" if position is not None else "20日區間 N/A"
    volume_text = f" | 量 {item['volume_ratio']}x" if item.get("volume_ratio") else ""
    return (
        f"{symbol}: {item['trend']} | {rsi_text} | {macd_text} | "
        f"距20MA {_fmt_pct(item.get('price_vs_ma20_pct'))} | {position_text}{volume_text}"
    )


def _fetch_market_technical_lines() -> list[str]:
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_compute_market_technical, symbol): symbol for symbol in MARKET_TECHNICAL_SYMBOLS}
        for future in as_completed(futures):
            item = future.result()
            if item:
                items.append(item)

    if not items:
        return []

    order = {symbol.upper(): idx for idx, symbol in enumerate(MARKET_TECHNICAL_SYMBOLS)}
    items.sort(key=lambda item: order.get(item["symbol"].upper(), 999))
    return ["🧭 技術面摘要"] + [f"• {_format_market_technical_line(item)}" for item in items]


def _build_portfolio_report_section(report: dict, current: dict, period_label: str = "今日") -> str:
    headline = report.get("headline", {})
    portfolio_name = report.get("portfolio") or "Portfolio"
    holdings = [
        h for h in current.get("holdings", [])
        if h.get("ticker") != "CASH" and h.get("day_change_pct") is not None
    ]
    for item in holdings:
        item["day_pnl"] = float(item.get("day_change") or 0) * float(item.get("shares") or 0)
    holding_movers = _aggregate_holding_movers(holdings)

    gainers = sorted(
        [item for item in holding_movers if (item.get("day_pnl") or 0) > 0],
        key=lambda item: item.get("day_pnl") or 0,
        reverse=True,
    )[:3]
    decliners = sorted(
        [item for item in holding_movers if (item.get("day_pnl") or 0) < 0],
        key=lambda item: item.get("day_pnl") or 0,
    )[:3]
    risk_flags = report.get("risk_flags", [])[:3]
    stock_insights = report.get("stock_insights", [])[:3]
    abnormal_summary = ((report.get("abnormal_stocks") or {}).get("summary") or [])[:6]

    lines = [
        f"📌 {portfolio_name}",
        f"💼 總值：{_fmt_money(headline.get('total_value'))}",
        f"📈 {period_label}損益：{_fmt_money(headline.get('value_change'), signed=True)} ({_fmt_pct(headline.get('value_change_pct'))})",
        (
            f"🏁 累計淨損益：{_fmt_money(headline.get('net_gain'), signed=True)} "
            f"({_fmt_pct(headline.get('net_return_pct'))})"
        ),
        (
            f"💵 現金：{_fmt_money(headline.get('total_cash'))} "
            f"({_fmt_plain_number(headline.get('cash_pct'), '%')}) | "
            f"持倉：{headline.get('position_count', 'N/A')}"
        ),
    ]
    if period_label == "今日" and headline.get("daily_pnl") is not None:
        lines[2] = f"📈 今日損益：{_fmt_money(headline.get('daily_pnl'), signed=True)} ({_fmt_pct(headline.get('daily_pnl_pct'))})"

    if gainers:
        lines.append("")
        lines.append("🟢 上漲貢獻")
        lines.extend(f"{idx}. {_format_holding_mover(item)}" for idx, item in enumerate(gainers, 1))
    if decliners:
        lines.append("")
        lines.append("🔴 下跌拖累")
        lines.extend(f"{idx}. {_format_holding_mover(item)}" for idx, item in enumerate(decliners, 1))
    if stock_insights:
        lines.append("")
        lines.append("🎯 今日需關注")
        for item in stock_insights:
            signals = "、".join(signal.get("tag", "") for signal in item.get("signals", [])[:2] if signal.get("tag"))
            suffix = f" | {signals}" if signals else ""
            lines.append(
                f"• {item.get('ticker')} {_fmt_pct(item.get('day_change_pct'))} | "
                f"權重 {_fmt_plain_number(item.get('weight_pct'), '%')}{suffix}"
            )
    if abnormal_summary:
        lines.append("")
        lines.append("🚨 異常股票")
        for item in abnormal_summary:
            signals = "、".join(
                sig.get("label", "")
                for sig in (item.get("signals") or [])[:3]
                if sig.get("label")
            )
            lines.append(
                f"• {item.get('ticker')} {_fmt_pct(item.get('day_change_pct'))} | "
                f"權重 {_fmt_plain_number(item.get('weight_pct'), '%')} | {signals}"
            )
    if risk_flags:
        lines.append("")
        lines.append(f"⚠️ {period_label}檢討")
        lines.extend(f"• {item.get('message')}" for item in risk_flags)

    return "\n".join(lines)


def _collect_daily_portfolio_reports(db: Session, seen: dict[str, float]) -> list[dict]:
    now = _taipei_now()
    if not _should_send_daily_portfolio_report(now):
        return []

    report_date = now.date().isoformat()
    legacy_event_id = f"telegram:portfolio-daily:{report_date}"
    portfolio_event_id = f"telegram:portfolio-daily:{report_date}:portfolio"
    market_event_id = f"telegram:portfolio-daily:{report_date}:market"
    legacy_sent = _seen_recently(seen, legacy_event_id, TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS)
    should_send_portfolio = not legacy_sent and not _seen_recently(
        seen,
        portfolio_event_id,
        TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS,
    )
    should_send_market = not legacy_sent and not _seen_recently(
        seen,
        market_event_id,
        TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS,
    )
    if not should_send_portfolio and not should_send_market:
        return []

    from .portfolio import get_csv_portfolio, _generate_csv_portfolio_report

    payload = get_csv_portfolio(name=None, db=db)
    portfolios = payload.get("portfolios", {}) if isinstance(payload, dict) else {}
    names = payload.get("portfolio_names", []) if isinstance(payload, dict) else []

    portfolio_sections: list[str] = []
    for name in names:
        current = portfolios.get(name)
        if not current:
            continue
        try:
            report = _generate_csv_portfolio_report(portfolio=name, period="daily", db=db)
        except Exception as exc:
            portfolio_sections.append(f"{name}\n日報產生失敗：{exc}")
            continue
        portfolio_sections.append(_build_portfolio_report_section(report, current))

    market_lines: list[str] = []
    snapshot_lines = _fetch_market_snapshot_lines()
    technical_lines = _fetch_market_technical_lines()
    if snapshot_lines:
        market_lines.extend(snapshot_lines)
    if technical_lines:
        if market_lines:
            market_lines.append("")
        market_lines.extend(technical_lines)

    rankings = _get_market_rankings(db)
    if rankings.get("gainers"):
        if market_lines:
            market_lines.append("")
        market_lines.append("🟢 全市場漲幅 Top 5")
        market_lines.extend(
            f"{idx}. {_format_market_rank(item)}"
            for idx, item in enumerate(rankings["gainers"][:5], 1)
        )
    if rankings.get("decliners"):
        if market_lines:
            market_lines.append("")
        market_lines.append("🔴 全市場跌幅 Top 5")
        market_lines.extend(
            f"{idx}. {_format_market_rank(item)}"
            for idx, item in enumerate(rankings["decliners"][:5], 1)
        )

    reports: list[dict] = []
    if should_send_portfolio and portfolio_sections:
        reports.append({
            "event_id": portfolio_event_id,
            "text": "📊 Portfolio 收盤日報\n\n" + "\n\n".join(portfolio_sections),
        })
    if should_send_market and market_lines:
        reports.append({
            "event_id": market_event_id,
            "text": "🌐 全市場收盤分析\n\n" + "\n".join(market_lines),
        })

    return reports


def _collect_weekly_portfolio_reports(db: Session, seen: dict[str, float]) -> list[dict]:
    now = _taipei_now()
    if not _should_send_weekly_portfolio_report(now):
        return []

    week_key = _iso_week_key(now.date())
    legacy_event_id = f"telegram:portfolio-weekly:{week_key}"
    portfolio_event_id = f"telegram:portfolio-weekly:{week_key}:portfolio"
    market_event_id = f"telegram:portfolio-weekly:{week_key}:market"
    legacy_sent = _seen_recently(seen, legacy_event_id, 7 * TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS)
    should_send_portfolio = not legacy_sent and not _seen_recently(
        seen,
        portfolio_event_id,
        7 * TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS,
    )
    should_send_market = not legacy_sent and not _seen_recently(
        seen,
        market_event_id,
        7 * TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS,
    )
    if not should_send_portfolio and not should_send_market:
        return []

    from .portfolio import get_csv_portfolio, _generate_csv_portfolio_report

    payload = get_csv_portfolio(name=None, db=db)
    portfolios = payload.get("portfolios", {}) if isinstance(payload, dict) else {}
    names = payload.get("portfolio_names", []) if isinstance(payload, dict) else []

    portfolio_sections: list[str] = []
    for name in names:
        current = portfolios.get(name)
        if not current:
            continue
        try:
            report = _generate_csv_portfolio_report(portfolio=name, period="weekly", db=db)
        except Exception as exc:
            portfolio_sections.append(f"{name}\n週報產生失敗：{exc}")
            continue
        portfolio_sections.append(_build_portfolio_report_section(report, current, period_label="本週"))

    market_lines: list[str] = []
    snapshot_lines = _fetch_weekly_market_snapshot_lines()
    technical_lines = _fetch_market_technical_lines()
    if snapshot_lines:
        market_lines.extend(snapshot_lines)
    if technical_lines:
        if market_lines:
            market_lines.append("")
        market_lines.extend(technical_lines)

    rankings = _get_weekly_market_rankings(db)
    if rankings.get("gainers"):
        if market_lines:
            market_lines.append("")
        market_lines.append("本週全市場漲幅 Top 10")
        market_lines.extend(_format_weekly_market_rank(item) for item in rankings["gainers"])
    if rankings.get("decliners"):
        if market_lines:
            market_lines.append("")
        market_lines.append("本週全市場跌幅 Top 10")
        market_lines.extend(_format_weekly_market_rank(item) for item in rankings["decliners"])

    reports: list[dict] = []
    if should_send_portfolio and portfolio_sections:
        reports.append({
            "event_id": portfolio_event_id,
            "text": "Portfolio 週末復盤\n" + "\n\n".join(portfolio_sections),
        })
    if should_send_market and market_lines:
        reports.append({
            "event_id": market_event_id,
            "text": "全市場週末復盤\n" + "\n".join(market_lines),
        })

    return reports


def _compose_digest(
    earnings: list[dict],
    portfolio_movers: list[dict],
    valuation_movers: list[dict],
) -> str | None:
    sections: list[str] = []

    if earnings:
        lines = [
            f"{item['ticker']}：{item['when_text']}（{item['date']}）"
            for item in earnings
        ]
        sections.append("財報提醒（只含持倉）\n" + "\n".join(lines))

    if portfolio_movers:
        section = _format_mover_section("Portfolio 異常波動", portfolio_movers)
        if section:
            sections.append(section)

    if valuation_movers:
        section = _format_mover_section("Valuation 追蹤異常波動", valuation_movers)
        if section:
            sections.append(section)

    if not sections:
        return None

    return f"StockRadar 通知摘要 {_taipei_now().date().isoformat()}\n\n" + "\n\n".join(sections)


def _mark_digest_seen(
    seen: dict[str, float],
    earnings: list[dict],
    portfolio_movers: list[dict],
    valuation_movers: list[dict],
) -> None:
    for item in [*earnings, *portfolio_movers, *valuation_movers]:
        event_id = item.get("event_id")
        if event_id:
            _mark_seen(seen, event_id)


# ── Claude post-market analysis ───────────────────────────────────────────────

CLAUDE_ANALYSIS_EVENT_PREFIX = "telegram:claude-analysis:"

# 依賴式工作流的狀態機（scripts/pipeline_state.py，純標準函式庫）。
# 固定時間只當「最晚啟動時間」：前置階段一完成就立刻接著跑，不用空等到整點，
# 也不會出現 A 還在跑、B 已到點的搶資源情況。
_SCRIPTS_ROOT = Path(__file__).resolve().parents[3]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))
try:
    from scripts import pipeline_state
except Exception as _exc:      # pragma: no cover - 匯入失敗時退回純時間排程
    pipeline_state = None
    print(f"[pipeline] 狀態機不可用，退回固定時間排程：{_exc}")


# 戰情官復盤要覆蓋全部 5 個美股交易日：台北週二～週五對應美股週一～週四收盤，
# 台北週六（weekday 5）補上美股週五收盤——否則週五收盤（週末跳空風險最高的一天）永遠沒人復盤。
CLAUDE_ANALYSIS_WEEKDAYS = DAILY_PORTFOLIO_REPORT_WEEKDAYS | {5}
# ⚠️ 這幾個時間常數自 2026-08-24 起只保留給既有引用與說明用；
# 真正的排程權威在 scripts/pipeline_state.STAGES（那裡的 latest_start 是「最晚啟動時間」，
# 前置階段一完成就會提早起跑）。要改排程請改那裡，不要改這裡。
MARKET_SCAN_TIME = dt_time(6, 0)   # ＝ pipeline_state 的 market_scan.latest_start
MARKET_SCAN_EVENT_PREFIX = "telegram:market-scan:"
MARKET_NEWS_TIME = dt_time(6, 40)  # ＝ pipeline_state 的 market_news.latest_start
MARKET_NEWS_EVENT_PREFIX = "telegram:market-news:"


def _should_send_claude_analysis(now: datetime, seen: dict) -> bool:
    """Run once per US trading day at/after DAILY_PORTFOLIO_REPORT_TIME."""
    if now.weekday() not in CLAUDE_ANALYSIS_WEEKDAYS:
        return False
    if now.time() < DAILY_PORTFOLIO_REPORT_TIME:
        return False
    event_id = f"{CLAUDE_ANALYSIS_EVENT_PREFIX}{now.date().isoformat()}"
    return not _seen_recently(seen, event_id, TELEGRAM_NOTIFICATION_COOLDOWN_SECONDS)


def _alert_pipeline_failure(stage: str, detail: str, *, retry_hint: str = "",
                            manual_cmd: str = "uv run python scripts/war_room.py") -> None:
    """復盤/掃描管線掛掉時發 Telegram 告警——失敗絕不可靜默，否則整天沒復盤也沒人知道。"""
    try:
        _send_telegram_message(
            f"⚠️ {stage} 失敗（{_taipei_now().strftime('%m/%d %H:%M')}）\n\n"
            f"{detail[:600]}\n\n"
            + (retry_hint + "\n\n" if retry_hint else "")
            + "手動補跑：\n"
            f"cd ~/Projects/Arthur_App/stock-radar && {manual_cmd}"
        )
    except Exception as exc:
        print(f"[alert] 告警發送也失敗：{exc}")


def _sync_external_stages(today: date) -> None:
    """launchd 擁有的階段（AI 持倉卡）不會自己回報，靠產物存在與否補登。

    沒有這一步，war_room 的前置永遠不會滿足，就退化成純時間排程（05:30 才起跑）。
    """
    if pipeline_state is None:
        return
    try:
        if pipeline_state.is_done(today, "ai_cards"):
            return
        card = settings.DATA_DIR / "ai_reports" / f"{today.isoformat()}.json"
        if card.exists():
            pipeline_state.mark_result(today, "ai_cards", pipeline_state.STATUS_SUCCESS,
                                       f"launchd 產物存在：{card.name}")
    except Exception as exc:
        print(f"[pipeline] 外部階段補登失敗（略過）：{exc}")


def _run_pipeline_stage(stage_key: str, *, script: str, args: list[str],
                        timeout: int, label: str,
                        manual_cmd: str | None = None) -> str | None:
    """依賴式工作流的統一 runner。

    gating 交給 scripts/pipeline_state（前置完成 or 到最晚啟動時間；含重試次數與冷卻），
    這裡只負責跑腳本並回報結果。回傳 event_id（供既有 seen 去重沿用），沒跑就 None。
    """
    import subprocess

    now = _taipei_now()
    today = now.date()

    if pipeline_state is not None:
        ok, why = pipeline_state.should_run(today, stage_key, now=now)
        if not ok:
            return None
        print(f"[pipeline] {label} 起跑：{why}")

    # claude 額度守門：鎖著就先不起跑，也不動用重試次數 —— 這個 loop 本來就會反覆呼叫，
    # 等額度 reset 之後的某一輪自然會通過。避免在死區裡把 max_attempts 燒光。
    if (until := _guard.blocked_until()):
        from datetime import datetime as _dt
        print(f"[{stage_key}] claude 額度未恢復（{_dt.fromtimestamp(until):%m-%d %H:%M} reset），本輪跳過")
        return None

    scripts_dir = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
    script_path = scripts_dir / script
    if not script_path.exists():
        print(f"[{stage_key}] Script not found: {script_path}")
        if pipeline_state is not None:
            pipeline_state.mark_result(today, stage_key, pipeline_state.STATUS_FAILED,
                                       f"找不到腳本 {script_path}")
        return None

    if pipeline_state is not None:
        pipeline_state.mark_start(today, stage_key)
    attempts = 0
    max_attempts = 1
    if pipeline_state is not None:
        stage = pipeline_state.STAGE_BY_KEY.get(stage_key)
        max_attempts = stage.max_attempts if stage else 1
        attempts = (pipeline_state.load(today)["stages"].get(stage_key) or {}).get("attempts", 1)

    event_id = f"telegram:{stage_key}:{today.isoformat()}"
    status = pipeline_state.STATUS_SUCCESS if pipeline_state else None
    detail = None
    try:
        result = subprocess.run(
            ["uv", "run", "python", str(script_path), *args],
            cwd=str(script_path.parent.parent),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            detail = ((result.stderr or result.stdout or "無輸出").strip().splitlines() or ["無輸出"])[-1]
            print(f"[{stage_key}] Non-zero exit:\n{(result.stderr or '')[:2000]}")
            status = pipeline_state.STATUS_FAILED if pipeline_state else None
            try:
                # 把 reset 時間回填共用 state，下一輪的 gate 就會擋住，不會空轉重試
                _guard.check((result.stdout or "") + (result.stderr or ""), job=stage_key)
            except _guard.QuotaExceeded as qexc:
                detail = f"claude 額度用罄，{qexc.resets_at_dt:%m-%d %H:%M} reset 後自動重試" \
                    if qexc.resets_at_dt else "claude 額度用罄，reset 時間未知"
                print(f"[{stage_key}] {detail}")
        else:
            print(f"[{stage_key}] Completed successfully")
    except subprocess.TimeoutExpired:
        detail = f"執行超時（>{timeout // 60} 分鐘）"
        print(f"[{stage_key}] {detail}")
        status = pipeline_state.STATUS_FAILED if pipeline_state else None
    except Exception as exc:
        detail = f"啟動失敗：{exc}"
        print(f"[{stage_key}] {detail}")
        status = pipeline_state.STATUS_FAILED if pipeline_state else None

    if pipeline_state is not None:
        pipeline_state.mark_result(today, stage_key, status, detail)

    if detail:
        remaining = max(0, max_attempts - attempts)
        hint = (f"還會自動重試 {remaining} 次（冷卻後）。" if remaining
                else "重試次數已用完，今天不會再自動跑。")
        _alert_pipeline_failure(label, detail, retry_hint=hint,
                                manual_cmd=manual_cmd or f"uv run python scripts/{script}")
    return event_id


def _extract_mimi_sections(tickers: list[str]) -> str:
    """
    Parse mimi操作建議總表.md and return only the sections whose
    level-4 header starts with a ticker that is in the portfolio.
    """
    doc_path = Path(settings.MIMI_DOC_PATH).expanduser()
    if not doc_path.exists():
        return ""
    text = doc_path.read_text(encoding="utf-8")

    ticker_set = {t.upper() for t in tickers}
    sections: list[str] = []
    # Split on level-4 headers; keep the header with its content
    parts = text.split("\n#### ")
    for part in parts[1:]:  # skip preamble before first ####
        first_line = part.split("\n")[0]
        # Header format: "TICKER（...）— description"
        token = first_line.split("（")[0].split("—")[0].strip().upper()
        if token in ticker_set:
            sections.append("#### " + part.rstrip())
    return "\n\n---\n\n".join(sections)


def _format_holdings_for_claude(portfolios: dict) -> str:
    """Format all portfolio holdings into a compact text table for the Claude prompt."""
    lines: list[str] = []
    for pname, pdata in portfolios.items():
        lines.append(f"【{pname}】總值 {pdata.get('summary', {}).get('total_value', 'N/A'):,.0f}")
        for h in pdata.get("holdings", []):
            t = h.get("ticker", "")
            if t == "CASH":
                lines.append(f"  CASH  ${h.get('cost_basis', 0):,.0f}")
                continue
            price = h.get("current_price")
            chg = h.get("day_change_pct")
            ret = h.get("return_pct")
            wt = h.get("weight_pct")
            val = h.get("market_value")
            lines.append(
                f"  {t:<6}  "
                f"${price:>8.2f}  " if price else f"  {t:<6}  N/A        "
            )
            # rebuild as single cleaner line
            price_str = f"${price:.2f}" if price else "N/A"
            chg_str = f"{chg:+.1f}%" if chg is not None else "—"
            ret_str = f"{ret:+.1f}%" if ret is not None else "—"
            wt_str = f"{wt:.1f}%" if wt is not None else "—"
            val_str = f"${val:,.0f}" if val else "—"
            lines[-1] = f"  {t:<6}  {price_str:<10} 今日{chg_str:<8} 總報酬{ret_str:<8} 權重{wt_str:<6} 市值{val_str}"
    return "\n".join(lines)


def _send_long_telegram_message(text: str) -> None:
    """Split text at paragraph boundaries to stay under Telegram's 4096-char limit."""
    LIMIT = 3900
    if len(text) <= LIMIT:
        _send_telegram_message(text)
        return
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in text.split("\n\n"):
        para_len = len(para) + 2
        if current_len + para_len > LIMIT and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len
    if current:
        chunks.append("\n\n".join(current))
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(1)
        _send_telegram_message(chunk)


def _generate_claude_daily_analysis(db: Session, seen: dict) -> str | None:
    """
    Build a Claude-powered post-market analysis and return the event_id if sent.
    Returns None if conditions not met or on error.
    """
    now = _taipei_now()
    if not _should_send_claude_analysis(now, seen):
        return None

    if not settings.ANTHROPIC_API_KEY:
        print("[claude] ANTHROPIC_API_KEY not set — skipping analysis")
        return None

    try:
        import anthropic
    except ImportError:
        print("[claude] anthropic package not installed")
        return None

    event_id = f"{CLAUDE_ANALYSIS_EVENT_PREFIX}{now.date().isoformat()}"
    date_str = now.date().isoformat()

    # ── Gather data ────────────────────────────────────────────────────────────
    try:
        from .portfolio import get_csv_portfolio, _generate_csv_portfolio_report
        payload = get_csv_portfolio(name=None, db=db)
        portfolios: dict = payload.get("portfolios", {}) if isinstance(payload, dict) else {}
        names: list = payload.get("portfolio_names", []) if isinstance(payload, dict) else []
    except Exception as exc:
        print(f"[claude] Failed to load portfolio: {exc}")
        return None

    all_tickers = list({
        h.get("ticker", "")
        for pdata in portfolios.values()
        for h in pdata.get("holdings", [])
        if h.get("ticker") not in ("", "CASH")
    })

    # Daily report data (technical signals + risk flags)
    report_sections: list[str] = []
    for name in names:
        try:
            report = _generate_csv_portfolio_report(portfolio=name, period="daily", db=db)
            abnormal = ((report.get("abnormal_stocks") or {}).get("summary") or [])[:8]
            flags = report.get("risk_flags", [])[:4]
            if abnormal:
                report_sections.append(
                    f"【{name}】異常股票\n"
                    + "\n".join(
                        "• "
                        + item.get("ticker", "")
                        + "｜"
                        + "、".join(
                            sig.get("label", "")
                            for sig in (item.get("signals") or [])[:3]
                            if sig.get("label")
                        )
                        for item in abnormal
                    )
                )
            elif flags:
                report_sections.append(
                    f"【{name}】風險提示\n"
                    + "\n".join(f"• {f['message']}" for f in flags)
                )
        except Exception:
            pass

    # Market snapshot
    market_lines = _fetch_market_snapshot_lines() + _fetch_market_technical_lines()
    market_text = "\n".join(market_lines) if market_lines else "（市場資料暫時無法取得）"

    holdings_text = _format_holdings_for_claude(portfolios)
    mimi_sections = _extract_mimi_sections(all_tickers)
    risk_text = "\n\n".join(report_sections) if report_sections else "（無特殊訊號）"

    # ── Build prompt ───────────────────────────────────────────────────────────
    system_prompt = (
        "你是 Arthur 的專屬投資分析助理。"
        "以下是 Mimi 的操作策略總表，作為分析的策略基準，"
        "包含各標的的建議進場區間、停損、目標倉位與操作邏輯：\n\n"
        + (mimi_sections if mimi_sections else "（與持倉相關的 Mimi 策略暫無資料）")
    )

    user_prompt = f"""今天是 {date_str}（台北時間），美股剛剛收盤。

## 大盤收盤快照
{market_text}

## 我的持倉
{holdings_text}

## 今日技術訊號與風險提示
{risk_text}

---
請依以下格式回應（繁體中文，每段簡潔，總長不超過 800 字）：

**📊 大盤判讀**
（2-3句：今日趨勢、情緒、關鍵訊號）

**🎯 持倉重點關注**
（最多3個持倉標的，各1-2句：今日表現 ＋ 對照 Mimi 策略建議）

**📋 明日操作計劃**
（具體動作：加倉 / 減倉 / 觀察 / 設停損，每條1句，說明觸發條件）

**⚡ 一句話總結**
（整體策略基調）"""

    # ── Call Claude with prompt caching on the mimi doc ───────────────────────
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        analysis = next(
            (block.text.strip() for block in response.content if hasattr(block, "text")),
            None,
        )
        if not analysis:
            print("[claude] Empty response from API")
            return None
    except Exception as exc:
        print(f"[claude] API call failed: {exc}")
        return None

    # ── Send via Telegram + LINE ───────────────────────────────────────────────
    header = f"🤖 Claude 收盤分析 {date_str}\n"
    try:
        _send_long_notification_message(header + analysis)
    except Exception as exc:
        print(f"[claude] Notification send failed: {exc}")
        return None

    return event_id


def _trigger_daily_analysis_script(seen: dict) -> str | None:
    """Run scripts/war_room.py（戰情中心，claude -p 自主蒐集情報）once per trading day.

    排程由 scripts/pipeline_state 的依賴式工作流決定：AI 持倉卡（launchd）一完成就可起跑，
    最遲 05:30 無論如何都會開始；失敗會依 max_attempts 自動重試（冷卻 20 分鐘），
    不再「當天永久放棄」。腳本自己處理 Telegram/LINE 發送。
    """
    return _run_pipeline_stage(
        "war_room", script="war_room.py", args=["--no-obsidian"],
        # war_room 的 claude 自主蒐集情報（tool-use 多輪）比舊版一次性 prompt 慢，放寬到 40 分鐘
        timeout=2400, label="盤後復盤（war_room）",
    )


def _trigger_market_scan_script(seen: dict) -> str | None:
    """每個美股交易日收盤後跑一次 scripts/market_scan.py（機會獵手）。

    復盤顧持倉，這支顧「新機會」——**依賴 war_room 完成才起跑**（不再兩條一起搶 claude／
    yfinance；復盤 06:10 才結束時掃描就 06:10 接著跑，而不是 06:00 硬闖）。
    掃描腳本自己發 Telegram 精華（含 🆕 首次入圍）並落地 opportunities journal，
    隔天 war_room 簡報的「🔭 機會掃描回顧」會回頭追這些機會。
    ⚠️ 掃描產出的是**研究候選**，不是交易指令——買賣結論只由 war_room 給。
    """
    return _run_pipeline_stage(
        "market_scan", script="market_scan.py", args=[],
        timeout=2400, label="機會掃描（market_scan）",
    )


def _trigger_market_news_script(seen: dict) -> str | None:
    """每個美股交易日早上跑一次 scripts/market_news.py（當日市場新聞策展）。

    復盤看持倉、掃描看機會，這支只回答「今天發生了什麼我該知道的事」：大盤/宏觀 +
    持倉與觀察名單的當日新聞，claude 策展後落地 data/market-news/{date}.json，
    portal 每日報告的「市場新聞」分頁直接讀。**只給事實與來源，不給持倉操作建議。**
    不推 Telegram（要推加 --telegram）。依賴 market_scan 完成，最遲 06:40 起跑。
    """
    return _run_pipeline_stage(
        "market_news", script="market_news.py", args=[],
        timeout=1500, label="市場新聞（market_news）",
    )


def _trigger_completeness_check(seen: dict) -> str | None:
    """管線收尾：算當日完整性 manifest，缺件就推 Telegram。

    這是「不能讓使用者自己從畫面猜今天缺什麼」的落實：manifest 同時寫進
    data/agent/manifest/{date}.json 與鏡像目錄 _manifest.json（portal 讀後者）。
    """
    return _run_pipeline_stage(
        "completeness", script="daily_manifest.py", args=["--telegram"],
        timeout=300, label="完整性檢查（daily_manifest）",
        manual_cmd="uv run python scripts/daily_manifest.py --telegram",
    )


async def telegram_notification_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            seen = _load_seen()
            _sync_external_stages(_taipei_now().date())
            portfolio_tickers = _portfolio_tickers()
            earnings = _collect_due_earnings(db, seen, portfolio_tickers)
            portfolio_movers, valuation_movers = _collect_market_movers(db, seen, portfolio_tickers)
            daily_reports = _collect_daily_portfolio_reports(db, seen)
            weekly_reports = _collect_weekly_portfolio_reports(db, seen)
            message = _compose_digest(earnings, portfolio_movers, valuation_movers)

            if message:
                _send_notification_message(message)
                _mark_digest_seen(seen, earnings, portfolio_movers, valuation_movers)
                _save_seen(seen)

            for report in daily_reports:
                _send_notification_message(f"StockRadar 收盤通知 {_taipei_now().date().isoformat()}\n\n{report['text']}")
                _mark_seen(seen, report["event_id"])
                _save_seen(seen)

            for report in weekly_reports:
                _send_notification_message(f"StockRadar 週末復盤 {_iso_week_key(_taipei_now().date())}\n\n{report['text']}")
                _mark_seen(seen, report["event_id"])
                _save_seen(seen)

            claude_event_id = _trigger_daily_analysis_script(seen)
            if claude_event_id:
                _mark_seen(seen, claude_event_id)
                _save_seen(seen)

            scan_event_id = _trigger_market_scan_script(seen)
            if scan_event_id:
                _mark_seen(seen, scan_event_id)
                _save_seen(seen)

            news_event_id = _trigger_market_news_script(seen)
            if news_event_id:
                _mark_seen(seen, news_event_id)
                _save_seen(seen)

            manifest_event_id = _trigger_completeness_check(seen)
            if manifest_event_id:
                _mark_seen(seen, manifest_event_id)
                _save_seen(seen)

        except Exception as exc:
            print(f"[notifications] Telegram notification loop error: {exc}")
        finally:
            db.close()

        await asyncio.sleep(TELEGRAM_NOTIFICATION_INTERVAL_SECONDS)
