#!/usr/bin/env python3
"""Add earnings notes to the Obsidian reading list (待閱讀清單.md).

Entries land in the newest `## 🆕 本週新增（...）` block under `### 投資` →
`#### 財報筆記`, as `- [ ] [[note stem]] 隔日 +x.x%`. Week rotation, the
per-author grouping and dedup all come from scripts/lib/article_list.py.

Idempotent: a note already linked anywhere in the file is never added twice,
so --sync-all can be run as often as you like (it is the self-healing path
for headless runs, which cannot write ~/Documents under launchd/TCC).

Usage:
  add_to_reading_list.py TICKER QUARTER   add one recorded note
  add_to_reading_list.py --sync-all       add every registry note that is missing
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import registry

# 清單常被系統清成 dataless 佔位檔，背景行程讀它會回 EDEADLK。裸 read_text()
# 會整支 traceback，害 sync_staged.sh 後面的 mirror 也跟著不跑。改走共用模組的
# 重試讀寫，讀不到就安靜退場——本支每天跑，明天會再補。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import article_list as al  # noqa: E402
from article_list import read_text, write_text  # noqa: E402

LIST = pathlib.Path.home() / "Documents/arthurwang_DB/待閱讀清單.md"


def add_entry(text, stem, suffix=""):
    return al.add_entry(text, stem, suffix=suffix, group=al.EARNINGS_GROUP)


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
