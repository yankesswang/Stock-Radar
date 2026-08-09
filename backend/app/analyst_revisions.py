"""
分析師預期修正 — answers one question: are analysts raising or lowering?

Four evidence streams, all from data already on disk:

1. Consensus EPS per **fiscal year**, tracked across forecast snapshots
   (data/forecast/detail/{TICKER}_forecast_{date}.json → forecast_ranges.eps).
   FY-labelled, so it is the only stream where a change is unambiguously a
   revision. Primary signal.
2. Average price target across those same snapshots.
3. Rating-distribution drift (% bullish) — periods are merged across snapshots,
   newest file wins, which stretches the usual 6-month window to ~12.
4. Individual analyst actions (Upgrades / Downgrades / "$276→$287" target moves).

A dense day-by-day consensus series also comes from the `valuations` table, but
`current_year_eps` there is **not** fiscal-year labelled — when the data source
rolls to the next fiscal year the number jumps 15-70% overnight. Those jumps are
detected and marked as breaks so they never read as a revision.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from .config import settings

FORECAST_DIR: Path = settings.DATA_DIR / "forecast" / "detail"

# A single-step EPS move larger than this is a fiscal-year roll or a data-source
# swap, not an analyst revision. Real post-earnings revisions stay well under it.
BREAK_THRESHOLD_PCT = 15.0

# Window for counting individual analyst actions, relative to the newest snapshot.
ACTION_WINDOW_DAYS = 90

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ── parsing helpers ───────────────────────────────────────────────────────────

def _parse_rec_date(raw: str | None) -> date | None:
    """'Aug 6, 2026' → date(2026, 8, 6)."""
    if not raw:
        return None
    m = re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})", raw.strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def _parse_period(raw: str | None) -> date | None:
    """\"Aug '26\" → date(2026, 8, 1) — used to sort/merge rating periods."""
    if not raw:
        return None
    m = re.match(r"([A-Za-z]{3})[a-z]*\s*'?(\d{2,4})", raw.strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    year = int(m.group(2))
    if year < 100:
        year += 2000
    return date(year, month, 1)


def _parse_target(raw: str | None) -> tuple[float | None, float | None]:
    """'$276→$287' → (276, 287);  '$300' → (None, 300);  'n/a' → (None, None)."""
    if not raw or raw.strip().lower() in {"n/a", "na", "-", ""}:
        return None, None
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d+(?:[\d,]*\.?\d*)", raw)]
    if not nums:
        return None, None
    if len(nums) >= 2 and ("→" in raw or "->" in raw or "»" in raw):
        return nums[0], nums[1]
    return None, nums[-1]


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _direction(pct: float | None, threshold: float = 0.5) -> str:
    if pct is None:
        return "unknown"
    if pct > threshold:
        return "up"
    if pct < -threshold:
        return "down"
    return "flat"


# ── snapshot loading ──────────────────────────────────────────────────────────

def _load_snapshots(ticker: str) -> list[tuple[date, dict]]:
    """Every forecast JSON for *ticker*, oldest first."""
    seen: dict[date, dict] = {}
    for pattern in (f"{ticker.upper()}_forecast_*.json", f"{ticker.lower()}_forecast_*.json"):
        for path in FORECAST_DIR.glob(pattern):
            m = re.search(r"_forecast_(\d{4}-\d{2}-\d{2})\.json$", path.name)
            if not m:
                continue
            try:
                snap_date = date.fromisoformat(m.group(1))
                with open(path, encoding="utf-8") as f:
                    seen[snap_date] = json.load(f)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return sorted(seen.items())


# ── stream 1: FY-aligned consensus EPS ────────────────────────────────────────

def _eps_by_fiscal_year(snapshots: list[tuple[date, dict]]) -> list[dict]:
    """One entry per forecast fiscal year, with its estimate history."""
    by_fy: dict[int, dict[date, float]] = {}

    for snap_date, payload in snapshots:
        ranges = ((payload.get("forecast_ranges") or {}).get("eps")) or {}
        years, avgs = ranges.get("years") or [], ranges.get("avg") or []
        pairs = list(zip(years, avgs))

        if not pairs:  # older scrapes lack forecast_ranges — fall back to the table
            af = payload.get("annual_forecast") or {}
            periods, eps = af.get("periods") or [], af.get("eps") or []
            pairs = [
                (p.replace("FY", "").strip(), e)
                for p, e in zip(periods, eps)
                # keep forward years only; earlier columns are reported actuals
                if str(p).strip().replace("FY", "").strip().isdigit()
                and int(str(p).strip().replace("FY", "").strip()) >= snap_date.year
            ]

        for year, avg in pairs:
            try:
                fy = int(str(year).strip())
            except (TypeError, ValueError):
                continue
            if avg is None or not isinstance(avg, (int, float)) or avg <= 0:
                continue
            by_fy.setdefault(fy, {})[snap_date] = round(float(avg), 4)

    out = []
    for fy in sorted(by_fy)[:4]:
        points = [{"date": d.isoformat(), "eps": v} for d, v in sorted(by_fy[fy].items())]
        first, latest = points[0]["eps"], points[-1]["eps"]
        change = _pct(latest, first)
        out.append({
            "fiscal_year": fy,
            "points": points,
            "first": first,
            "first_date": points[0]["date"],
            "latest": latest,
            "latest_date": points[-1]["date"],
            "change_pct": change,
            "direction": _direction(change),
        })
    return out


# ── stream 2: price target ────────────────────────────────────────────────────

def _price_target_series(snapshots: list[tuple[date, dict]]) -> list[dict]:
    series = []
    for snap_date, payload in snapshots:
        pt = payload.get("price_target") or {}
        avg = pt.get("average")
        if not isinstance(avg, (int, float)) or avg <= 0:
            continue
        series.append({
            "date": snap_date.isoformat(),
            "avg": round(float(avg), 2),
            "median": pt.get("median"),
            "high": pt.get("high"),
            "low": pt.get("low"),
        })
    return series


# ── stream 3: rating distribution drift ───────────────────────────────────────

_RATING_KEYS = ("strong_buy", "buy", "hold", "sell", "strong_sell")


def _ratings_trend(snapshots: list[tuple[date, dict]]) -> dict | None:
    """
    Stitch each snapshot's 6-month rating window into one longer series.

    Walks newest → oldest so the freshest numbers win. An older snapshot is only
    accepted if its coverage count agrees with what we already have on the
    overlapping months — early scrapes captured a partial analyst universe
    (e.g. 17 analysts where the current file reports 46), and splicing those in
    would fake a collapse in coverage halfway through the chart.
    """
    def _window(payload: dict) -> dict[date, dict]:
        ratings = payload.get("analyst_ratings") or {}
        periods = ratings.get("periods") or []
        out: dict[date, dict] = {}
        for i, label in enumerate(periods):
            pd = _parse_period(label)
            if not pd:
                continue
            counts = {k: (ratings.get(k) or [None] * len(periods))[i] or 0 for k in _RATING_KEYS}
            if sum(counts.values()) == 0:
                continue
            out[pd] = {"period": label, **counts}
        return out

    merged: dict[date, dict] = {}
    for _, payload in reversed(snapshots):  # newest first
        window = _window(payload)
        if not window:
            continue
        overlap = set(window) & set(merged)
        if merged:
            if not overlap:
                break  # gap in coverage — can't verify the splice, so stop here
            diffs = []
            for pd in overlap:
                have = sum(merged[pd][k] for k in _RATING_KEYS)
                incoming = sum(window[pd][k] for k in _RATING_KEYS)
                diffs.append(abs(incoming - have) / have if have else 1.0)
            if sorted(diffs)[len(diffs) // 2] > 0.25:
                break  # different analyst universe — older history is not comparable
        for pd, row in window.items():
            merged.setdefault(pd, row)  # never overwrite a fresher value

    if len(merged) < 2:
        return None

    rows = []
    for pd in sorted(merged):
        row = merged[pd]
        total = sum(row[k] for k in _RATING_KEYS)
        rows.append({
            **row,
            "total": total,
            "bull_pct": round((row["strong_buy"] + row["buy"]) / total * 100, 1) if total else None,
        })

    first, last = rows[0], rows[-1]
    bull_change = None
    if first["bull_pct"] is not None and last["bull_pct"] is not None:
        bull_change = round(last["bull_pct"] - first["bull_pct"], 1)

    return {
        "rows": rows,
        "bull_pct_latest": last["bull_pct"],
        "bull_pct_first": first["bull_pct"],
        "bull_pct_change": bull_change,
        "total_latest": last["total"],
        "direction": _direction(bull_change, threshold=2.0),
    }


# ── stream 4: individual analyst actions ──────────────────────────────────────

def _analyst_actions(snapshots: list[tuple[date, dict]]) -> tuple[list[dict], dict]:
    """Union of every snapshot's recommendation list, newest action first."""
    seen: dict[tuple, dict] = {}
    for _, payload in snapshots:
        for rec in payload.get("analyst_recommendations") or []:
            action_date = _parse_rec_date(rec.get("date"))
            if not action_date:
                continue
            firm = (rec.get("firm") or "").strip()
            raw_target = rec.get("price_target")
            key = (action_date, firm, (rec.get("rating") or "").strip(), (rec.get("action") or "").strip())
            target_from, target_to = _parse_target(raw_target)

            action = (rec.get("action") or "").strip()
            low = action.lower()
            if "upgrade" in low:
                direction = "up"
            elif "downgrade" in low:
                direction = "down"
            else:
                direction = _direction(_pct(target_to, target_from), threshold=0.0)
                if direction == "unknown":
                    direction = "flat"

            analyst = (rec.get("analyst") or "").strip()
            if analyst.lower().startswith("unknown analyst"):
                analyst = ""

            seen[key] = {
                "date": action_date.isoformat(),
                "firm": firm,
                "analyst": analyst,
                "rating": (rec.get("rating") or "").strip(),
                "action": action,
                "price_target": raw_target,
                "target_from": target_from,
                "target_to": target_to,
                "target_change_pct": _pct(target_to, target_from),
                "upside": rec.get("upside"),
                "direction": direction,
            }

    actions = sorted(seen.values(), key=lambda a: a["date"], reverse=True)

    counts = {"window_days": ACTION_WINDOW_DAYS, "raised": 0, "lowered": 0,
              "upgrades": 0, "downgrades": 0, "maintains": 0, "total": 0}
    if actions:
        cutoff = (date.fromisoformat(actions[0]["date"]) - timedelta(days=ACTION_WINDOW_DAYS)).isoformat()
        for a in actions:
            if a["date"] < cutoff:
                continue
            counts["total"] += 1
            low = a["action"].lower()
            if "upgrade" in low:
                counts["upgrades"] += 1
            elif "downgrade" in low:
                counts["downgrades"] += 1
            else:
                counts["maintains"] += 1
            if a["target_change_pct"] is not None:
                if a["target_change_pct"] > 0:
                    counts["raised"] += 1
                elif a["target_change_pct"] < 0:
                    counts["lowered"] += 1

    return actions, counts


# ── dense consensus series from the valuations table ──────────────────────────

def _consensus_trend(rows: list) -> dict:
    """
    Step series of the daily-ish consensus EPS, with fiscal-year rolls flagged.

    `rows` are Valuation ORM objects ordered by snapshot_date ascending.
    """
    raw: list[dict] = []
    last_date: str | None = None

    for v in rows:
        cur = v.current_year_eps if v.current_year_eps and v.current_year_eps > 0 else None
        nxt = v.next_year_eps if v.next_year_eps and v.next_year_eps > 0 else None
        # The scraper writes next == current when the next-year estimate is missing.
        if nxt is not None and cur is not None and abs(nxt - cur) < 1e-9:
            nxt = None
        if cur is None:
            continue

        cur, nxt = round(cur, 4), (round(nxt, 4) if nxt is not None else None)
        last_date = v.snapshot_date.isoformat()

        # Only keep the day an estimate actually moved — the repeats in between
        # are stale re-scrapes, not new analyst input.
        if raw and raw[-1]["current_year_eps"] == cur and raw[-1]["next_year_eps"] == nxt:
            continue

        raw.append({
            "date": last_date,
            "current_year_eps": cur,
            "next_year_eps": nxt,
            "price": v.current_price if v.current_price and v.current_price > 0 else None,
        })

    # Drop one-off scraper garbage: a value that disagrees wildly with both
    # neighbours while those neighbours agree with each other (e.g. a single
    # TWD-denominated EPS in a USD series). A real fiscal-year roll persists,
    # so it never matches this shape and survives as a break below.
    dropped = 0
    cleaned = []
    i = 0
    while i < len(raw):
        prev = cleaned[-1] if cleaned else None
        nxt_row = raw[i + 1] if i + 1 < len(raw) else None
        if prev and nxt_row:
            a = _pct(raw[i]["current_year_eps"], prev["current_year_eps"])
            b = _pct(nxt_row["current_year_eps"], raw[i]["current_year_eps"])
            c = _pct(nxt_row["current_year_eps"], prev["current_year_eps"])
            if (a is not None and b is not None and c is not None
                    and abs(a) > BREAK_THRESHOLD_PCT and abs(b) > BREAK_THRESHOLD_PCT
                    and abs(c) <= BREAK_THRESHOLD_PCT):
                dropped += 1
                i += 1
                continue
        cleaned.append(raw[i])
        i += 1

    points: list[dict] = []
    prev_cur: float | None = None
    for row in cleaned:
        step = _pct(row["current_year_eps"], prev_cur)
        is_break = step is not None and abs(step) > BREAK_THRESHOLD_PCT
        points.append({**row, "step_pct": None if is_break else step,
                       "is_break": is_break, "carry": False})
        prev_cur = row["current_year_eps"]

    # Extend the step line to the newest snapshot so the chart doesn't stop
    # at whenever the estimate last moved.
    if points and last_date and points[-1]["date"] != last_date:
        points.append({**points[-1], "date": last_date, "step_pct": 0.0,
                       "is_break": False, "carry": True})

    # Most recent stretch with no fiscal-year roll — the only comparable window.
    seg_start = 0
    for i, p in enumerate(points):
        if p["is_break"]:
            seg_start = i
    segment = points[seg_start:]

    momentum = {}
    if segment:
        latest = segment[-1]
        latest_date = date.fromisoformat(latest["date"])
        for label, days in (("1m", 30), ("3m", 90), ("6m", 180)):
            target = latest_date - timedelta(days=days)
            prior = [p for p in segment if date.fromisoformat(p["date"]) <= target]
            momentum[label] = _pct(latest["current_year_eps"], prior[-1]["current_year_eps"]) if prior else None
        momentum["since_segment_start"] = _pct(latest["current_year_eps"], segment[0]["current_year_eps"])
        momentum["segment_start_date"] = segment[0]["date"]

    return {
        "points": points,
        "breaks": [p["date"] for p in points if p["is_break"]],
        "outliers_dropped": dropped,
        "momentum": momentum,
        "latest": segment[-1] if segment else None,
    }


# ── verdict ───────────────────────────────────────────────────────────────────

def _verdict(eps_fy: list[dict], pt_series: list[dict], ratings: dict | None,
             counts: dict, momentum: dict) -> dict:
    score = 0
    reasons: list[dict] = []

    # 1. Nearest fiscal year with at least two observations — the strongest signal.
    primary = next((fy for fy in eps_fy if len(fy["points"]) >= 2), None)
    if primary and primary["change_pct"] is not None:
        chg = primary["change_pct"]
        score += 2 if chg > 3 else 1 if chg > 0.5 else -2 if chg < -3 else -1 if chg < -0.5 else 0
        reasons.append({
            "label": f"FY{primary['fiscal_year']} EPS 共識",
            "detail": f"{primary['first']} → {primary['latest']}（{primary['first_date'][:7]} 起）",
            "change_pct": chg,
            "direction": primary["direction"],
            "weight": "primary",
        })
    elif momentum.get("since_segment_start") is not None:
        chg = momentum["since_segment_start"]
        score += 1 if chg > 0.5 else -1 if chg < -0.5 else 0
        reasons.append({
            "label": "本年度 EPS 共識",
            "detail": f"自 {momentum['segment_start_date']} 起（同一會計年度）",
            "change_pct": chg,
            "direction": _direction(chg),
            "weight": "primary",
        })

    # 2. Average price target.
    if len(pt_series) >= 2:
        chg = _pct(pt_series[-1]["avg"], pt_series[0]["avg"])
        if chg is not None:
            score += 1 if chg > 2 else -1 if chg < -2 else 0
            reasons.append({
                "label": "平均目標價",
                "detail": f"${pt_series[0]['avg']} → ${pt_series[-1]['avg']}",
                "change_pct": chg,
                "direction": _direction(chg, threshold=2.0),
                "weight": "secondary",
            })

    # 3. Share of bullish ratings.
    if ratings and ratings["bull_pct_change"] is not None:
        chg = ratings["bull_pct_change"]
        score += 1 if chg > 2 else -1 if chg < -2 else 0
        reasons.append({
            "label": "看多分析師佔比",
            "detail": f"{ratings['bull_pct_first']}% → {ratings['bull_pct_latest']}%"
                      f"（{ratings['total_latest']} 位）",
            "change_pct": chg,
            "unit": "pt",
            "direction": ratings["direction"],
            "weight": "secondary",
        })

    # 4. Individual actions in the recent window.
    net = (counts["raised"] + counts["upgrades"]) - (counts["lowered"] + counts["downgrades"])
    if counts["total"]:
        score += 1 if net > 0 else -1 if net < 0 else 0
        reasons.append({
            "label": f"近 {counts['window_days']} 天分析師動作",
            "detail": f"調升目標價 {counts['raised']} · 調降 {counts['lowered']} · "
                      f"升評 {counts['upgrades']} · 降評 {counts['downgrades']}",
            "change_pct": None,
            "direction": "up" if net > 0 else "down" if net < 0 else "flat",
            "weight": "secondary",
        })

    if not reasons:
        return {"direction": "unknown", "score": 0, "headline": "資料不足",
                "summary": "尚未累積足夠的分析師快照，無法判斷修正方向。", "reasons": []}

    ups = sum(1 for r in reasons if r["direction"] == "up")
    downs = sum(1 for r in reasons if r["direction"] == "down")

    # "Flat" must mean everything is quiet, not that a big cut and a big raise
    # happened to cancel out to a score of zero.
    if score >= 2:
        direction = "up"
    elif score <= -2:
        direction = "down"
    elif ups and downs:
        direction = "mixed"
    else:
        direction = "flat"
    headline = {"up": "分析師上調預期", "down": "分析師下調預期",
                "mixed": "訊號分歧", "flat": "預期持平"}[direction]
    return {
        "direction": direction,
        "score": score,
        "headline": headline,
        "summary": f"{len(reasons)} 項指標中 {ups} 項偏多、{downs} 項偏空。",
        "reasons": reasons,
    }


# ── entry point ───────────────────────────────────────────────────────────────

def build_revisions(ticker: str, valuation_rows: list) -> dict:
    """Assemble the full analyst-revision picture for *ticker*."""
    snapshots = _load_snapshots(ticker)

    eps_fy = _eps_by_fiscal_year(snapshots)
    pt_series = _price_target_series(snapshots)
    ratings = _ratings_trend(snapshots)
    actions, counts = _analyst_actions(snapshots)
    consensus = _consensus_trend(valuation_rows)

    return {
        "ticker": ticker.upper(),
        "as_of": snapshots[-1][0].isoformat() if snapshots else (
            consensus["latest"]["date"] if consensus["latest"] else None
        ),
        "snapshot_count": len(snapshots),
        "verdict": _verdict(eps_fy, pt_series, ratings, counts, consensus["momentum"]),
        "eps_by_fiscal_year": eps_fy,
        "consensus_trend": consensus,
        "price_target": {
            "series": pt_series,
            "change_pct": _pct(pt_series[-1]["avg"], pt_series[0]["avg"]) if len(pt_series) >= 2 else None,
            "latest": pt_series[-1] if pt_series else None,
        },
        "ratings": ratings,
        "actions": actions[:20],
        "action_counts": counts,
    }
