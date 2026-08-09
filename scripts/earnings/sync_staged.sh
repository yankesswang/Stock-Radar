#!/bin/zsh
# Move headless-generated earnings notes from staging into the Obsidian vault.
# Run interactively (TCC blocks launchd from writing ~/Documents).
set -u
STAGE="$HOME/.arthur-digest/earnings_notes/vault-staging"
VAULT="$HOME/Documents/arthurwang_DB"
[[ -d "$STAGE" ]] || { echo "nothing staged"; exit 0; }
COUNT=$(find "$STAGE" -type f | wc -l | tr -d ' ')
[[ "$COUNT" = "0" ]] && { echo "nothing staged"; exit 0; }
if rsync -av "$STAGE/" "$VAULT/"; then
  find "$STAGE" -type f -delete
  find "$STAGE" -type d -empty -delete
  echo "synced $COUNT files into vault"
  # notes only exist in the vault now, so this is where the reading list gets updated
  /usr/bin/env python3 "$(dirname "$0")/add_to_reading_list.py" --sync-all
  # refresh the TCC-safe mirror the stock-radar API serves the 財報筆記 tab from
  "$(dirname "$0")/mirror_notes.sh"
else
  echo "sync failed (expected under launchd; run me from a terminal)"
  exit 1
fi
