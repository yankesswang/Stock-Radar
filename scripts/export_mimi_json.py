#!/usr/bin/env python3
"""
Export mimi操作建議總表.md → mimi操作建議總表.json
One JSON object per #### stock block.
Original .md NOT modified.

Supports TWO block formats:
  • NEW (2026-07+):  "**🟢 當前動作（date，當時 $X → 現 $Y，±Z%）：佈局——…**"
                     + op table  | 進場 | 目標 | 停損 | 關鍵催化劑 |
                     + optional  觀點演變 table
                     + **邏輯**/**基本面**/**風險** label lines
  • OLD:             | 項目 | 數值 | key-value table + > [!tip]/[!warning] callouts

New format is detected by the "當前動作" line; otherwise falls back to the
old key-value parser. Emits backward-compatible fields (analysis / direction /
stop_loss / date_first / callouts) so downstream DB sync keeps working, PLUS
new fields: action_light, price_rec, price_now, return_pct, entry, target,
catalyst, logic, risk, history.
"""
import re, json
from pathlib import Path

SRC = Path('/Users/yankesswang/Documents/arthurwang_DB/投資/mimi操作建議總表.md')
DST = Path('/Users/yankesswang/Documents/arthurwang_DB/投資/mimi操作建議總表.json')

DATE_RE     = re.compile(r'\d{4}-\d{2}-\d{2}|\d{4}-\d{2}')
DATE_FULL   = re.compile(r'\d{4}-\d{2}-\d{2}')
UPDATE_RE   = re.compile(r'更新[：:]\s*(\d{4}-\d{2}-\d{2})')
WL_RE       = re.compile(r'\[\[.*?\]\]')
TSEP_RE     = re.compile(r'^\s*\|[-| :]+\|\s*$')
CHAPTER_RE  = re.compile(r'^## [一二三四五六七八九十]')
ORDINAL_RE  = re.compile(r'^[一二三四五六七八九十百]+[、]')

# NEW format ------------------------------------------------------------------
LIGHTS = '🟢🟡🟠🔴⚪'
ACTION_RE = re.compile(
    r'^\*\*\s*([' + LIGHTS + r'])?\s*當前動作（(?P<paren>.*?)）\s*[：:]\s*(?P<dir>.*?)\s*\*\*'
)
PRICE_RE = re.compile(
    r'當時\s*(?:NT\$|¥|\$)?\s*([\d,]+(?:\.\d+)?)\s*(?:→|->)\s*現\s*(?:NT\$|¥|\$)?\s*([\d,]+(?:\.\d+)?)'
)
PRICE1_RE = re.compile(r'當時\s*(?:NT\$|¥|\$)?\s*([\d,]+(?:\.\d+)?)')  # 當時 $X only (new listings)
RET_RE    = re.compile(r'([+-]\s*\d+(?:\.\d+)?\s*%)')
SHUTUI_RE = re.compile(r'首推\s*(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})')
LABEL_RE  = re.compile(r'^\*\*(邏輯|基本面\(參考\)|基本面（參考）|風險)\*\*[：:]\s*(.*)')

DIRECTION_KEYS = {'操作方向', '進場策略', '建議操作', '操作建議', '配置建議', '進場時機', '現況'}
STOP_KEYS      = {'停損', '停損參考', '停損條件', '停損 / 失效條件', '停損/失效條件'}


def strip_bold(v):
    return re.sub(r'\*+', '', v).strip()

def num(s):
    try:
        return float(s.replace(',', ''))
    except (ValueError, AttributeError):
        return None

def first_date(v):
    m = DATE_FULL.search(v)
    if m: return m.group(0)
    m = DATE_RE.search(v)
    return m.group(0) if m else ''

def update_date(v):
    m = UPDATE_RE.search(v)
    return m.group(1) if m else ''

def wikilinks(text):
    seen, out = set(), []
    for l in WL_RE.findall(text):
        if l not in seen: seen.add(l); out.append(l)
    return out

def split_row(line):
    return [p.strip() for p in line.strip().strip('|').split('|')]

def parse_kv_table(lines):
    """OLD format: | key | value | → list of (key, value)."""
    rows = []
    for line in lines:
        if TSEP_RE.match(line):
            if rows: rows.pop()  # drop header row preceding separator
            continue
        parts = split_row(line)
        if len(parts) >= 2:
            rows.append((strip_bold(parts[0]), parts[1].strip()))
    return rows

def find_tables(content):
    """Return list of (header_cells, [data_rows_cells]) for every md table."""
    tables, i = [], 0
    while i < len(content):
        if content[i].startswith('|') and i + 1 < len(content) and TSEP_RE.match(content[i + 1]):
            header = split_row(content[i])
            data = []
            j = i + 2
            while j < len(content) and content[j].startswith('|'):
                if not TSEP_RE.match(content[j]):
                    data.append(split_row(content[j]))
                j += 1
            tables.append((header, data))
            i = j
        else:
            i += 1
    return tables

def key_match(name, key_set):
    return any(name == k or name.startswith(k) for k in key_set)

def parse_heading(h4):
    line = h4[5:]
    # split ticker(s) from description on the first em/en/box dash (spaces optional).
    parts = re.split(r'\s*[—–―─]\s*', line, maxsplit=1)
    if len(parts) == 2:
        left, desc = parts
    else:
        left, desc = line, ''
    tickers, names = [], []
    for part in left.split('/'):
        part = part.strip()
        m = re.match(r'([^（\s]+)[（(](.+?)[）)]', part)
        if m:
            tickers.append(m.group(1).strip()); names.append(m.group(2).strip())
        elif part:
            tickers.append(part)
    return tickers, names, desc.strip()

def extract_callouts(content):
    result, cur_type, cur_lines = {}, None, []
    for ln in content:
        m = re.match(r'^> \[!(\w+)\]\s*(.*)', ln)
        if m:
            if cur_type: result[cur_type] = '\n'.join(cur_lines).strip()
            cur_type = m.group(1).lower()
            heading = m.group(2).strip()
            cur_lines = [heading] if heading else []
        elif cur_type and ln.startswith('>'):
            cur_lines.append(ln[1:].lstrip(' '))
        elif cur_type:
            result[cur_type] = '\n'.join(cur_lines).strip()
            cur_type, cur_lines = None, []
    if cur_type:
        result[cur_type] = '\n'.join(cur_lines).strip()
    return result


# ── NEW-format parsers ──────────────────────────────────────────────────────

def parse_action_line(ln):
    """Return dict from a '當前動作' line, or None."""
    m = ACTION_RE.search(ln)
    if not m:
        return None
    light, paren, direction = m.group(1) or '', m.group('paren'), m.group('dir')
    out = {'action_light': light, 'direction': strip_bold(direction).strip()}
    out['date_action'] = first_date(paren) or first_date(ln)
    sm = SHUTUI_RE.search(paren) or SHUTUI_RE.search(ln)
    out['date_shutui'] = sm.group(1) if sm else ''
    pm = PRICE_RE.search(ln)
    if pm:
        out['price_rec'] = num(pm.group(1))
        out['price_now'] = num(pm.group(2))
    else:
        p1 = PRICE1_RE.search(ln)  # single "當時 $X" (e.g. new listing, no 現)
        if p1:
            out['price_rec'] = num(p1.group(1))
    rm = RET_RE.search(ln)
    if rm:
        out['return_pct'] = rm.group(1).replace(' ', '')
    return out

def parse_op_table(tables):
    """Operation table (header has 進場 & 停損/催化劑) → column dict."""
    for header, data in tables:
        hset = set(header)
        if '進場' in hset and ('停損' in hset or '關鍵催化劑' in hset) and data:
            idx = {h: k for k, h in enumerate(header)}
            row = data[0]
            def cell(name):
                k = idx.get(name)
                return strip_bold(row[k]) if k is not None and k < len(row) else ''
            return {
                'entry':    cell('進場'),
                'target':   cell('目標'),
                'stop':     cell('停損'),
                'catalyst': cell('關鍵催化劑') or cell('催化劑'),
            }
    return {}

def parse_history(tables):
    """觀點演變 table → list of {date, price, action, thesis, change}."""
    for header, data in tables:
        hjoin = ' '.join(header)
        if '日期' in header and ('核心論點' in hjoin or '比前次' in hjoin or '相對前次' in hjoin):
            idx = {h: k for k, h in enumerate(header)}
            out = []
            for row in data:
                def cell(name):
                    k = idx.get(name)
                    return strip_bold(row[k]) if k is not None and k < len(row) else ''
                out.append({
                    'date':   cell('日期'),
                    'price':  cell('當時價'),
                    'action': cell('動作'),
                    'thesis': cell('該篇核心論點') or cell('核心論點'),
                    'change': cell('比前次變了什麼') or cell('相對前次'),
                })
            return out
    return []

def parse_labels(content):
    """**邏輯**/**基本面**/**風險** label lines → dict."""
    out = {}
    for ln in content:
        m = LABEL_RE.match(ln)
        if m:
            key = '基本面' if '基本面' in m.group(1) else m.group(1)
            out[key] = m.group(2).strip()
    return out

def _first(d, *keys):
    for k in keys:
        if d.get(k):
            return d[k]
    return ''


def block_to_obj(h4, content, sector):
    tickers, names, heading_desc = parse_heading(h4)
    raw_md = '\n'.join([h4] + content)
    tables = find_tables(content)

    action = None
    for ln in content:
        action = parse_action_line(ln)
        if action:
            break

    date_first = date_upd = direction = stop_loss = ''
    entry = target = catalyst = ''
    action_light = ''
    price_rec = price_now = None
    return_pct = ''
    history, analysis, callouts = [], {}, {}
    logic = risk = ''
    raw_links = []

    if action:
        # ---- NEW format ----
        action_light = action.get('action_light', '')
        direction    = action.get('direction', '')
        price_rec    = action.get('price_rec')
        price_now    = action.get('price_now')
        return_pct   = action.get('return_pct', '')
        history      = parse_history(tables)
        op           = parse_op_table(tables)
        entry, target, stop_loss, catalyst = (
            op.get('entry', ''), op.get('target', ''),
            op.get('stop', ''), op.get('catalyst', ''),
        )
        labels = parse_labels(content)
        logic  = labels.get('邏輯', '')
        risk   = labels.get('風險', '')

        date_upd = action.get('date_action', '')
        if history and history[0].get('date') and DATE_FULL.match(history[0]['date']):
            date_first = history[0]['date']
        else:
            date_first = action.get('date_shutui') or date_upd

        # price_rec fallback: first 觀點演變 row's price (AVGO-style, price only in history)
        if price_rec is None and history and history[0].get('price'):
            price_rec = num(re.sub(r'[^\d.]', '', history[0]['price']))

        if entry:    analysis['進場區間'] = entry
        if target:   analysis['第一目標'] = target
        if catalyst: analysis['關鍵催化劑時間點'] = catalyst
        if logic:    analysis['邏輯'] = logic
        if risk:     analysis['風險'] = risk
        if logic: callouts['tip'] = logic
        if risk:  callouts['warning'] = risk
        callouts.update(extract_callouts(content))

    else:
        # ---- OLD format (key-value table) ----
        t0 = t1 = None
        for i, ln in enumerate(content):
            if ln.startswith('|') and t0 is None: t0 = i
            if t0 is not None and not ln.startswith('|') and ln.strip(): t1 = i; break
        if t0 is not None and t1 is None: t1 = len(content)
        before      = content[:t0] if t0 is not None else []
        table_lines = content[t0:t1] if t0 is not None else []
        after       = content[t1:]  if t1 is not None else content
        rows = parse_kv_table(table_lines)

        for ln in before:
            if '首推日期' in ln and not date_first:
                date_first = first_date(ln); raw_links.extend(wikilinks(ln))
        for name, val in rows:
            if name == '首推日期':
                if not date_first: date_first = first_date(val)
                if not date_upd:   date_upd = update_date(val)
                raw_links.extend(wikilinks(val))
            elif key_match(name, DIRECTION_KEYS) and not direction:
                direction = strip_bold(val)
            elif key_match(name, STOP_KEYS) and not stop_loss:
                stop_loss = strip_bold(val)
            else:
                analysis[name] = strip_bold(val)
        entry    = _first(analysis, '進場區間', '進場策略', '進場區間或觀察點')
        target   = _first(analysis, '第一目標', '目標價', '目標價（第一目標）')
        catalyst = _first(analysis, '關鍵催化劑時間點', '關鍵催化劑', '催化劑')
        callouts = extract_callouts(content)
        logic = callouts.get('tip', ''); risk = callouts.get('warning', '')
        for ln in after:
            if '📖' in ln:
                raw_links.extend(wikilinks(re.sub(r'^>\s*', '', ln)))

    for ln in content:
        if '📖' in ln:
            raw_links.extend(wikilinks(re.sub(r'^>\s*', '', ln)))
    seen, sources = set(), []
    for l in raw_links:
        if l not in seen: seen.add(l); sources.append(l)

    return {
        'id':           f"{'+'.join(tickers)}_{date_first}" if date_first else '+'.join(tickers),
        'tickers':      tickers,
        'names':        names,
        'heading':      heading_desc,
        'sector':       sector,
        'format':       'new' if action else 'old',
        'date_first':   date_first,
        'date_updated': date_upd,
        'action_light': action_light,
        'direction':    direction,
        'entry':        entry,
        'target':       target,
        'stop_loss':    stop_loss,
        'catalyst':     catalyst,
        'price_rec':    price_rec,
        'price_now':    price_now,
        'return_pct':   return_pct,
        'logic':        logic,
        'risk':         risk,
        'history':      history,
        'sources':      sources,
        'analysis':     analysis,
        'callouts':     callouts,
        'raw_md':       raw_md,
    }


# ── read & split ────────────────────────────────────────────────────────────
with SRC.open('r', encoding='utf-8') as f:
    raw = [l.rstrip('\n') for l in f]

hdr_end = 0
for i, ln in enumerate(raw):
    if CHAPTER_RE.match(ln): hdr_end = i; break
body = raw[hdr_end:]

sector, records, i = '', [], 0
while i < len(body):
    ln = body[i]
    if CHAPTER_RE.match(ln):
        title = ln[3:]
        m = ORDINAL_RE.match(title)
        sector = title[m.end():] if m else title
        i += 1
    elif ln.startswith('#### '):
        h4 = ln; content = []; i += 1
        while i < len(body):
            nxt = body[i]
            if nxt.startswith('#### ') or CHAPTER_RE.match(nxt): break
            content.append(nxt); i += 1
        while content and content[-1].strip() in ('', '---'): content.pop()
        records.append(block_to_obj(h4, content, sector))
    else:
        i += 1

out = json.dumps(records, ensure_ascii=False, indent=2)
DST.write_text(out, encoding='utf-8')

print(f'Written  → {DST.name}')
print(f'Records  : {len(records)}  ('
      f'{sum(1 for r in records if r["format"]=="new")} new / '
      f'{sum(1 for r in records if r["format"]=="old")} old format)')
print(f'Size     : {len(out):,} chars\n')

empty = {f: 0 for f in ('date_first', 'direction', 'entry', 'target', 'stop_loss',
                        'catalyst', 'return_pct', 'sources')}
for r in records:
    for f in empty:
        if not r[f]: empty[f] += 1
print('Empty field counts (lower is better):')
for f, n in empty.items():
    print(f'  {f:<14} {n} empty / {len(records)} total')
