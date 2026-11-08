#!/bin/zsh
# Move headless-generated earnings notes from staging into the Obsidian vault.
# Run interactively (TCC blocks launchd from writing ~/Documents).
set -u
# ${0:A:h} = 這支腳本所在目錄的絕對路徑。用 $(dirname "$0") 的話，被相對路徑
# 呼叫時會解析成 "."，底下兩支同層腳本就找不到了。
HERE="${0:A:h}"
STAGE="$HOME/.arthur-digest/earnings_notes/vault-staging"
VAULT="$HOME/Documents/arthurwang_DB"

# Reconcile the reading list against the registry. Idempotent (a note already
# linked is never re-added), so it is safe to run on every cron tick.
#
# This used to live inside the rsync success branch, which meant it only ran on
# days that actually staged something. A note whose list entry failed that day
# (the list is often a dataless placeholder — see add_to_reading_list.py) was
# then never retried: CAMT/FSLY/UMAC 2026Q2 and ADI 2026Q3 all went missing that
# way. Earnings season has long gaps, so "runs only when something is staged" is
# not a schedule — it has to run daily.
sync_reading_list() {
  /usr/bin/env python3 "$HERE/add_to_reading_list.py" --sync-all
}

# 把每篇筆記的操作結論抽進 earnings_conclusions，餵 App 的「財報結論」總覽頁。
# 讀的是鏡像，所以一定要排在 mirror_notes.sh 之後。--llm-backfill 只對「抽不到
# 動作句」的筆記呼叫 claude，正常一天 0 到 1 篇；上一輪補過的會自動沿用不重打。
extract_conclusions() {
  /usr/bin/env python3 "$HERE/extract_conclusions.py" --llm-backfill
  # 論點追蹤表與成績單數據（純表格解析，不花額度）。深度分析要 LLM，另外掛
  # --llm-deep；已抽過且章節沒變的會跳過，所以放進每日流程也不會重複燒。
  /usr/bin/env python3 "$HERE/extract_insights.py" --llm-deep
}

# 各部門營收與利潤（SEC 申報的分部門報表 → backend/stock_valuation.db）。
# --only-new 先比對最新申報編號，沒新財報就整檔跳過：全部跳過時約 30 秒跑完，
# 不加這個旗標每天都會重抓上千個 SEC 請求。分部門一季才更新一次，不必天天全抓。
fetch_segments() {
  /usr/bin/env python3 "$HERE/fetch_segments.py" --only-new --filings 4
}

# 三大財報表（結構取自 SEC 申報的 R-file、數值取自 XBRL companyfacts），
# 再從三大表推算基本面指標。每檔約 5 個請求，全跑約 5 分鐘，一天一次不算重。
fetch_financials() {
  /usr/bin/env python3 "$HERE/fetch_financials.py" --sleep 0.25
  # 10-K 不單獨報第四季，季度序列每年會斷一次。補出來（標記為推算）之後
  # 再算指標，否則 TTM 與季度圖表每年都缺一格。
  /usr/bin/env python3 "$HERE/derive_q4.py"
  /usr/bin/env python3 "$HERE/derive_metrics.py"
}

# stockanalysis 的 TTM 快照：補 SEC 沒有的欄位，並拿來對帳自算的指標。
# 間隔 1 秒是禮貌，49 檔約一分鐘。這條路徑已經抓出兩個自己的錯（有息負債
# 重複加總、金融業營收只算到一小塊），值得每天跑。
fetch_supplement() {
  /usr/bin/env python3 "$HERE/fetch_stockanalysis.py" --sleep 1.0
  /usr/bin/env python3 "$HERE/reconcile_metrics.py"
}

if [[ ! -d "$STAGE" ]] || [[ "$(find "$STAGE" -type f | wc -l | tr -d ' ')" == "0" ]]; then
  echo "nothing staged"
  sync_reading_list
  extract_conclusions
  fetch_segments
  fetch_financials
  fetch_supplement
  exit 0
fi

COUNT=$(find "$STAGE" -type f | wc -l | tr -d ' ')
# 排版檢查（只報不擋）：headless 生成的筆記最常犯的是把 30 秒結論寫成一整段、
# 內文全是 200 字散文（FSLY／WDAY 2026Q2）。這裡把 FAIL 的印進 log，
# 互動 session 看到就回頭重排；擋下來反而會讓筆記卡在 staging 沒人知道。
find "$STAGE" -name '*財報筆記.md' -print0 | xargs -0 -r /usr/bin/env python3 "$HERE/lint_note_format.py" --quiet \
  || echo "排版檢查有 FAIL（見上表），同步後請重排：python3 $HERE/lint_note_format.py <筆記>"
if rsync -av "$STAGE/" "$VAULT/"; then
  find "$STAGE" -type f -delete
  find "$STAGE" -type d -empty -delete
  echo "synced $COUNT files into vault"
  # notes only exist in the vault now, so this is where the reading list gets updated
  sync_reading_list
  # refresh the TCC-safe mirror the stock-radar API serves the 財報筆記 tab from
  "$HERE/mirror_notes.sh"
  extract_conclusions
  fetch_segments
  fetch_financials
  fetch_supplement
else
  echo "sync failed (expected under launchd; run me from a terminal)"
  # the staged notes are still queued, but earlier notes already in the vault may
  # still be missing from the list — reconcile those before bailing out
  sync_reading_list
  exit 1
fi
