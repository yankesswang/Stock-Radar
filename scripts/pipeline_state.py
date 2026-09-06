#!/usr/bin/env python3
"""
每日管線狀態機（依賴式工作流）—— 只用標準函式庫，backend 與 scripts 兩邊都能 import。

問題來源（2026-08-23 檢討）：
  A 最長可跑到 06:10，B 06:00 就到點；B 最長又可能壓到 C。固定時間排程等於讓三條
  管線同時搶 claude、網路與 backend。而且任何一條失敗就「當天永久放棄」，不重試、
  也沒有 checkpoint —— 於是主報告連續缺檔、Market Scan 從未成功落地都沒人發現。

改成：**完成事件串接，固定時間只是「最晚啟動時間」**。

    快照(ai_cards) → 復盤(war_room) → 掃描(market_scan) → 新聞(market_news)
                                                            → 完整性檢查(completeness)

    某一階段可以跑的條件 =
        今天是該跑的星期
        AND（前置階段全部完成 OR 現在已超過 latest_start）
        AND 這階段今天還沒成功
        AND 若曾失敗，重試次數未用完且已過冷卻時間

狀態檔：data/agent/pipeline/{date}.json（date = 台北日期，與報告資料夾一致）
"""
from __future__ import annotations

import json
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / "agent" / "pipeline"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

DONE_STATUSES = {STATUS_SUCCESS, STATUS_PARTIAL, STATUS_SKIPPED}

# 台北週二～週六 = 美股週一～週五收盤
TRADING_WEEKDAYS = {1, 2, 3, 4, 5}


class Stage:
    def __init__(self, key: str, label: str, latest_start: dt_time,
                 depends_on: tuple[str, ...] = (), max_attempts: int = 3,
                 retry_cooldown_min: int = 15, weekdays: set[int] | None = None,
                 owner: str = "backend-loop"):
        self.key = key
        self.label = label
        self.latest_start = latest_start
        self.depends_on = depends_on
        self.max_attempts = max_attempts
        self.retry_cooldown_min = retry_cooldown_min
        self.weekdays = weekdays if weekdays is not None else TRADING_WEEKDAYS
        self.owner = owner


STAGES: list[Stage] = [
    Stage("ai_cards", "🧑‍🍳 AI 持倉卡預產", dt_time(5, 0), (), max_attempts=1,
          weekdays={0, 1, 2, 3, 4, 5, 6}, owner="launchd"),
    Stage("war_room", "🎖️ 盤後復盤", dt_time(5, 30), ("ai_cards",), max_attempts=2,
          retry_cooldown_min=20),
    Stage("market_scan", "🔭 機會掃描", dt_time(6, 0), ("war_room",), max_attempts=2,
          retry_cooldown_min=20),
    Stage("market_news", "📰 市場新聞", dt_time(6, 40), ("market_scan",), max_attempts=3,
          retry_cooldown_min=10),
    Stage("completeness", "📋 完整性檢查", dt_time(7, 20), ("market_news",), max_attempts=2,
          retry_cooldown_min=15),
]
STAGE_BY_KEY = {s.key: s for s in STAGES}


def _now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def _path(d: date) -> Path:
    return STATE_DIR / f"{d.isoformat()}.json"


def load(d: date) -> dict:
    try:
        p = _path(d)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("stages", {})
                return data
    except Exception as e:
        print(f"[pipeline] 狀態讀取失敗（視為空）：{e}")
    return {"date": d.isoformat(), "stages": {}}


def save(d: date, state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state["date"] = d.isoformat()
        state["updated_at"] = _now().isoformat()
        _path(d).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[pipeline] 狀態寫入失敗（略過）：{e}")


def _stage_entry(state: dict, key: str) -> dict:
    return state["stages"].setdefault(key, {
        "key": key, "status": STATUS_PENDING, "attempts": 0,
        "started_at": None, "finished_at": None, "detail": None, "history": [],
    })


def mark_start(d: date, key: str) -> None:
    state = load(d)
    entry = _stage_entry(state, key)
    entry["status"] = STATUS_RUNNING
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["started_at"] = _now().isoformat()
    entry["finished_at"] = None
    save(d, state)


def mark_result(d: date, key: str, status: str, detail: str | None = None) -> None:
    state = load(d)
    entry = _stage_entry(state, key)
    entry["status"] = status
    entry["finished_at"] = _now().isoformat()
    entry["detail"] = (detail or "")[:500] or None
    entry.setdefault("history", []).append({
        "status": status, "at": entry["finished_at"], "detail": (detail or "")[:200] or None,
    })
    save(d, state)


def status_of(d: date, key: str) -> str:
    return (load(d)["stages"].get(key) or {}).get("status", STATUS_PENDING)


def is_done(d: date, key: str) -> bool:
    return status_of(d, key) in DONE_STATUSES


def should_run(d: date, key: str, now: datetime | None = None,
               state: dict | None = None) -> tuple[bool, str]:
    """回傳 (可不可以跑, 原因)。原因是給 log 用的，方便事後追為什麼沒跑。"""
    stage = STAGE_BY_KEY.get(key)
    if not stage:
        return False, f"未知階段 {key}"
    now = now or _now()
    if now.weekday() not in stage.weekdays:
        return False, "今天不是這條管線的排程日"

    state = state or load(d)
    entry = state["stages"].get(key) or {}
    status = entry.get("status", STATUS_PENDING)

    if status in DONE_STATUSES:
        return False, f"今天已完成（{status}）"
    if status == STATUS_RUNNING:
        started = entry.get("started_at")
        # 卡住超過 90 分鐘視為死掉，允許重跑（避免 backend 重啟後永遠停在 running）
        if started:
            try:
                age = (now - datetime.fromisoformat(started)).total_seconds() / 60
                if age < 90:
                    return False, f"執行中（已 {age:.0f} 分鐘）"
            except Exception:
                pass
        else:
            return False, "執行中"

    attempts = int(entry.get("attempts") or 0)
    if attempts >= stage.max_attempts:
        return False, f"已用完 {stage.max_attempts} 次嘗試"
    # 失敗過就要等冷卻（attempts 由 mark_start 累加；若只有 mark_result 也要擋，
    # 否則每 5 分鐘的迴圈會對同一個壞掉的階段連環重試）
    if attempts > 0 or status == STATUS_FAILED:
        last = entry.get("finished_at") or entry.get("started_at")
        if last:
            try:
                gap = (now - datetime.fromisoformat(last)).total_seconds() / 60
                if gap < stage.retry_cooldown_min:
                    return False, f"重試冷卻中（還要 {stage.retry_cooldown_min - gap:.0f} 分鐘）"
            except Exception:
                pass

    deps_done = all(
        (state["stages"].get(dep) or {}).get("status") in DONE_STATUSES
        for dep in stage.depends_on
    )
    if deps_done:
        return True, "前置階段已完成（事件觸發）"
    if now.time() >= stage.latest_start:
        pending = [dep for dep in stage.depends_on
                   if (state["stages"].get(dep) or {}).get("status") not in DONE_STATUSES]
        return True, f"已到最晚啟動時間 {stage.latest_start.strftime('%H:%M')}（未完成前置：{', '.join(pending) or '無'}）"
    return False, f"等前置 {', '.join(stage.depends_on)} 完成，或等到 {stage.latest_start.strftime('%H:%M')}"


def summary(d: date) -> dict:
    state = load(d)
    return {
        "date": d.isoformat(),
        "stages": [
            {
                "key": s.key, "label": s.label,
                "status": (state["stages"].get(s.key) or {}).get("status", STATUS_PENDING),
                "attempts": (state["stages"].get(s.key) or {}).get("attempts", 0),
                "detail": (state["stages"].get(s.key) or {}).get("detail"),
                "finished_at": (state["stages"].get(s.key) or {}).get("finished_at"),
            }
            for s in STAGES
        ],
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="每日管線狀態")
    ap.add_argument("--date")
    args = ap.parse_args()
    d = date.fromisoformat(args.date) if args.date else date.today()
    s = summary(d)
    print(f"管線狀態 {s['date']}")
    for row in s["stages"]:
        ok, why = should_run(d, row["key"])
        print(f"  {row['label']:<18} {row['status']:<9} 嘗試 {row['attempts']}  → 可跑={ok}（{why}）")
