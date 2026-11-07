// 盤後復盤「第一層：90 秒晨報」的解析器 —— 純函式、無 React 依賴，可直接用 node 測。
//
// 報告（scripts/war_room_report.py）的晨報段是 markdown，格式自 2026-08-28 起穩定：
//
//   **市場狀態**：多空分歧
//   **資料品質**：22/22 檔有最新報價｜LIVE…
//   ### 今天需要處理
//   1. **LLY**：建議**減碼**；目標倉位 5.0%；觸發：跌破1230已觸發；停損 $1230.0 （前次減碼：已回應，待對帳）
//      - 🔴 現價 $1149.36 **已在停損 $1230.0 之下**，最大風險 US$242
//   12. **TXN**：現價 $258.44，距停損 $250.0 僅 3.3%，最大風險 US$110
//   ### 等待條件
//   - ⚠️ **IBM**：停損 條件已於 近期 觸發，**已回應，待對帳** —— 需人工確認是否成交   ← 同一檔會重複出現很多次
//   - SANM（距 0.1%）：漲破198.74確認加碼
//   - （另有 15 條距觸發價較遠的條件單，見附錄決策帳本）
//   ### 最大風險
//   - 🟡 AI／半導體鏈 曝險 39.7%（13 檔）已過 35% 警戒線：ADI, ARM, …
//   - 全部停損同時觸發：約 **US$-384,997（組合 -506.95%）**
//   - 🔴 WDAY（風險：高）：內部人賣股…
//   ### 其餘 17 檔：維持，無新事件
//   ADI ARM …
//   > **戰情官一句話**：…
//
// 這裡把它切成物件讓前端排卡片；認不出來的段落留在 `unparsed`，前端退回 markdown。

import { matchActionToken } from './warRoomAttention.js';

const TICKER = '[A-Z0-9][A-Z0-9.-]{0,9}';
const strip = (s) => (s || '').replace(/\*\*/g, '').trim();
const num = (s) => {
  if (s == null) return null;
  const v = Number(String(s).replace(/[,$]/g, ''));
  return Number.isFinite(v) ? v : null;
};

function parseMeta(md) {
  const meta = {};
  const block = md.match(/```text\n([\s\S]*?)```/);
  if (block) {
    for (const line of block[1].split('\n')) {
      const m = line.match(/^(\S[^：:]*?)\s*[：:]\s*(.+)$/);
      if (!m) continue;
      const k = m[1].replace(/[\s\u3000]/g, '');
      meta[k] = m[2].trim();
    }
  }
  const verify = md.match(/^>\s*(✅|⚠️|⚠|🔴)?\s*產出驗證\s*[：:]\s*(.+)$/m);
  if (verify) meta.verify = { ok: !verify[1] || verify[1] === '✅', text: verify[2].trim() };
  const title = md.match(/^#\s+.*?—\s*(\d{4}-\d{2}-\d{2})/m);
  if (title) meta.date = title[1];
  return meta;
}

function parseTodoItem(head, sub) {
  const m = head.match(new RegExp(`^\\*\\*(${TICKER})\\*\\*\\s*[：:]\\s*(.*)$`));
  if (!m) return null;
  const ticker = m[1];
  const body = m[2];
  const item = { ticker, action: null, actionLabel: null, target: null, trigger: null, stop: null,
                 status: null, statusKind: null, price: null, belowStop: false, distPct: null, maxRisk: null, raw: strip(head) };
  const act = body.match(/建議\s*\*\*([^*]+)\*\*/);
  if (act) {
    item.actionLabel = act[1].trim();
    item.action = matchActionToken(act[1])?.action || null;
  }
  const tgt = body.match(/目標倉位\s*([\d.]+)%/);
  if (tgt) item.target = num(tgt[1]);
  const trg = body.match(/觸發\s*[：:]\s*([^；;（]+)/);
  if (trg) item.trigger = strip(trg[1]);
  const stp = body.match(/停損\s*\$?\s*([\d,.]+)/);
  if (stp) item.stop = num(stp[1]);
  if (/尚未確認執行/.test(body)) { item.status = '尚未確認執行'; item.statusKind = 'unconfirmed'; }
  else {
    const st = body.match(/（([^）]*?(已回應|待對帳|已成交|已執行)[^）]*)）/);
    if (st) { item.status = st[1].trim(); item.statusKind = /待對帳/.test(st[1]) ? 'pending' : 'done'; }
  }
  // 「現價 $258.44，距停損 $250.0 僅 3.3%，最大風險 US$110」型（沒有建議動作的貼近停損項）
  const lines = [body, ...(sub || [])].map(strip);
  for (const l of lines) {
    const pr = l.match(/現價\s*\$?\s*([\d,.]+)/);
    if (pr && item.price == null) item.price = num(pr[1]);
    if (/已在停損[^之]*之下/.test(l)) item.belowStop = true;
    const ds = l.match(/距停損\s*\$?\s*([\d,.]+)\s*僅\s*([\d.]+)%/);
    if (ds) { if (item.stop == null) item.stop = num(ds[1]); item.distPct = num(ds[2]); }
    const mr = l.match(/最大風險\s*(US\$[-\d,]+)/);
    if (mr) item.maxRisk = mr[1];
  }
  item.kind = item.action ? 'action' : 'near';
  item.note = (sub || []).map(strip).map((l) => l.replace(/^[-•]\s*/, '')).join('；') || null;
  return item;
}

function parseTodo(lines) {
  const items = [];
  let cur = null;
  for (const raw of lines) {
    const l = raw.replace(/\s+$/, '');
    const head = l.match(/^\s*\d+\.\s+(.*)$/);
    if (head) {
      if (cur) items.push(cur);
      cur = { head: head[1], sub: [] };
      continue;
    }
    if (cur && /^\s+[-•]\s+/.test(l)) cur.sub.push(l.trim().replace(/^[-•]\s+/, ''));
  }
  if (cur) items.push(cur);
  return items.map((it) => parseTodoItem(it.head, it.sub)).filter(Boolean);
}

function parseWaiting(lines) {
  const triggered = new Map(); // ticker+action → { ticker, action, actionLabel, date }
  const pending = [];
  let more = null;
  for (const raw of lines) {
    const l = raw.trim().replace(/^[-•]\s*/, '');
    if (!l) continue;
    const t = l.match(new RegExp(`^(?:⚠️|⚠|🔴|🟡)?\\s*\\*\\*(${TICKER})\\*\\*\\s*[：:]\\s*(\\S+?)\\s*條件已於\\s*(\\S+?)\\s*觸發[，,]?\\s*(.*)$`));
    if (t) {
      const key = `${t[1]}|${t[2]}`;
      const date = /^\d{4}-\d{2}-\d{2}$/.test(t[3]) ? t[3] : null;
      const prev = triggered.get(key);
      const status = strip(t[4]).replace(/\s*——.*$/, '');
      if (!prev) triggered.set(key, { ticker: t[1], actionLabel: t[2], action: matchActionToken(t[2])?.action || null, date, status, times: 1 });
      else { prev.times += 1; if (!prev.date && date) prev.date = date; else if (prev.date && date && date < prev.date) prev.date = date; }
      continue;
    }
    const p = l.match(new RegExp(`^(${TICKER})\\s*[（(]距\\s*([\\d.]+)%[）)]\\s*[：:]\\s*(.*)$`));
    if (p) { pending.push({ ticker: p[1], distPct: num(p[2]), condition: strip(p[3]) }); continue; }
    const m = l.match(/另有\s*(\d+)\s*條/);
    if (m) more = Number(m[1]);
  }
  return { triggered: [...triggered.values()], pending, more };
}

function parseRisks(lines) {
  const out = { exposure: null, totalStop: null, tickers: [], other: [] };
  for (const raw of lines) {
    const l = raw.trim().replace(/^[-•]\s*/, '');
    if (!l) continue;
    const ex = l.match(/^(🟡|🔴|⚠️|⚠)?\s*(.+?)\s*曝險\s*([\d.]+)%\s*[（(](\d+)\s*檔[）)]\s*(.*?)[：:]\s*(.*)$/);
    if (ex) {
      out.exposure = {
        level: ex[1] === '🔴' ? 'act' : 'watch', group: strip(ex[2]), pct: num(ex[3]), count: Number(ex[4]),
        note: strip(ex[5]), tickers: strip(ex[6]).split(/[,，、\s]+/).filter(Boolean),
      };
      continue;
    }
    const ts = l.match(/全部停損同時觸發\s*[：:]\s*約?\s*\*?\*?(US\$[-\d,]+)\s*[（(]組合\s*([-\d.]+)%[）)]/);
    if (ts) { out.totalStop = { amount: ts[1], pct: num(ts[2]) }; continue; }
    const tk = l.match(new RegExp(`^(🔴|🟡|⚠️|⚠)?\\s*\\*?\\*?(${TICKER})\\*?\\*?\\s*[（(]風險\\s*[：:]\\s*(\\S+?)[）)]\\s*[：:]\\s*(.*)$`));
    if (tk) { out.tickers.push({ ticker: tk[2], risk: tk[3], reason: strip(tk[4]), level: tk[1] === '🔴' ? 'act' : 'watch' }); continue; }
    out.other.push(strip(l));
  }
  return out;
}

/** 把晨報 markdown 切成結構化物件；`ok` 為 false 表示格式認不出來，請退回 markdown。 */
export function parseMorningBrief(md) {
  const src = (md || '').replace(/\r\n/g, '\n');
  const res = {
    ok: false, meta: parseMeta(src), regime: null, quality: null,
    todo: [], waiting: { triggered: [], pending: [], more: null },
    risks: { exposure: null, totalStop: null, tickers: [], other: [] },
    rest: { count: null, tickers: [] }, oneLiner: null, unparsed: [],
  };
  const rg = src.match(/^\*\*市場狀態\*\*\s*[：:]\s*(.+)$/m);
  if (rg) res.regime = strip(rg[1]);
  const q = src.match(/^\*\*資料品質\*\*\s*[：:]\s*(.+)$/m);
  if (q) res.quality = strip(q[1]).split(/[｜|]/).map((s) => s.trim()).filter(Boolean);
  const ol = src.match(/^>\s*\*\*戰情官一句話\*\*\s*[：:]\s*(.+)$/m);
  if (ol) res.oneLiner = strip(ol[1]);

  // 切 ### 段
  const sections = [];
  let cur = null;
  for (const line of src.split('\n')) {
    const h = line.match(/^###\s+(.*)$/);
    if (h) { cur = { title: strip(h[1]), lines: [] }; sections.push(cur); continue; }
    if (line.startsWith('## ') || line.startsWith('# ') || /^>\s*\*\*戰情官一句話/.test(line) || line.trim() === '---') { cur = null; continue; }
    if (cur) cur.lines.push(line);
  }
  for (const sec of sections) {
    if (/^今天需要處理/.test(sec.title)) { res.todo = parseTodo(sec.lines); res.ok = true; }
    else if (/^等待條件/.test(sec.title)) { res.waiting = parseWaiting(sec.lines); res.ok = true; }
    else if (/^最大風險/.test(sec.title)) { res.risks = parseRisks(sec.lines); res.ok = true; }
    else if (/^其餘/.test(sec.title)) {
      const c = sec.title.match(/其餘\s*(\d+)\s*檔/);
      res.rest = {
        count: c ? Number(c[1]) : null,
        label: sec.title.replace(/^其餘\s*\d+\s*檔\s*[：:]?\s*/, ''),
        tickers: sec.lines.join(' ').split(/[\s\u3000,，、]+/).map((s) => s.trim()).filter((s) => new RegExp(`^${TICKER}$`).test(s)),
      };
    } else {
      res.unparsed.push({ title: sec.title, md: sec.lines.join('\n').trim() });
    }
  }
  return res;
}

// 「今天要做」卡片的排序：破停損 → 停損／出場 → 減碼 → 加碼／建倉 → 其他；同級內尚未確認執行的在前。
const ACTION_RANK = { stop_loss: 0, exit: 0, sell: 0, trim: 1, reduce: 1, add: 2, open: 2, buy: 2, accumulate: 2 };
export function sortTodo(items) {
  return [...items].sort((a, b) => {
    if (a.belowStop !== b.belowStop) return a.belowStop ? -1 : 1;
    const ra = a.action in ACTION_RANK ? ACTION_RANK[a.action] : 5;
    const rb = b.action in ACTION_RANK ? ACTION_RANK[b.action] : 5;
    if (ra !== rb) return ra - rb;
    const ua = a.statusKind === 'unconfirmed' ? 0 : 1;
    const ub = b.statusKind === 'unconfirmed' ? 0 : 1;
    return ua - ub;
  });
}
