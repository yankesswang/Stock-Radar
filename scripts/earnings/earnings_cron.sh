#!/bin/zsh
# Daily earnings-note automation, fired by launchd (com.arthur.earnings-notes).
#
# Flow: refresh calendar -> EDGAR-confirm which watched tickers reported
# recently without a note -> chunk them into batches of $BATCH -> one headless
# claude -p session per chunk (the 2nd+ note in a session rides on the cached
# prefix: system prompt, SKILL rules, template understanding all warm).
# launchd cannot touch ~/Documents (TCC), so headless notes go to staging;
# sync_staged.sh (run interactively) moves them into the Obsidian vault.
#
# Env knobs:
#   DRY_RUN=1   print what would run, no claude calls
#   MODEL=opus  model override for claude -p
#   WAIT=2      days to wait after the 8-K before generating (transcripts out)
#   WINDOW=6    how many days back to look (backfill: WINDOW=14)
#   BATCH=2     notes per claude session (default 2 for cache reuse)

set -u
# launchd starts us with a bare PATH, so `claude` is not on it (2026-08-03: three
# chunks failed with "command not found: claude" and their markers blocked retries)
CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
[[ -x "$CLAUDE" ]] || CLAUDE="$(command -v claude 2>/dev/null || echo claude)"
ER="$HOME/Projects/Arthur_App/stock-radar/scripts/earnings"
LOGDIR="$HOME/Projects/Arthur_App/logs"
STAGE="$HOME/.arthur-digest/earnings_notes"
LOCKDIR="$STAGE/state"
mkdir -p "$LOGDIR" "$LOCKDIR"
LOG="$LOGDIR/earnings_cron.log"
BATCH=${BATCH:-2}

echo "[$(date '+%F %T')] earnings_cron start (batch=$BATCH)" >> "$LOG"

# holdings first: a company missing from the watchlist is invisible to pending(),
# so a newly bought name would silently never get a note
/usr/bin/env python3 "$ER/earnings_calendar.py" sync-portfolio >> "$LOG" 2>&1
/usr/bin/env python3 "$ER/earnings_calendar.py" update >> "$LOG" 2>&1

# collect runnable tickers (marker = already attempted this filing, skip)
typeset -a QUEUE
while read -r TICKER FILED; do
  [[ -z "$TICKER" ]] && continue
  if [[ -e "$LOCKDIR/${TICKER}_${FILED}" ]]; then
    echo "[$(date '+%F %T')] $TICKER $FILED already attempted, skip" >> "$LOG"
    continue
  fi
  QUEUE+=("$TICKER $FILED")
done < <(/usr/bin/env python3 "$ER/earnings_calendar.py" pending \
           --wait-days "${WAIT:-2}" --window "${WINDOW:-6}")

HEADLESS_NOTE="headless 模式注意：本程序由 launchd 執行，寫不進 ~/Documents（TCC）。
筆記與附件請寫到 $STAGE/vault-staging/投資/財報筆記/{產業}/{TICKER}/{年度}_{Q}/
（層狀結構同 vault，見 SKILL Step 4）。每份筆記完成後照 SKILL Step 6 回寫 registry
（note_path 填 vault 相對路徑）。全部完成後執行 $ER/sync_staged.sh，
失敗（EPERM）屬預期，留在 staging 即可。"

i=1
while (( i <= ${#QUEUE[@]} )); do
  typeset -a CHUNK
  CHUNK=("${(@)QUEUE[$i,$((i + BATCH - 1))]}")
  (( i += BATCH ))

  TICKERS=(); NAMES=""
  for entry in "${CHUNK[@]}"; do
    T="${entry%% *}"
    TICKERS+=("$T")
    NAMES+="${NAMES:+-}$T"
  done

  PROMPT="/earnings-note ${TICKERS[1]}"
  if (( ${#TICKERS[@]} > 1 )); then
    PROMPT+="
完成 ${TICKERS[1]} 的筆記與 registry 回寫後，「在同一個 session 內」緊接著處理："
    for T in "${TICKERS[@]:1}"; do
      PROMPT+="
/earnings-note $T"
    done
    PROMPT+="
接續處理時：SKILL 流程與格式鐵律已在 context，不必重讀；材料（新聞稿/逐字稿/簡報/
stock_context）各自重新抓；動筆前把格式鐵律八條快速自檢一遍再寫。"
  fi
  PROMPT+="
$HEADLESS_NOTE"

  echo "[$(date '+%F %T')] generating notes for ${NAMES} " >> "$LOG"
  if [[ "${DRY_RUN:-0}" = "1" ]]; then
    echo "DRY_RUN: would run claude -p for ${NAMES}" | tee -a "$LOG"
    continue
  fi
  for entry in "${CHUNK[@]}"; do
    touch "$LOCKDIR/${entry%% *}_${entry##* }"
  done
  # timestamped per run: the limit check greps this file, a shared daily file
  # would false-positive on an earlier attempt's limit message
  CHUNK_LOG="$LOGDIR/earnings_note_${NAMES}_$(date +%F_%H%M%S).log"
  (
    cd "$HOME/Projects/Arthur_App" || exit 1
    MODEL_ARGS=()
    [[ -n "${MODEL:-}" ]] && MODEL_ARGS=(--model "$MODEL")
    "$CLAUDE" -p "$PROMPT" \
      "${MODEL_ARGS[@]}" \
      --dangerously-skip-permissions \
      --output-format text \
      >> "$CHUNK_LOG" 2>&1
  )
  RC=$?
  echo "[$(date '+%F %T')] ${NAMES} done, exit=$RC" >> "$LOG"
  # Any failure (usage limit, missing binary, crash) must release this chunk's
  # markers, otherwise a stuck attempt permanently blocks the retry. A real run
  # writes a substantial summary; a failure writes a one-line error.
  if (( RC != 0 )) || [[ $(wc -c < "$CHUNK_LOG") -lt 300 ]]; then
    for entry in "${CHUNK[@]}"; do
      rm -f "$LOCKDIR/${entry%% *}_${entry##* }"
    done
    echo "[$(date '+%F %T')] ${NAMES} failed; markers cleared" >> "$LOG"
    grep -q "hit your limit" "$CHUNK_LOG" 2>/dev/null && {
      echo "[$(date '+%F %T')] usage limit, aborting batch" >> "$LOG"; break; }
  fi
done

# harmless under launchd (EPERM), works when run interactively.
# sync_staged.sh also updates 待閱讀清單.md once the notes are in the vault.
"$ER/sync_staged.sh" >> "$LOG" 2>&1 || true
echo "[$(date '+%F %T')] earnings_cron end" >> "$LOG"
