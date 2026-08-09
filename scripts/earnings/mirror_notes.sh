#!/bin/zsh
# Mirror earnings notes out of ~/Documents so the backend can read them.
# macOS TCC blocks launchd-started services from reading ~/Documents, so the
# stock-radar API serves notes from this mirror (same trick as stock-digest).
# Run after notes change; sync_staged.sh calls it automatically.
set -u
VAULT="$HOME/Documents/arthurwang_DB/投資/財報筆記"
MIRROR="$HOME/.arthur-digest/earnings_notes/vault-mirror"
[[ -d "$VAULT" ]] || { echo "vault notes dir not found: $VAULT"; exit 0; }
mkdir -p "$MIRROR"
rsync -a --delete "$VAULT/" "$MIRROR/"
echo "mirrored $(find "$MIRROR" -name '*財報筆記.md' | wc -l | tr -d ' ') notes -> $MIRROR"
