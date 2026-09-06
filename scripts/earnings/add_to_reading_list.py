#!/usr/bin/env python3
"""Add earnings notes to the Obsidian reading list (待閱讀清單.md).

Entries land in the newest `## 🆕 本週新增（...）` block under its `### 投資`
subsection, as `- [ ] [[note stem]] 隔日 +x.x%`. If the newest block's date
range does not cover today, a fresh week block is opened and the previous 🆕
is demoted to 📅.

Idempotent: a note already linked anywhere in the file is never added twice,
so --sync-all can be run as often as you like (it is the self-healing path
for headless runs, which cannot write ~/Documents under launchd/TCC).

Usage:
  add_to_reading_list.py TICKER QUARTER   add one recorded note
  add_to_reading_list.py --sync-all       add every registry note that is missing
"""
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import registry

# 清單常被系統清成 dataless 佔位檔，背景行程讀它會回 EDEADLK。裸 read_text()
# 會整支 traceback，害 sync_staged.sh 後面的 mirror 也跟著不跑。改走共用模組的
# 重試讀寫，讀不到就安靜退場——本支每天跑，明天會再補。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"))
from article_list import read_text, write_text  # noqa: E402

LIST = pathlib.Path.home() / "Documents/arthurwang_DB/待閱讀清單.md"
SECTION = "### 投資"


def week_range(today=None):
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    return monday, monday + dt.timedelta(days=6)


def ensure_current_week(text):
    """Return text with a 🆕 block covering today (demoting a stale one)."""
    mon, sun = week_range()
    label = f"## 🆕 本週新增（{mon:%Y/%m/%d} – {sun:%m/%d}）"
    m = re.search(r"^## 🆕 本週新增（(\d{4})/(\d{2})/(\d{2})\s*–", text, re.M)
    if m:
        start = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if start == mon:
            return text  # current block is this week's
        text = text.replace(m.group(0), m.group(0).replace("🆕 本週新增", "📅 上週"), 1)
    anchor = "# 待閱讀清單\n\n---\n\n"
    if anchor not in text:  # fall back: insert after the first H1
        anchor = re.search(r"^# .*\n", text, re.M).group(0)
    return text.replace(anchor, f"{anchor}{label}\n\n{SECTION}\n\n", 1)


def add_entry(text, stem, suffix=""):
    if f"[[{stem}]]" in text:
        return text, False
    text = ensure_current_week(text)
    head = re.search(r"^## (?:🆕 本週新增|📅).*$", text, re.M)
    block_start = head.end()
    nxt = re.search(r"^## ", text[block_start:], re.M)
    block_end = block_start + (nxt.start() if nxt else len(text) - block_start)
    block = text[block_start:block_end]

    line = f"- [ ] [[{stem}]]{suffix}\n"
    sec = re.search(rf"^{SECTION}\s*$", block, re.M)
    if sec:  # append to the last bullet of 投資
        after = block[sec.end():]
        bullets = list(re.finditer(r"^- \[.\].*$", after, re.M))
        nxt_sec = re.search(r"^### ", after, re.M)
        limit = nxt_sec.start() if nxt_sec else len(after)
        bullets = [b for b in bullets if b.end() <= limit]
        pos = sec.end() + (bullets[-1].end() + 1 if bullets else 1)
        block = block[:pos] + line + block[pos:]
        # keep one blank line before whatever follows the block
        block = re.sub(r"\n(?=## )", "\n\n", block)
    else:  # no 投資 subsection in this block yet
        block = block.rstrip("\n") + f"\n\n{SECTION}\n{line}"
    return text[:block_start] + block + text[block_end:], True


def note_stem(note_path):
    return pathlib.Path(note_path).stem


def collect(ticker=None, quarter=None):
    """[(stem, suffix)] for registry events that have a note recorded."""
    out = []
    with registry.connect() as con:
        q = "SELECT ticker, quarter, note_path, reaction_pct FROM earnings_events " \
            "WHERE note_path IS NOT NULL AND note_path != ''"
        args = []
        if ticker:
            q += " AND ticker=?"
            args.append(ticker.upper())
        if quarter:
            q += " AND quarter=?"
            args.append(quarter)
        for row in con.execute(q + " ORDER BY report_date", args):
            r = row["reaction_pct"]
            out.append((note_stem(row["note_path"]),
                        f"  隔日 {r:+.1f}%" if r is not None else ""))
    return out


def main():
    if not LIST.exists():
        sys.exit(f"reading list not found: {LIST}")
    args = sys.argv[1:]
    if args[:1] == ["--sync-all"]:
        items = collect()
    elif len(args) == 2:
        items = collect(args[0], args[1])
        if not items:
            sys.exit(f"no note recorded for {args[0]} {args[1]}; run registry.py record first")
    else:
        sys.exit(__doc__)

    try:
        text = read_text(LIST)
    except OSError as e:
        print(f"reading list unavailable, skipping this run: {e}")
        return
    added = []
    for stem, suffix in items:
        text, ok = add_entry(text, stem, suffix)
        if ok:
            added.append(stem)
    if added:
        try:
            write_text(LIST, text)
        except OSError as e:
            print(f"reading list not writable, skipping this run: {e}")
            return
    print(f"added {len(added)}: {added}" if added else "nothing new to add")


if __name__ == "__main__":
    main()
