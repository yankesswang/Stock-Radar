"""唯讀開啟 SEC 資料庫的共用入口。

為什麼需要這支：背景寫入的排程（fetch_segments、extract_nongaap、prune_history
的 VACUUM）會在 stock_valuation.db 上拿 EXCLUSIVE 鎖。這個庫是 rollback-journal
模式，鎖住的時候**連唯讀連線都會 SQLITE_BUSY**——不是慢，是直接報錯。

原本每個 router 各自寫 `except sqlite3.Error: return []`，於是「資料庫正在忙」
被翻譯成「這檔沒有資料」：/api/segments 回 axes=[]，前端 SecSegmentRevenue 的
`if (state === 'none') return null` 就把整個「分部門營收」區塊收掉。使用者看到的
是面板憑空消失，重整一次又回來，完全查不出所以然。

所以這裡只做兩件事，而且刻意分開：

  1. **等**——busy_timeout 讓連線在鎖上等，而不是立刻放棄。寫入端是逐檔 commit，
     多數情況等幾百毫秒就過去了，使用者根本不會察覺。
  2. **等不到就承認**——丟 503 往上，讓前端顯示「資料庫忙碌中，稍後再試」。
     絕不回空陣列假裝沒資料：那是把暫時性故障偽裝成永久事實，最難查。

真的沒有資料（DB 檔不存在、查詢結果為空）還是回空的，那才是誠實的空。
"""
import sqlite3
from pathlib import Path

from fastapi import HTTPException

# 等多久放棄。寫入端逐檔 commit，秒級的等待足以蓋過絕大多數的鎖；
# VACUUM 這種長交易等不完是應該的——那時回 503 才是實話。
BUSY_TIMEOUT_S = 5.0


def ro_connect(db: str | Path, timeout: float = BUSY_TIMEOUT_S) -> sqlite3.Connection:
    """唯讀連線，撞到鎖會先等 *timeout* 秒。

    connect() 的 timeout 參數就是 busy timeout，但只作用在它自己開的連線上，
    所以每個呼叫端都要帶——不能靠某處設一次就以為全域生效。
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=timeout)
    con.row_factory = sqlite3.Row
    return con


def ro_rows(db: str | Path, sql: str, args: tuple = ()) -> list[dict]:
    """跑一個唯讀查詢 → list[dict]。

    DB 檔不存在回空（還沒建庫是合理狀態）；等不到鎖丟 503（暫時性故障）；
    其他 sqlite 錯誤原樣往上（schema 打錯這種 bug 不該被靜靜吃掉）。
    """
    if not Path(db).exists():
        return []
    try:
        con = ro_connect(db)
    except sqlite3.OperationalError as e:
        raise HTTPException(503, f"資料庫忙碌中，稍後再試（{e}）") from e
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.OperationalError as e:
        # "database is locked" / "database table is locked"：等超過 busy_timeout 了
        if "locked" in str(e) or "busy" in str(e).lower():
            raise HTTPException(503, f"資料庫忙碌中，稍後再試（{e}）") from e
        raise
    finally:
        con.close()
