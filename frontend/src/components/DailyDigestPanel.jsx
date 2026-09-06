import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Link, useLocation } from 'wouter';
import axios from 'axios';
import {
  Newspaper, LineChart, ChevronDown, ChevronUp, ChevronLeft, ChevronRight,
  ExternalLink, CalendarDays, Loader2, RefreshCw, AlertTriangle, LayoutList, ArrowRight,
  Info, Target, CircleCheck, CircleAlert, CircleX, FileText, ClipboardCheck,
} from 'lucide-react';
import ReportReader from './ReportReader';
import { mergeDecisionsWithCards, wrActionMeta, aiActionDir, parseTickerBlocks } from './reviewMerge';

// 面板收合／分頁選擇記住使用者的選擇
const OPEN_LS_KEY = 'sr.portfolio.digestOpen';
// v2：加了「總覽」分頁，舊的記憶值會讓老使用者還是落在 AI 持倉，換 key 讓大家先看到總覽一次
// v3：「AI 持倉」併進「持倉復盤」，舊值 'ai' 已不存在，再換一次 key
const TAB_LS_KEY  = 'sr.portfolio.digestTab.v3';
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

// 三份日報各自是獨立的 pipeline，日期清單也各自不同。
const TABS = [
  {
    key: 'overview',
    label: '總覽',
    icon: LayoutList,
    api: null,                 // 總覽沒有自己的 endpoint，靠另外三份組出來
    blurb: '一天一頁：今天要處理什麼、組合姿態、今天就能執行的機會、必讀新聞',
    active: 'bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)]',
    dot: 'bg-[var(--accent-cyan)]',
    ring: 'ring-[var(--accent-cyan)]',
  },
  // active 的底色與文字色必須同一顆色 —— 這裡原本是 rgba(139,92,246)（紫）配
  // #578bfa（藍）、rgba(6,182,212)（青）配 #0052ff（藍），是舊深色主題留下的漂移。
  {
    // 2026-09-03 起「AI 持倉」併進來：War Room 是唯一決策層，05:00 的逐檔 AI 卡
    // 是它的證據層，逐檔擺在決策旁邊對照，不再自成一份日報。
    //
    // feed-only：復盤的完整畫面在 /review（PostMarketReview），這裡不再重畫一份。
    // 兩邊本來吃同一支 API、用同一批 parser，只是各畫各的 —— 同一份資料兩扇門。
    // 這份資料留著是因為總覽第一段「今天要處理」要用它，但沒有自己的分頁鈕。
    key: 'review',
    label: '持倉復盤',
    icon: ClipboardCheck,
    api: '/api/war-room/review',
    feedOnly: true,
    href: '/review',
    blurb: '🎖️ War Room 盤後復盤：每檔持倉明天怎麼做 + 05:00 預產的逐檔 AI 卡逐檔對照',
    active: 'bg-[rgba(87,139,250,0.16)] text-[var(--accent-purple-text)]',
    dot: 'bg-[var(--accent-purple)]',
    ring: 'ring-[var(--accent-purple)]',
  },
  {
    key: 'market',
    label: '市場個股',
    icon: LineChart,
    api: '/api/stock-digest',
    blurb: '每日盤勢報告：Macro · 財報 · AI/半導體 · 盤面 · Options · 持倉',
    active: 'bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)]',
    dot: 'bg-[var(--accent-cyan)]',
    ring: 'ring-[var(--accent-cyan)]',
  },
  {
    key: 'notes',
    label: '市場新聞',
    icon: Newspaper,
    api: '/api/market-news',
    blurb: '當天大盤／宏觀 + 我的持倉與觀察名單必讀新聞（AI 策展，每則附原文出處）',
    active: 'bg-[rgba(5,150,105,0.14)] text-[var(--accent-success-text)]',
    dot: 'bg-[var(--accent-green)]',
    ring: 'ring-[var(--accent-green)]',
  },
];
const TAB_BY_KEY = Object.fromEntries(TABS.map(t => [t.key, t]));
const SOURCE_TABS = TABS.filter(t => t.api);       // 真正有 endpoint 的三份
const NAV_TABS = TABS.filter(t => !t.feedOnly);   // 真的畫得出分頁的（復盤在 /review）

const GREEN = 'var(--accent-success)';
const RED   = 'var(--accent-danger)';
const AMBER = 'var(--accent-amber)';
// 小字專用：原色在白底只有 3.2:1，撐不住 11-13px 的資訊文字
const AMBER_TEXT = 'var(--accent-amber-text)';
const GREEN_TEXT = 'var(--accent-success-text)';
const CYAN  = 'var(--accent-cyan)';
const RATING_COLOR = { '強烈看多': GREEN, '偏多': GREEN, '中性': CYAN, '偏空': RED, '強烈看空': RED };
const ACTION_COLOR = { '分批買入': GREEN, '買入': GREEN, '持有': CYAN, '觀望': 'var(--text-muted)', '減碼': AMBER, '賣出': RED };

// 這裡的顏色全是 var(--…)，不是 hex —— `${RED}1A` 串出來的是無效的
// color 值，瀏覽器直接丟掉整條宣告：底色變透明、邊框退回 currentColor（黑）。
// 要疊透明度只能用 color-mix。
const tint = (color, pct) => `color-mix(in srgb, ${color} ${pct}%, transparent)`;

function scoreColor(s) {
  if (s == null) return 'var(--text-muted)';
  if (s >= 70) return GREEN;
  if (s >= 55) return '#6ee7b7';
  if (s >= 45) return CYAN;
  if (s >= 30) return AMBER;
  return RED;
}

const isoOf = (y, m, d) => `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

// 2026-08-23 → 六
function weekdayOf(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return WEEKDAYS[new Date(y, m - 1, d).getDay()];
}

// 2026-08-23 → 8/23（週六）
function labelDate(iso) {
  if (!iso) return '—';
  const [, m, d] = iso.split('-').map(Number);
  return `${m}/${d}（週${weekdayOf(iso)}）`;
}

// 策展新聞的 tldr 是一段 150-200 字的三段式（發生什麼。跟我持倉的關係。要留意什麼。），
// 攤成一整段就得全部讀完才找得到第三段。依句號切開，一句一條。
// 不用 lookbehind：Safari 16.4 以前不支援，這頁會整個掛掉。
function splitSentences(text) {
  const out = [];
  const re = /[^。]*。[」』）)]*|[^。]+$/g;
  let m;
  while ((m = re.exec(String(text || ''))) !== null) {
    const t = m[0].trim();
    if (t) out.push(t);
  }
  return out;
}

// ── 市場新聞：單則策展新聞 ───────────────────────────────────────────────
const IMPACT_STYLE = {
  '利多': { color: 'var(--accent-success)', bg: 'rgba(16,185,129,0.12)' },
  '利空': { color: 'var(--accent-danger)',  bg: 'rgba(239,68,68,0.12)' },
  '中性': { color: 'var(--accent-cyan)',    bg: 'rgba(6,182,212,0.12)' },
};

// 2026-08-23T20:16:00+08:00 → 8/23 20:16
function labelTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function SourceLink({ src }) {
  const time = labelTime(src.published_at);
  return (
    <a href={src.url} target="_blank" rel="noreferrer"
       className="inline-flex items-center gap-1 text-[13px] text-[var(--accent-cyan)] hover:underline">
      <ExternalLink size={13} />
      {src.source || '原文'}{time && <span className="text-[var(--text-meta)]">· {time}</span>}
    </a>
  );
}

function NewsRow({ item, index }) {
  const [open, setOpen] = useState(false);
  const sources = item.sources || [];
  const extra = sources.slice(1);
  const impact = IMPACT_STYLE[item.impact];
  const points = splitSentences(item.tldr);

  return (
    <li
      className="px-3 py-2.5 transition-colors hover:bg-[var(--bg-secondary)]"
      style={{ borderLeft: `3px solid ${impact ? impact.color : 'var(--glass-border)'}` }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="w-4 shrink-0 text-right font-mono text-[13px] tabular-nums text-[var(--text-meta)]">{index}</span>
        <h3 className="max-w-[76ch] text-[17px] font-semibold leading-snug text-[var(--text-primary)]">{item.title}</h3>
        {item.tickers?.map((t) => (
          <Link key={t} href={`/stock/${encodeURIComponent(t)}`}
                className="rounded px-1.5 py-0.5 font-mono text-[12px] font-semibold text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)] hover:bg-[rgba(16,185,129,0.18)] transition-colors">
            ${t}
          </Link>
        ))}
        {impact && (
          <span className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold"
                style={{ color: impact.color, background: impact.bg }}>
            {item.impact}
          </span>
        )}
      </div>

      {points.length > 0 && (
        <ul className="mt-1 space-y-0.5 pl-6">
          {points.map((t, k) => (
            <li key={k} className="flex gap-1.5 text-[15px] leading-relaxed text-[var(--text-body)]">
              <span className="shrink-0 text-[var(--text-meta)]">•</span>
              <span className="max-w-[76ch]">{t}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 pl-6">
        {sources[0] && <SourceLink src={sources[0]} />}
        {extra.length > 0 && (
          <button onClick={() => setOpen((o) => !o)}
                  className="tap-44 digest-focusable inline-flex cursor-pointer items-center gap-1 rounded text-[13px] text-[var(--text-meta)] transition-colors duration-150 hover:text-[var(--text-primary)]">
            {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            另外 {extra.length} 則出處
          </button>
        )}
      </div>

      {open && extra.length > 0 && (
        <ul className="mt-1.5 space-y-1 border-t border-[var(--glass-border)] pt-1.5 pl-6">
          {extra.map((src, i) => (
            <li key={i} className="flex gap-1.5 text-xs leading-snug text-[var(--text-meta)]">
              <span className="shrink-0">•</span>
              <span>
                <span className="text-[var(--text-secondary)]">{src.title}</span>{' '}
                <SourceLink src={src} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

// ── 月曆彈出層：有報告的日子才可點，其餘淡出 ─────────────────────────────
function DigestCalendar({ dates, active, accent, onPick, onClose }) {
  const dateSet = useMemo(() => new Set(dates), [dates]);
  const [view, setView] = useState(() => {
    const base = active || dates[0] || new Date().toISOString().slice(0, 10);
    const [y, m] = base.split('-').map(Number);
    return { year: y, month: m - 1 };
  });

  const { year, month } = view;
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const todayStr = new Date().toISOString().slice(0, 10);

  const cells = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(isoOf(year, month, d));

  const monthCount = dates.filter(d =>
    d.startsWith(`${year}-${String(month + 1).padStart(2, '0')}-`)).length;

  const step = (delta) => setView(({ year, month }) => {
    const m = month + delta;
    if (m < 0) return { year: year - 1, month: 11 };
    if (m > 11) return { year: year + 1, month: 0 };
    return { year, month: m };
  });

  return (
    <div className="absolute right-0 top-full mt-2 z-50 w-[286px] rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-3 shadow-2xl shadow-[rgba(10,11,13,0.35)]">
      <div className="flex items-center justify-between mb-3">
        <button onClick={() => step(-1)}
          className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-all">
          <ChevronLeft size={16} />
        </button>
        <div className="text-center">
          <p className="text-sm font-bold">{year} 年 {month + 1} 月</p>
          <p className="text-[10px] text-[var(--text-muted)]">本月 {monthCount} 份</p>
        </div>
        <button onClick={() => step(1)}
          className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-all">
          <ChevronRight size={16} />
        </button>
      </div>

      <div className="grid grid-cols-7 mb-1">
        {WEEKDAYS.map(w => (
          <div key={w} className="text-center text-[10px] font-semibold text-[var(--text-muted)] py-1">{w}</div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1">
        {cells.map((iso, i) => {
          if (!iso) return <div key={`e-${i}`} />;
          const day = Number(iso.slice(8));
          const has = dateSet.has(iso);
          const isActive = iso === active;
          const isToday = iso === todayStr;
          return (
            <button
              key={iso}
              disabled={!has}
              onClick={() => { onPick(iso); onClose(); }}
              className={`relative h-9 rounded-lg text-xs font-semibold transition-all ${
                isActive
                  ? `bg-[var(--bg-tertiary)] text-[var(--text-primary)] ring-1 ${accent.ring}`
                  : has
                    ? 'text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] cursor-pointer'
                    : 'text-[var(--text-muted)] opacity-35 cursor-default'
              } ${isToday && !isActive ? 'ring-1 ring-[var(--glass-border)]' : ''}`}
            >
              {day}
              {has && !isActive && (
                <span className={`absolute bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full ${accent.dot}`} />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── 持倉復盤：一檔持倉只出現一次 ─────────────────────────────────────────
// 這一頁曾經是四塊東西疊在一起：今日焦點表（從晨報文字解析）、逐檔對照表
// （journal × AI 卡）、收合的 AI 卡格、再加整份復盤全文 —— 同一檔 OUST 在頁上
// 出現四次，每次給的欄位都不一樣，讀者得自己把它們對起來。
//
// 現在只有一張表：一列一檔，這檔的所有資訊（War Room 動作與執行狀態、現價／停損／
// 距離／最大風險、AI 卡評分與建議、明日怎麼做）都掛在同一列；展開那一列才看到
// 這檔在復盤全文裡的逐檔論述（今日原因／Mimi／技術／明日）與 AI 卡的完整內容。
// 組合層的段落（帳戶總評、大盤形勢、組合層風險、執行狀態）不是逐檔的，單獨放在
// 表格下方；整份原文收在最底下，只給要對照原句的人。

const WR_ACTION_COLOR = { '-1': RED, '1': GREEN, '0': CYAN };
const wrColor = (action) => (action ? WR_ACTION_COLOR[String(wrActionMeta(action).dir)] : 'var(--text-muted)');

const REGIME_META = {
  'risk-on':  { label: 'RISK-ON',  color: GREEN_TEXT },
  risk_on:    { label: 'RISK-ON',  color: GREEN_TEXT },
  'risk-off': { label: 'RISK-OFF', color: RED },
  risk_off:   { label: 'RISK-OFF', color: RED },
  mixed:      { label: 'MIXED',    color: AMBER_TEXT },
};

const RISK_LABEL = {
  low:    { label: '低', color: GREEN_TEXT },
  medium: { label: '中', color: AMBER_TEXT },
  high:   { label: '高', color: RED },
};

const DIVERGENCE = {
  opposite: { label: 'AI 反向', color: RED, title: 'AI 卡的建議方向與 War Room 相反 —— 值得多看一眼' },
  lean:     { label: '不同調', color: 'var(--text-meta)', title: '一邊要動、一邊不動' },
};

function DivergenceBadge({ kind }) {
  const d = DIVERGENCE[kind];
  if (!d) return null;
  return (
    <span className="whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-bold"
          style={{ color: d.color, background: tint(d.color, 12) }} title={d.title}>
      {d.label}
    </span>
  );
}

function WrActionPill({ action }) {
  if (!action) return <span className="text-[var(--text-muted)]">—</span>;
  const color = wrColor(action);
  return (
    <span className="whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-bold"
          style={{ color, background: tint(color, 12) }}>
      {wrActionMeta(action).label}
    </span>
  );
}

function PulseStat({ label, value, sub, tone = 'var(--text-primary)' }) {
  return (
    <div className="min-w-[92px]">
      <div className="font-mono text-xl font-bold leading-none tabular-nums" style={{ color: tone }}>{value}</div>
      <div className="mt-1 text-[11px] text-[var(--text-meta)]">{label}</div>
      {sub && <div className="text-[10px] text-[var(--text-muted)]">{sub}</div>}
    </div>
  );
}

// ── 每一列的資料：journal 決策 × 晨報數字 × 全文逐檔段落 × 帳本 × AI 卡 × 收盤價 ──
// 分組是「今天對這檔要做什麼」：先出場減碼，再加碼建倉，再貼近停損的觀察，
// 最後才是沒事的持有。同一檔只會落在一組。
const HOLD_GROUPS = [
  { key: 'stop',  title: '停損／出場', hint: '減少曝險',                   color: RED },
  { key: 'trim',  title: '減碼',       hint: '減少曝險',                   color: AMBER },
  { key: 'add',   title: '加倉',       hint: '加碼既有部位',               color: GREEN },
  { key: 'open',  title: '建倉',       hint: '開新部位',                   color: GREEN },
  { key: 'watch', title: '觀察',       hint: '持有，但貼近停損或風險 high', color: CYAN,  dim: true },
  { key: 'hold',  title: '持有',       hint: '維持，無新事件',             color: 'var(--text-muted)', dim: true, folded: true },
  { key: 'other', title: '只有 AI 卡', hint: '復盤沒有這檔的決策',         color: 'var(--text-muted)', dim: true, folded: true },
];
const GROUP_BY_KEY = Object.fromEntries(HOLD_GROUPS.map(g => [g.key, g]));
const MOVE_GROUPS = new Set(['stop', 'trim', 'add', 'open']);

const GROUP_OF_ACTION = {
  stop_loss: 'stop', exit: 'stop', sell: 'stop', sell_short: 'stop',
  trim: 'trim', reduce: 'trim',
  add: 'add', accumulate: 'add', buy: 'add',
  open: 'open',
};

function buildHoldings({ journal, cards, md, focus, ledger, weights, quotes }) {
  const base = mergeDecisionsWithCards(journal?.decisions, cards?.cards, cards?.holdings, weights);
  const blocks = parseTickerBlocks(md);
  const focusBy = new Map();
  for (const key of ['stop', 'breached', 'trim', 'add', 'open', 'watch', 'highRisk']) {
    for (const it of (focus?.[key] || [])) if (!focusBy.has(it.ticker)) focusBy.set(it.ticker, { ...it, bucket: key });
  }
  const openBy = new Map((ledger?.open_decisions || []).map(r => [String(r.ticker).toUpperCase(), r]));

  return base.map(r => {
    const d = r.decision;
    const f = focusBy.get(r.ticker);
    const q = quotes?.[r.ticker];
    const stopNum = num(f?.stopRef || f?.stopPx) ?? (d?.stop != null && d.stop !== '' ? num(d.stop) : null);
    const reportPrice = f?.price ? num(f.price) : null;
    const price = reportPrice ?? (q?.price ?? null);
    const priceFromQuote = reportPrice == null && q?.price != null;
    let dist = f?.below ? 0 : (f?.dist ?? null);
    let distComputed = false;
    if (dist == null && price && stopNum) { dist = ((price - stopNum) / price) * 100; distComputed = true; }

    let group;
    if (!d) group = 'other';
    else if (GROUP_OF_ACTION[d.action]) group = GROUP_OF_ACTION[d.action];
    else if (f || d.risk_level === 'high' || (dist != null && dist <= 4)) group = 'watch';
    else group = 'hold';

    return {
      ...r,
      group,
      price, priceFromQuote, quoteDate: q?.last_date || null,
      stopNum, dist, distComputed,
      below: Boolean(f?.below),
      riskUsd: f?.risk ?? null,
      exec: f?.exec || '',
      why: f?.why || '',
      block: blocks[r.ticker] || null,
      open: openBy.get(r.ticker) || null,
    };
  });
}

// ── 統計列：整份復盤最該先看到的幾個數字，只出現這一次 ───────────────────
function ReviewStats({ journal, cards, focus, rows }) {
  const regime = REGIME_META[journal?.market_regime];
  const move = rows.filter(r => MOVE_GROUPS.has(r.group));
  const byGroup = (k) => rows.filter(r => r.group === k).length;
  const todo = rows.filter(r => r.exec === 'todo').length;
  const recon = rows.filter(r => r.exec === 'recon').length;
  const opposite = rows.filter(r => r.divergence === 'opposite').length;
  const { totalRisk, exposure } = focus || {};
  const list = cards?.cards || [];
  if (!journal && !cards) return null;

  const breakdown = [['stop', '停損'], ['trim', '減碼'], ['add', '加倉'], ['open', '建倉']]
    .map(([k, l]) => [byGroup(k), l]).filter(([n]) => n > 0).map(([n, l]) => `${l} ${n}`).join(' · ');

  return (
    <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-4 py-3">
      <div className="flex flex-wrap items-start gap-x-7 gap-y-4 lg:[&>div+div]:border-l lg:[&>div+div]:border-[var(--glass-border)] lg:[&>div+div]:pl-7">
        {regime && <PulseStat label="市場狀態" value={regime.label} sub="War Room 判讀" tone={regime.color} />}
        {journal
          ? <PulseStat label="今天要動" value={move.length} sub={breakdown || `共 ${rows.length} 檔持倉`} tone={move.length ? AMBER_TEXT : 'var(--text-primary)'} />
          : <PulseStat label="今天要動" value="—" sub="這天復盤沒產出" tone="var(--text-muted)" />}
        {(todo > 0 || recon > 0) && (
          <PulseStat label="待執行 / 待對帳" value={`${todo} / ${recon}`} sub="還沒下單 / 下過單待確認" tone={todo ? AMBER_TEXT : 'var(--text-secondary)'} />
        )}
        {totalRisk && (
          <PulseStat label="全部停損同時觸發" value={usd(totalRisk.usd)} sub={`組合 ${totalRisk.pct}%`} tone={RED} />
        )}
        {exposure && (
          <PulseStat label={`${exposure.theme} 曝險`} value={`${exposure.pct}%`}
                     sub={`${exposure.count} 檔 · 警戒 ${exposure.limit}%`}
                     tone={exposure.pct > exposure.limit ? AMBER_TEXT : 'var(--text-primary)'} />
        )}
        {cards
          ? <PulseStat label="AI 卡" value={`${cards.ok ?? list.length}/${cards.total ?? list.length}`}
                       sub={journal ? (opposite ? `${opposite} 檔與復盤反向` : '無方向相反') : '05:00 預產，證據層'}
                       tone={opposite ? AMBER_TEXT : 'var(--text-primary)'} />
          : <PulseStat label="AI 卡" value="—" sub="05:00 預產沒跑" tone="var(--text-muted)" />}
      </div>
      {cards?.errors?.length > 0 && (
        <p className="mt-3 border-t border-[var(--glass-border)] pt-2.5 text-[11px] text-[var(--text-muted)]">
          <span style={{ color: AMBER_TEXT }}>{cards.errors.length} 檔 AI 卡沒跑出來</span>
          <span className="ml-1.5 font-mono">{cards.errors.join('、')}</span>
        </p>
      )}
    </section>
  );
}

// ── 排序 ───────────────────────────────────────────────────────────────────
const RISK_RANK = { low: 0, medium: 1, high: 2 };
const HOLD_SORT = {
  ticker: (r) => r.ticker,
  weight: (r) => r.weight,
  wr:     (r) => (r.decision ? wrActionMeta(r.decision.action).dir : null),
  exec:   (r) => (r.exec === 'todo' ? 0 : r.exec === 'recon' ? 1 : null),
  price:  (r) => r.price,
  stop:   (r) => r.stopNum,
  dist:   (r) => (r.below ? -Infinity : r.dist),
  target: (r) => r.decision?.target_weight_pct ?? null,
  riskUsd:(r) => r.riskUsd,
  risk:   (r) => (r.decision?.risk_level in RISK_RANK ? RISK_RANK[r.decision.risk_level] : null),
  score:  (r) => r.card?.score ?? null,
  ai:     (r) => (r.card?.action ? (aiActionDir(r.card.action) ?? null) : null),
};

// 預設順序＝處理順序：已跌破 → 距停損近 → 重倉
function byUrgencyRow(a, b) {
  if (a.below !== b.below) return a.below ? -1 : 1;
  const ad = a.dist ?? Infinity, bd = b.dist ?? Infinity;
  if (ad !== bd) return ad - bd;
  return (b.weight ?? -1) - (a.weight ?? -1);
}

function sortRows(rows, sort) {
  if (!sort?.key || !HOLD_SORT[sort.key]) return [...rows].sort(byUrgencyRow);
  const get = HOLD_SORT[sort.key];
  const sign = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = get(a), vb = get(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string') return sign * va.localeCompare(vb);
    return sign * (va - vb);
  });
}

// 欄位定義：寬度寫死（auto layout 會把截斷後多出來的空間全塞給第一欄）。
const HOLD_COLS = [
  { k: 'ticker',  label: '代號',      w: '9.5rem',  first: 'asc' },
  { k: 'weight',  label: '權重',      w: '5rem',    first: 'desc', align: 'right' },
  { k: 'wr',      label: '動作',      w: '5.5rem',  first: 'asc',  title: '依方向：減碼／停損 → 不動 → 加碼' },
  { k: 'exec',    label: '執行',      w: '4.75rem', first: 'asc',  title: '待執行在前' },
  { k: 'price',   label: '現價',      w: '5.75rem', first: 'desc', align: 'right' },
  { k: 'stop',    label: '停損',      w: '5.75rem', first: 'desc', align: 'right' },
  { k: 'dist',    label: '距停損',    w: '5rem',    first: 'asc',  align: 'right', title: '近的在前' },
  { k: 'target',  label: '目標倉位',  w: '5rem',    first: 'desc', align: 'right' },
  { k: 'riskUsd', label: '最大風險',  w: '5.5rem',  first: 'desc', align: 'right' },
  { k: 'risk',    label: '風險',      w: '3.5rem',  first: 'desc' },
  { k: 'score',   label: 'AI',        w: '9.5rem',  first: 'desc', title: '依 AI 評分' },
  { k: 'note',    label: '明日／論點', w: 'auto' },
  { k: 'more',    label: '',          w: '2.25rem' },
];
const COL_BY_KEY = Object.fromEntries(HOLD_COLS.map(c => [c.k, c]));

function SortTh({ col, sort, onSort }) {
  const meta = COL_BY_KEY[col];
  const sortable = Boolean(meta.first) && Boolean(onSort);
  const active = sort?.key === col;
  const align = meta.align === 'right' ? 'text-right' : 'text-left';
  const inner = (
    <>
      {meta.label}
      {sortable && (active
        ? (sort.dir === 'asc' ? <ChevronUp size={11} aria-hidden="true" /> : <ChevronDown size={11} aria-hidden="true" />)
        : <span aria-hidden="true" className="inline-block w-[11px]" />)}
    </>
  );
  return (
    <th className={`whitespace-nowrap px-2.5 py-2 text-[12px] font-semibold text-[var(--text-meta)] ${align}`}>
      {sortable ? (
        <button type="button" onClick={() => onSort(col)} title={meta.title || `依${meta.label}排序`}
                aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                className={`digest-focusable inline-flex items-center gap-0.5 transition-colors hover:text-[var(--text-primary)] ${
                  active ? 'text-[var(--accent-cyan-text)]' : ''}`}>
          {inner}
        </button>
      ) : inner}
    </th>
  );
}

const dash = <span className="text-[var(--text-muted)]">—</span>;
const money2 = (v) => (v == null ? null : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

// 展開一列才看到的內容：這檔在復盤全文裡的逐檔論述 + AI 卡完整內容，並排。
function Field({ label, children, tone }) {
  return (
    <div className="flex gap-2 text-[12.5px] leading-relaxed">
      <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">{label}</dt>
      <dd className="min-w-0 max-w-[76ch] text-[var(--text-body)]" style={tone ? { color: tone } : undefined}>{children}</dd>
    </div>
  );
}

function HoldingDetail({ r }) {
  const d = r.decision;
  const b = r.block;
  const c = r.card;
  const ack = d?.ack_prev;
  const ackText = ack ? (typeof ack === 'string' ? ack : [ack.verdict, ack.note].filter(Boolean).join('：')) : '';
  const hasWR = Boolean(b || d?.trigger || d?.thesis || r.why || r.open || ackText);
  if (!hasWR && !c) {
    return <p className="px-3 py-2 text-xs text-[var(--text-meta)]">這檔沒有更多內容。</p>;
  }
  return (
    <div className="grid gap-x-8 gap-y-4 px-3 py-3 lg:grid-cols-2">
      {hasWR && (
        <dl className="space-y-1.5">
          <p className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-meta)]">🎖️ War Room</p>
          {b?.reason && <Field label="今日原因">{b.reason}</Field>}
          {b?.mimi && <Field label="Mimi">{b.mimi}</Field>}
          {b?.tech && <Field label="技術">{b.tech}</Field>}
          {b?.verdict && (
            <Field label="明日" tone="var(--text-primary)">
              <b>{b.verdict.emoji && `${b.verdict.emoji} `}{b.verdict.text}</b>
            </Field>
          )}
          {!b && d?.thesis && <Field label="論點">{d.thesis}</Field>}
          {d?.trigger && (
            <Field label="觸發">
              {d.trigger}
              {d.confidence != null && <span className="ml-2 text-[var(--text-muted)]">信心 {Math.round(d.confidence * 100)}%</span>}
            </Field>
          )}
          {r.open && (
            <Field label="帳本">
              {mmddOf(r.open.proposed_date)} 提出，{r.open.streak} 份日誌
              {r.open.status === 'triggered' && <b className="ml-2" style={{ color: RED }}>✅ {mmddOf(r.open.hit_date)} 已觸發（收 {r.open.last_close}）</b>}
              {r.open.status === 'pending' && r.open.distance_pct != null && <span className="ml-2">距觸發 {r.open.distance_pct > 0 ? '+' : ''}{r.open.distance_pct}%</span>}
            </Field>
          )}
          {ackText && <Field label="前次回應">{ackText}</Field>}
          {r.why && <Field label="風險 high" tone={RED}>{r.why}</Field>}
        </dl>
      )}
      {c && (
        <dl className="space-y-1.5">
          <p className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-[var(--text-meta)]">
            🧑‍🍳 AI 卡
            <span className="font-mono text-[var(--text-muted)]">05:00 預產 · 證據層</span>
            <Link href={`/stock/${r.ticker}/ai`} className="ml-auto inline-flex items-center gap-1 text-[var(--accent-cyan)] hover:opacity-70">
              完整單檔分析 <ExternalLink size={11} />
            </Link>
          </p>
          {c.summary && <Field label="核心" tone="var(--text-primary)"><b>{c.summary}</b></Field>}
          {c.trend && <Field label="趨勢">{c.trend}</Field>}
          <Field label="價位">
            <span className="mr-3">進場 <b style={{ color: GREEN }}>{c.buy_zone || '—'}</b></span>
            <span className="mr-3">目標 <b style={{ color: CYAN }}>{c.target || '—'}</b></span>
            <span>停損 <b style={{ color: RED }}>{c.stop_loss || '—'}</b></span>
          </Field>
          {c.risks?.length > 0 && <Field label="風險" tone={RED}>{c.risks.join('；')}</Field>}
          {c.catalysts?.length > 0 && <Field label="催化" tone={GREEN_TEXT}>{c.catalysts.join('；')}</Field>}
          {c.analysis_bullets?.length > 0 && (
            <ul className="space-y-1 border-t border-[var(--glass-border)] pt-2">
              {c.analysis_bullets.map((bl, i) => (
                <li key={i} className="flex gap-1.5 text-xs leading-relaxed text-[var(--text-secondary)]">
                  <span className="text-[var(--text-muted)]">•</span>
                  <span>
                    {bl.tag && <span className="font-semibold text-[var(--text-primary)]">{bl.tag}：</span>}
                    {bl.point}
                    {bl.sources?.length > 0 && (
                      <span className="text-[10px] text-[var(--text-muted)]">
                        （來源：{bl.sources.map((sc, j) => (
                          <span key={j}>
                            {j > 0 && '、'}
                            {sc.url
                              ? <a href={sc.url} target="_blank" rel="noreferrer" title={sc.title || ''} className="text-[var(--accent-cyan)] underline decoration-dotted hover:opacity-70">{sc.label}</a>
                              : sc.label}
                          </span>
                        ))}）
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </dl>
      )}
    </div>
  );
}

const mmddOf = (iso) => (iso ? `${Number(String(iso).slice(5, 7))}/${Number(String(iso).slice(8, 10))}` : '—');

function HoldingRow({ r, color, showAccount, open, onToggle, compact }) {
  const d = r.decision;
  const c = r.card;
  const ex = EXEC[r.exec];
  const risk = RISK_LABEL[d?.risk_level];
  const divColor = r.divergence === 'opposite' ? RED : null;
  const note = r.block?.verdict?.text || d?.thesis || c?.summary || '';
  const noteLabel = r.block?.verdict ? '明日' : d?.thesis ? '論點' : c?.summary ? 'AI' : '';
  const distCell = r.below
    ? <b style={{ color }}>已跌破</b>
    : r.dist == null ? dash
      : <span style={r.distComputed ? { color: r.dist < 0 ? RED : 'var(--text-meta)' } : undefined}
              title={r.distComputed ? `依 ${r.quoteDate || '最後'} 收盤價補算，非報告原文` : undefined}>
          {r.dist < 0 ? '−' : ''}{Math.abs(r.dist).toFixed(1)}%
        </span>;
  return (
    <>
      <tr className={`border-t border-[var(--glass-border)] transition-colors hover:bg-[var(--bg-tertiary)] ${open ? 'bg-[var(--bg-secondary)]' : ''}`}
          style={divColor ? { boxShadow: `inset 3px 0 0 ${divColor}` } : undefined}>
        <td className="px-2.5 whitespace-nowrap">
          <Link href={`/stock/${r.ticker}`}
                className="digest-focusable font-mono text-[15px] font-bold text-[var(--text-primary)] hover:underline">
            {r.ticker}
          </Link>
          {showAccount && r.portfolios?.length > 0 && (
            <span className="ml-1.5 text-[10px] text-[var(--text-muted)]">{r.portfolios.join('・')}</span>
          )}
          {r.divergence && <span className="ml-1.5"><DivergenceBadge kind={r.divergence} /></span>}
        </td>
        <td className="px-2.5 text-right font-mono tabular-nums">{r.weight != null ? `${r.weight.toFixed(1)}%` : dash}</td>
        <td className="px-2.5"><WrActionPill action={d?.action} /></td>
        <td className="px-2.5">
          {ex
            ? <span className="whitespace-nowrap rounded px-1.5 py-0.5 text-[12px] font-semibold"
                    style={{ color: ex.tone, background: tint(ex.tone, 10) }} title={ex.title}>{ex.label}</span>
            : dash}
        </td>
        <td className="px-2.5 text-right font-mono tabular-nums"
            style={r.below ? { color } : r.priceFromQuote ? { color: 'var(--text-meta)' } : undefined}
            title={r.priceFromQuote ? `${r.quoteDate || '最後'} 收盤價（報告未附，系統補上）` : undefined}>
          {money2(r.price) || dash}
        </td>
        <td className="px-2.5 text-right font-mono tabular-nums">{money2(r.stopNum) || dash}</td>
        <td className="px-2.5 text-right font-mono tabular-nums">{distCell}</td>
        <td className="px-2.5 text-right font-mono tabular-nums">{d?.target_weight_pct != null ? `${d.target_weight_pct}%` : dash}</td>
        <td className="px-2.5 text-right font-mono tabular-nums">{r.riskUsd != null ? usd(r.riskUsd) : dash}</td>
        <td className="px-2.5">{risk ? <span className="font-semibold" style={{ color: risk.color }}>{risk.label}</span> : dash}</td>
        <td className="px-2.5 whitespace-nowrap">
          {c ? (
            <span className="inline-flex items-center gap-1.5">
              <b className="font-mono tabular-nums" style={{ color: scoreColor(c.score) }}>{c.score ?? '—'}</b>
              <span className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                    style={{ background: tint(RATING_COLOR[c.rating] || CYAN, 12), color: RATING_COLOR[c.rating] || CYAN }}>
                {c.rating || '—'}
              </span>
              <span className="text-[12px] font-semibold" style={{ color: ACTION_COLOR[c.action] || CYAN }}>{c.action || '—'}</span>
            </span>
          ) : <span className="text-[var(--text-muted)]">無卡</span>}
        </td>
        <td className="max-w-0 truncate px-2.5 text-[13px] text-[var(--text-secondary)]" title={note}>
          {noteLabel && <span className="mr-1 text-[10px] text-[var(--text-muted)]">{noteLabel}</span>}
          {note || dash}
        </td>
        <td className="px-1.5 text-right">
          {!compact && (
            <button type="button" onClick={onToggle} aria-expanded={open}
                    title={open ? '收起' : '展開這檔的復盤論述與 AI 卡'}
                    className="digest-focusable rounded p-1 text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]">
              {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-[var(--bg-secondary)]">
          <td colSpan={HOLD_COLS.length} className="border-t border-dashed border-[var(--glass-border)] p-0">
            <HoldingDetail r={r} />
          </td>
        </tr>
      )}
    </>
  );
}

function HoldGroupHead({ group, items, folded, onFold }) {
  const todo = items.filter(it => it.exec === 'todo').length;
  const recon = items.filter(it => it.exec === 'recon').length;
  return (
    <tr>
      <th colSpan={HOLD_COLS.length}
          className="border-t border-[var(--glass-border)] px-2.5 py-1.5 text-left font-normal"
          style={{ background: group.dim ? 'var(--bg-secondary)' : tint(group.color, 9) }}>
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-xl font-extrabold tabular-nums" style={{ color: group.color }}>{items.length}</span>
          <span className="text-[15px] font-semibold text-[var(--text-primary)]">{group.title}</span>
          <span className="text-[12.5px] font-normal text-[var(--text-muted)]">{group.hint}</span>
          {(todo > 0 || recon > 0) && (
            <span className="ml-auto text-[12.5px] font-normal text-[var(--text-meta)]">
              {todo > 0 && <b style={{ color: AMBER_TEXT }}>待執行 {todo}</b>}
              {todo > 0 && recon > 0 && ' · '}
              {recon > 0 && `待對帳 ${recon}`}
            </span>
          )}
          {onFold && (
            <button type="button" onClick={onFold}
                    className={`digest-focusable inline-flex items-center gap-0.5 text-[12px] text-[var(--text-meta)] hover:text-[var(--text-primary)] ${
                      (todo > 0 || recon > 0) ? 'ml-3' : 'ml-auto'}`}>
              {folded ? <>展開 <ChevronDown size={12} /></> : <>收起 <ChevronUp size={12} /></>}
            </button>
          )}
        </span>
      </th>
    </tr>
  );
}

const HOLD_FILTERS = [
  { key: 'all',    label: '全部' },
  { key: 'move',   label: '要動的' },
  { key: 'differ', label: 'AI 反向／不同調' },
];
// 'all' 全收；沒寫帳戶的列歸「其他」
const matchAccount = (r, name) => name === 'all'
  || (r.portfolios?.length ? r.portfolios.includes(name) : name === '其他');

/**
 * @param {boolean} compact  總覽用：只列要動的那幾組，沒有篩選、排序、展開
 */
function HoldingsTable({ rows, account, onAccount, compact = false }) {
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState({ key: null, dir: 'desc' });
  const [openTicker, setOpenTicker] = useState(null);
  const [folded, setFolded] = useState(() => new Set(HOLD_GROUPS.filter(g => g.folded).map(g => g.key)));

  const accounts = useMemo(() => {
    const seen = [];
    for (const r of rows) for (const p of (r.portfolios?.length ? r.portfolios : ['其他'])) if (!seen.includes(p)) seen.push(p);
    return seen;
  }, [rows]);
  const scoped = useMemo(() => rows.filter(r => matchAccount(r, account)), [rows, account]);
  const counts = {
    all: scoped.length,
    move: scoped.filter(r => MOVE_GROUPS.has(r.group)).length,
    differ: scoped.filter(r => r.divergence).length,
  };
  const accountCount = (name) => rows.filter(r => matchAccount(r, name)).length;

  const groups = useMemo(() => {
    const visible = scoped.filter(r => {
      if (compact) return MOVE_GROUPS.has(r.group);
      if (filter === 'move') return MOVE_GROUPS.has(r.group);
      if (filter === 'differ') return Boolean(r.divergence);
      return true;
    });
    return HOLD_GROUPS
      .map(g => ({ ...g, items: sortRows(visible.filter(r => r.group === g.key), sort) }))
      .filter(g => g.items.length);
  }, [scoped, filter, sort, compact]);

  if (!rows.length) return null;

  const onSort = (key) => setSort(cur => {
    if (cur.key !== key) return { key, dir: COL_BY_KEY[key].first };
    const flipped = cur.dir === 'asc' ? 'desc' : 'asc';
    return flipped === COL_BY_KEY[key].first ? { key: null, dir: 'desc' } : { key, dir: flipped };
  });
  const toggleFold = (k) => setFolded(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const chip = (active) => `digest-focusable rounded-full border px-2 py-0.5 text-[11px] font-semibold transition-colors ${
    active
      ? 'border-[var(--accent-cyan)] bg-[rgba(0,82,255,0.10)] text-[var(--accent-cyan-text)]'
      : 'border-[var(--glass-border)] text-[var(--text-meta)] hover:text-[var(--text-primary)]'}`;
  const nFilled = scoped.filter(r => r.priceFromQuote).length;
  const quoteDate = scoped.map(r => r.quoteDate).find(Boolean);

  return (
    <section className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {!compact && (
          <h3 className="flex items-baseline gap-2 text-sm font-bold text-[var(--text-primary)]">
            <LayoutList size={14} className="translate-y-[2px] text-[var(--text-meta)]" aria-hidden="true" />
            逐檔持倉
            <span className="text-xs font-normal text-[var(--text-meta)]">依操作分組 · 展開看復盤論述與 AI 卡</span>
          </h3>
        )}
        {accounts.length > 1 && onAccount && (
          <div className="flex items-center gap-0.5 rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-0.5"
               role="group" aria-label="帳戶">
            {[{ key: 'all', label: '全部帳戶', n: rows.length }, ...accounts.map(a => ({ key: a, label: a, n: accountCount(a) }))].map(a => (
              <button key={a.key} onClick={() => onAccount(a.key)} aria-pressed={account === a.key}
                      className={`digest-focusable rounded-md px-2 py-0.5 text-[11px] font-semibold transition-colors ${
                        account === a.key
                          ? 'bg-[var(--bg-primary)] text-[var(--text-primary)] shadow-[0_1px_2px_rgba(10,11,13,0.08)]'
                          : 'text-[var(--text-meta)] hover:text-[var(--text-primary)]'}`}>
                {a.label} <span className="font-mono font-normal tabular-nums">{a.n}</span>
              </button>
            ))}
          </div>
        )}
        {!compact && (
          <div className="flex flex-wrap gap-1">
            {HOLD_FILTERS.map(f => (
              <button key={f.key} onClick={() => setFilter(f.key)} className={chip(filter === f.key)}>
                {f.label} <span className="font-mono tabular-nums">{counts[f.key]}</span>
              </button>
            ))}
          </div>
        )}
        {!compact && sort.key && (
          <button onClick={() => setSort({ key: null, dir: 'desc' })}
                  className="digest-focusable ml-auto text-[11px] text-[var(--text-meta)] hover:text-[var(--text-primary)]">
            清除排序（回到依緊急度）
          </button>
        )}
      </div>

      {groups.length === 0 ? (
        <p className="text-xs text-[var(--text-meta)]">{compact ? '今日復盤沒有需要處理的項目。' : '這個帳戶／篩選下沒有持倉。'}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[var(--glass-border)]">
          <table className="focus-table w-full min-w-[74rem] table-fixed border-collapse text-[14px]">
            <colgroup>{HOLD_COLS.map(c => <col key={c.k} style={{ width: c.w }} />)}</colgroup>
            <thead>
              <tr className="bg-[var(--bg-tertiary)]">
                {HOLD_COLS.map(c => <SortTh key={c.k} col={c.k} sort={sort} onSort={compact ? null : onSort} />)}
              </tr>
            </thead>
            {groups.map(g => {
              const isFolded = !compact && folded.has(g.key);
              return (
                <tbody key={g.key}>
                  <HoldGroupHead group={g} items={g.items} folded={isFolded}
                                 onFold={!compact && g.dim ? () => toggleFold(g.key) : null} />
                  {!isFolded && g.items.map(r => (
                    <HoldingRow key={r.ticker} r={r} color={g.color} compact={compact}
                                showAccount={account === 'all'}
                                open={openTicker === r.ticker}
                                onToggle={() => setOpenTicker(t => (t === r.ticker ? null : r.ticker))} />
                  ))}
                </tbody>
              );
            })}
          </table>
        </div>
      )}
      {!compact && nFilled > 0 && (
        <p className="text-xs leading-relaxed text-[var(--text-meta)]">
          淡色的現價與距停損是依 {quoteDate || '最後'} 收盤價補算（報告未附，{nFilled} 檔），非即時報價；報告有寫的以報告為準。
        </p>
      )}
    </section>
  );
}

// ── 完整性橫幅：缺件必須寫出來，不能讓使用者從「少一個分頁」自己猜 ──────────
const COMPLETENESS_STYLE = {
  success: { Icon: CircleCheck, label: '完整',     color: 'var(--accent-success-text)', bg: 'rgba(16,185,129,0.10)' },
  partial: { Icon: CircleAlert, label: '部分完成', color: 'var(--accent-amber-text)',   bg: 'rgba(245,158,11,0.10)' },
  failed:  { Icon: CircleX,     label: '產出失敗', color: 'var(--accent-danger-text)',  bg: 'rgba(239,68,68,0.10)' },
};

function CompletenessBanner({ completeness }) {
  if (!completeness) return null;
  const { overall, missing_labels: missing = [], degraded_labels: degraded = [], has_manifest } = completeness;
  if (overall === 'success' && !missing.length && !degraded.length) return null;
  const st = COMPLETENESS_STYLE[overall] || COMPLETENESS_STYLE.partial;
  // 這是一則狀態註記，不是內容 —— 用細邊 + 一條左色標，別讓整條飽和色塊
  // 跟下面真正要讀的風險數字搶注意力。
  const chip = (text, key) => (
    <span key={key} className="rounded bg-[var(--bg-tertiary)] px-1.5 py-0.5 text-[var(--text-body)]">{text}</span>
  );
  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] py-2 pl-3 pr-3 text-[13px] leading-relaxed"
      style={{ borderLeft: `3px solid ${st.color}` }}
    >
      <st.Icon size={13} className="shrink-0" style={{ color: st.color }} aria-hidden="true" />
      <span className="font-semibold" style={{ color: st.color }}>今日產出{st.label}</span>
      {missing.length > 0 && (
        <span className="flex flex-wrap items-center gap-1 text-[var(--text-meta)]">缺少 {missing.map(chip)}</span>
      )}
      {degraded.length > 0 && (
        <span className="flex flex-wrap items-center gap-1 text-[var(--text-meta)]">降級 {degraded.map(chip)}</span>
      )}
      {!has_manifest && <span className="text-[var(--text-muted)]">（依實際檔案推算，完整性檢查尚未跑過）</span>}
    </div>
  );
}

// ── 「盤後復盤」一眼看重點 ─────────────────────────────────────────────
// 「今天需要處理」是 war_room_report.py 固定格式產出的兩行結構（不是模型自由
// 發揮），一筆長這樣：
//
//   1. **LLY**：建議**減碼**；目標倉位 4.0%；觸發：跌破1230已觸發；停損 $1100.0（前次減碼：已回應，待對帳）
//      - 距停損 $1150.0 僅 0.9%，最大風險 US$30
//
// 一份裡混了五種完全不同的操作：要出場的、要減碼的、要加倉的、要建倉的，
// 以及根本沒有建議、只是價格貼近停損的。原本全部倒進同一個清單，讀者得逐行
// 讀「建議」兩個字才知道這筆該做什麼 —— 依動作分開，才是照操作邏輯在讀。
//
// 另一條同樣重要但原本整個被丟掉的軸是執行狀態：
//   （前次X：已回應，待對帳）＝ 已經下過單，今天要做的是去對帳
//   ← 尚未確認執行              ＝ 還沒動作，今天要做的是真的去下單
// 這兩件事的動作完全不同，逐筆標出來。
const ACTION_BUCKET = {
  停損: 'stop', 出場: 'stop', 賣出: 'stop', 清倉: 'stop', 放空: 'stop',
  減碼: 'trim', 減倉: 'trim', 停利: 'trim',
  加倉: 'add', 加碼: 'add',
  建倉: 'open', 買進: 'open', 買入: 'open', 分批買進: 'open', 分批買入: 'open',
};

const num = (s) => (s == null ? null : Number(String(s).replace(/,/g, '')));

// 第一行：建議動作、目標倉位、觸發條件、停損價、執行狀態
function parseTodoHead(rest) {
  const action = /建議\*\*([^*]+)\*\*/.exec(rest)?.[1] || '';
  const target = num(/目標倉位\s*([\d.]+)\s*%/.exec(rest)?.[1]);
  const trigger = (/觸發：\s*([^；\u3000]+)/.exec(rest)?.[1] || '').replace(/\*\*/g, '').trim();
  const stopPx = /停損\s*\$([\d,.]+)/.exec(rest)?.[1] || '';
  // 「（前次停損：已回應，待對帳）」→ 待對帳；「← 尚未確認執行」→ 待執行
  const recon = /（前次[^：）]*：\s*([^）]+)）/.exec(rest)?.[1] || '';
  const todo = /←\s*(尚未確認執行)/.exec(rest)?.[1] || '';
  return {
    action, target, trigger, stopPx,
    exec: todo ? 'todo' : (/待對帳/.test(recon) ? 'recon' : ''),
    note: rest.replace(/\*\*/g, '').replace(/\u3000/g, ' ').trim(),
  };
}

// 第二行（可能沒有）：現價相對停損的位置、打到停損會賠多少
function parseTodoTail(text) {
  const below = /已在停損/.test(text);
  return {
    below,
    price: /現價\s*\$([\d,.]+)/.exec(text)?.[1] || '',
    dist: below ? 0 : num(/距停損\s*\$?[\d,.]+\s*僅\s*([\d.]+)\s*%/.exec(text)?.[1]),
    // 距離是對著這個價位算的。LLY 這種第一行寫「停損 $1100」、第二行卻寫
    // 「距停損 $1150 僅 0.9%」的，同一列要用後者，%數才跟價位對得起來。
    stopRef: (/(?:距停損|已在停損)\s*\$([\d,.]+)/.exec(text) || [])[1] || '',
    risk: num(/最大風險\s*(?:US)?\$?\s*([\d,]+(?:\.\d+)?)/.exec(text)?.[1]),
  };
}

// 先「已跌破停損」，再依距停損由近到遠，最後看金額 —— 這就是實際處理順序
function byUrgency(a, b) {
  if (a.below !== b.below) return a.below ? -1 : 1;
  const ad = a.dist ?? Infinity;
  const bd = b.dist ?? Infinity;
  if (ad !== bd) return ad - bd;
  return (b.risk ?? 0) - (a.risk ?? 0);
}

function parseMorningBrief(md) {
  if (!md) return null;
  const section = (heading) => {
    const m = md.match(new RegExp(`### ${heading}\\n([\\s\\S]*?)(?:\\n### |\\n---|\\n## |$)`));
    return m ? m[1] : '';
  };

  const buckets = { stop: [], breached: [], trim: [], add: [], open: [], watch: [] };
  let cur = null;
  const flush = () => { if (cur) buckets[cur.bucket].push(cur.item); cur = null; };
  for (const line of section('今天需要處理').split('\n')) {
    const head = /^\s*\d+\.\s+\*\*([A-Z0-9.-]+)\*\*：(.+)$/.exec(line);
    if (head) {
      flush();
      const [, ticker, rest] = head;
      const parsed = parseTodoHead(rest);
      // 沒有「建議**動作**」的是純風險提示（只寫了距停損多少），不是待辦動作。
      // 不能整行掃關鍵字 —— 「建議建倉…停損 $465」會被停損兩個字誤判成停損。
      // 沒有建議動作的那幾筆（16. **VRT**：距停損 $246.0 僅 3.9%，最大風險 US$160）
      // 把數字寫在第一行而不是續行，所以第一行也要跑一次 tail 的抽取；真的有續行
      // 時再由下面的 Object.assign 覆蓋掉。
      const item = { ticker, ...parseTodoTail(rest), ...parsed };
      // 報告有些日子整份都沒有寫「建議X」（產出降級時就會這樣）。那時不能把
      // 已經跌破停損的也丟進「接近停損」—— 那一區寫著「只是價格貼近」，而它們
      // 早就破了。不替報告發明建議，但要照價格狀態分開放。
      cur = {
        bucket: ACTION_BUCKET[parsed.action] || (item.below ? 'breached' : 'watch'),
        item,
      };
      continue;
    }
    const tail = /^\s+-\s+(.+)$/.exec(line);
    if (tail && cur) {
      Object.assign(cur.item, parseTodoTail(tail[1]));
      // 續行才讀到「已在停損之下」時，第一行當下分的組要跟著改
      if (!cur.item.action && cur.item.below) cur.bucket = 'breached';
    }
  }
  flush();
  Object.values(buckets).forEach((list) => list.sort(byUrgency));

  const riskBody = section('最大風險');

  // 逐檔 risk=high 的理由。多數代號在「今天需要處理」裡已經有一列了 —— 與其另開
  // 一塊重複列一次，不如把理由掛回那一列（原本那一列只有數字，沒有「為什麼」）。
  const reasons = new Map();
  const riskRe = /^- 🔴 ([A-Z0-9.-]+)（risk=high）：(.+)$/gm;
  let m;
  while ((m = riskRe.exec(riskBody))) reasons.set(m[1], m[2].trim());

  const seen = new Set();
  Object.values(buckets).forEach(list => list.forEach((it) => {
    seen.add(it.ticker);
    if (reasons.has(it.ticker)) it.why = reasons.get(it.ticker);
  }));
  // 剩下的（沒進今天待辦但被標 risk=high）才自成一塊，不會跟上面重複
  const highRisk = [...reasons.entries()]
    .filter(([ticker]) => !seen.has(ticker))
    .map(([ticker, why]) => ({ ticker, why, below: false, dist: null, risk: null, exec: '' }));

  // 整份報告最重要的一個數字，原本只是「最大風險」段落裡的最後一顆項目符號
  const tot = /全部停損同時觸發：約\s*\*{0,2}US\$([\d,]+(?:\.\d+)?)（組合\s*([\d.]+)%）/.exec(riskBody);
  const totalRisk = tot ? { usd: num(tot[1]), pct: Number(tot[2]) } : null;

  // 曝險過線的主題警示（🟡 那一行），跟逐檔 risk=high 是不同層級的東西
  const ex = /^- 🟡 (.+?) 曝險 ([\d.]+)%（(\d+) 檔）已過 ([\d.]+)% 警戒線/m.exec(riskBody);
  const exposure = ex ? { theme: ex[1], pct: Number(ex[2]), count: Number(ex[3]), limit: Number(ex[4]) } : null;

  // 執行狀態的檔數直接從上面的待辦算 —— 「等待條件」那一段同一檔會重複列 3-4 次，
  // 而且含不在今天待辦裡的代號，拿它當計數會跟畫面上的列數對不起來。
  const all = Object.values(buckets).flat();
  const execCount = {
    todo: all.filter(it => it.exec === 'todo').length,
    recon: all.filter(it => it.exec === 'recon').length,
  };

  return { ...buckets, highRisk, totalRisk, exposure, execCount };
}

const usd = (n) => `US$${Math.round(n).toLocaleString()}`;

// 執行狀態：待執行＝還沒下單，待對帳＝下過單要去確認成交。兩者今天要做的事不同。
const EXEC = {
  todo:  { label: '待執行', tone: AMBER_TEXT, title: '尚未確認執行 —— 今天要真的去下單' },
  recon: { label: '待對帳', tone: 'var(--text-meta)', title: '已回應，待對帳 —— 今天要去確認是否成交' },
};

// ── 「機會掃描」一眼看重點：market_scan.py `_render_note()` 每檔固定寫三行
// （**action**｜方向｜信心｜score｜RR → 進場｜目標｜停損 → 論點），逐檔切開後
// 轉成一條一檔的清單，取代只能整段讀 markdown 的體驗。
const OPP_ACTION_COLOR = { buy: GREEN, accumulate: GREEN, watch: AMBER, avoid: 'var(--text-muted)', sell_short: RED };
const OPP_ACTION_LABEL = { buy: '買入', accumulate: '加碼', watch: '觀察', avoid: '避開', sell_short: '放空' };
// 缺值或不認得的動作一律併入「觀察」這類，不再各自顯示原始英文字串.
const oppActionKey = (action) => (action && OPP_ACTION_LABEL[action] ? action : 'watch');
// 清單按「今天能不能動手」排：買入 → 加碼 → 放空 → 觀察 → 避開。
const OPP_ACTION_ORDER = { buy: 0, accumulate: 1, sell_short: 2, watch: 3, avoid: 4 };
const oppActionRank = (action) => OPP_ACTION_ORDER[oppActionKey(action)];

// 報告全文的「## 機會清單」是按 score 寫的，這裡重排成跟摘要同一個動作順序。
// 只動這一段的 ### 區塊，前後段落與段內文字原封不動；同動作內維持原本的排名。
function sortScanOpportunities(md) {
  const HEAD = '## 機會清單';
  const idx = String(md || '').indexOf(HEAD);
  if (idx < 0) return md;
  const after = md.slice(idx);
  // 機會清單後面還有別的段落（附錄、驗證明細…）—— 只切到下一個 # / ## 為止
  const endRel = after.slice(HEAD.length).search(/\n#{1,2} /);
  const section = endRel < 0 ? after : after.slice(0, HEAD.length + endRel);
  const tail = endRel < 0 ? '' : after.slice(HEAD.length + endRel);
  const parts = section.split(/\n(?=### )/);   // [0] 是標題＋前言，其餘一檔一塊
  if (parts.length < 3) return md;
  const rankOf = (chunk) => oppActionRank(/^-\s+\*\*([^*]+)\*\*/m.exec(chunk)?.[1]?.trim().toLowerCase());
  const sorted = parts.slice(1).sort((a, b) => rankOf(a) - rankOf(b));
  return md.slice(0, idx) + [parts[0], ...sorted].join('\n') + tail;
}

// 機會掃描開頭的兩塊引言（Stage-1 universe 統計、「本頁是研究候選」聲明）天天一模
// 一樣，佔掉整個第一屏。產生器已經不寫了，歷史報告在這裡一併拿掉。
function stripScanPreamble(md) {
  const lines = String(md || '').split('\n');
  let i = 0;
  while (i < lines.length && (!lines[i].trim() || /^>\s?/.test(lines[i]))) i++;
  return i === 0 ? md : lines.slice(i).join('\n');
}

// 產生器把 setup 有時寫成 key、有時寫成中文；表格裡統一成中文。
const SETUP_LABEL = {
  momentum_breakout: '動能突破',
  undervalued_catalyst: '低估+催化',
  oversold_bounce: '超賣反彈',
  breakdown_short: '破底放空',
  mimi_entry: 'Mimi 進場',
};
// 「🎯 今日 top 機會：DELL｜動能突破｜accumulate｜理由…｜AMD｜…」——五檔串成一段，
// 要一路讀到底才知道有幾檔、哪一檔是什麼。四欄一組拆成表格；欄位對不上就原樣不動。
const TICKERISH = /^[A-Z][A-Z0-9.-]{0,6}(（[^）]*）|\([^)]*\))?$/;

function tableizeScanHighlights(md) {
  const HEAD = '## 精華';
  const idx = String(md || '').indexOf(HEAD);
  if (idx < 0) return md;
  const after = md.slice(idx);
  const endRel = after.slice(HEAD.length).search(/\n#{1,2} /);
  const section = endRel < 0 ? after : after.slice(0, HEAD.length + endRel);
  const tail = endRel < 0 ? '' : after.slice(HEAD.length + endRel);

  const lines = section.split('\n');
  const start = lines.findIndex(l => l.trimStart().startsWith('🎯'));
  if (start < 0) return md;
  let end = start + 1;                       // 機會可能寫成一行，也可能一檔一行
  while (end < lines.length && !lines[end].trimStart().startsWith('⚠️')) end++;

  const body = lines.slice(start, end).join('\n').replace(/^🎯\s*[^：:]*[：:]\s*/, '');
  const rows = [];
  for (const line of body.split('\n')) {
    const cells = line.split('｜').map(c => c.trim()).filter(Boolean);
    if (!cells.length) continue;
    if (cells.length < 4 || cells.length % 4 !== 0) return md;   // 不是四欄一組 → 別亂拆
    for (let i = 0; i < cells.length; i += 4) rows.push(cells.slice(i, i + 4));
  }
  // 代號與動作都要站得住腳，不然寧可留原文（產生器偶爾寫成純散文）
  const ok = rows.length >= 2 && rows.every(
    r => TICKERISH.test(r[0]) && OPP_ACTION_LABEL[r[2].toLowerCase()],
  );
  if (!ok) return md;

  const cell = (s) => s.replace(/\|/g, '\\|').replace(/。$/, '');
  const table = [
    '| 代號 | 型態 | 動作 | 為什麼 |',
    '| --- | --- | --- | --- |',
    ...rows.map(([t, setup, action, why]) =>
      `| **${cell(t)}** | ${cell(SETUP_LABEL[setup] || setup)} | **${action.toLowerCase()}** | ${cell(why)} |`),
  ].join('\n');

  const rebuilt = [...lines.slice(0, start), '', table, '', ...lines.slice(end)].join('\n');
  return md.slice(0, idx) + rebuilt + tail;
}

// 「催化」欄位是用「；」把 2-3 個獨立事件串成一段（例：財報 beat；併購挹注；
// 投行上調目標價），論點偶爾也是。串在一起要整段讀完才知道有幾件事 —— 拆成一條一件。
const splitPoints = (text) =>
  String(text || '').split(/[；;]/).map(x => x.trim()).filter(Boolean);

// 「- 來源：[Reuters 8/27](https://…)；[Citi 8/28](https://…)」→ 與催化同序的來源陣列。
// 舊報告沒有這一行，回空陣列，畫面就跟以前一樣（不會憑空生出來源）。
const SOURCE_RE = /^\[([^\]]+)\]\(([^)]*)\)$/;
const parseSources = (text) =>
  splitPoints(text).map((raw) => {
    const m = SOURCE_RE.exec(raw);
    return m ? { label: m[1].trim(), url: m[2].trim() } : { label: raw, url: '' };
  });

// 報告裡價位是字串（"$265.0-275.0"、"255.0"）。要比對現價得先變回數字。
const parseNum = (t) => {
  const m = /-?\d+(?:\.\d+)?/.exec(String(t || '').replace(/,/g, ''));
  return m ? Number(m[0]) : null;
};
const parseRange = (t) => {
  const nums = String(t || '').replace(/,/g, '').match(/\d+(?:\.\d+)?/g);
  if (!nums?.length) return null;
  const lo = Number(nums[0]);
  const hi = nums.length > 1 ? Number(nums[1]) : lo;
  return [Math.min(lo, hi), Math.max(lo, hi)];
};

// 「進場 $265.0-275.0」單看不知道現在能不能買 —— 要跟現價比才有意義。
// 做空的停損在上方，判斷方向相反。
function entryStatus(o, price) {
  if (price == null || !o.entryRange) return null;
  const short = o.direction === 'short';
  const [lo, hi] = o.entryRange;
  if (o.stopNum != null && (short ? price >= o.stopNum : price <= o.stopNum)) {
    return { label: '已破停損', color: RED };
  }
  if (price >= lo && price <= hi) return { label: '在進場區', color: GREEN };
  const ref = price < lo ? lo : hi;
  const pct = Math.abs((price - ref) / ref) * 100;
  const below = price < lo;
  return {
    label: `${below ? '低於' : '高於'}進場區 ${pct.toFixed(1)}%`,
    // 做多時低於進場區是「更便宜」，做空時相反
    color: below === !short ? CYAN : AMBER,
  };
}

function parseOpportunities(md) {
  if (!md) return [];
  const idx = md.indexOf('## 機會清單');
  if (idx < 0) return [];
  const chunks = md.slice(idx).split(/\n(?=### )/).slice(1);
  const out = [];
  for (const chunk of chunks) {
    const head = chunk.match(/^### (🆕 )?([A-Z0-9.-]+)（([^）]*)）— (.+)$/m);
    if (!head) continue;
    const line1 = chunk.match(/^-\s+\*\*([^*]+)\*\*｜方向\s*([^｜]*)｜信心\s*([^｜]*)｜score\s*([^｜]*)｜RR\s*(.+)$/m);
    const line2 = chunk.match(/^-\s+進場\s*([^｜]*)｜目標\s*([^｜]*)｜停損\s*(.+)$/m);
    const line3 = chunk.match(/^-\s+論點：(.+)$/m);
    const line4 = chunk.match(/^-\s+催化：(.+)$/m);
    const line5 = chunk.match(/^-\s+來源：(.+)$/m);
    out.push({
      ticker: head[2], company: head[3], setup: head[4], isNew: !!head[1],
      action: (line1?.[1] || '').trim().toLowerCase(),
      direction: (line1?.[2] || 'long').trim().toLowerCase(),
      conviction: (line1?.[3] || '—').trim(),
      rr: (line1?.[5] || '—').trim(),
      entry: (line2?.[1] || '—').trim(),
      target: (line2?.[2] || '—').trim(),
      stop: (line2?.[3] || '—').trim(),
      entryRange: parseRange(line2?.[1]),
      stopNum: parseNum(line2?.[3]),
      thesis: (line3?.[1] || '').trim(),
      catalyst: (line4?.[1] || '').trim(),
      sources: parseSources(line5?.[1] || ''),
    });
  }
  return out;
}

// 每則催化後面掛它的出處。沒有 url 的（本地 API 推導）只顯示 label，不做成連結。
function SourceTag({ src, bare = false }) {
  if (!src?.label) return null;
  const cls = bare ? '' : 'ml-1.5';
  if (!src.url) {
    return <span className={`${cls} text-[10px] text-[var(--text-muted)] opacity-70`}>· {src.label}</span>;
  }
  return (
    <a
      href={src.url}
      target="_blank"
      rel="noreferrer"
      className={`${cls} inline-flex items-center gap-0.5 text-[10px] font-medium text-[var(--accent-cyan)] hover:underline`}
    >
      {src.label}<ExternalLink size={9} />
    </a>
  );
}

// 「進場 $265-275」單看不知道現在能不能買。一次批次抓完（/prices 是單一 yfinance
// bulk call + 2 分鐘快取），抓不到就不顯示現價，其餘照常。總覽與機會清單共用。
function useQuotes(items) {
  const [quotes, setQuotes] = useState({});
  const [quoteErr, setQuoteErr] = useState(false);
  const tickers = useMemo(
    () => [...new Set((items || []).map(o => o.ticker).filter(Boolean))].join(','),
    [items],
  );
  useEffect(() => {
    if (!tickers) return;
    let alive = true;
    axios.get('/api/stocks/prices', { params: { tickers } })
      .then(r => { if (alive) { setQuotes(r.data?.prices || {}); setQuoteErr(false); } })
      .catch(() => { if (alive) setQuoteErr(true); });
    return () => { alive = false; };
  }, [tickers]);
  return { quotes, quoteErr, loaded: Object.keys(quotes).length > 0 };
}

/**
 * @param {boolean} onlyInZone  只留現價已進入進場區的（總覽的「今天就能執行」用）
 * @param {string[]} heldTickers  目前持倉代號。這份清單是陌生開發（掃全市場找新標的），
 *   偶爾會掃到手上已經有的部位 —— 那不是新機會，是加減碼決策，要一眼認得出來。
 */
function OpportunityList({ items, onlyInZone = false, heldTickers }) {
  const held = useMemo(() => new Set(heldTickers || []), [heldTickers]);
  const { quotes, quoteErr, loaded } = useQuotes(items);

  if (!items?.length) return null;
  if (onlyInZone && !loaded) {
    return <p className="text-xs text-[var(--text-meta)]">
      {quoteErr ? '現價載入失敗，無法判斷哪幾檔已進入進場區。' : '正在比對現價與進場區…'}
    </p>;
  }
  // 報告本身是按 score 排的，讀的時候要的卻是「今天能不能動手」——按動作分群，
  // 同動作內維持報告原本的排名順序（sort 是穩定的）。
  const shown = (onlyInZone
    ? items.filter(o => entryStatus(o, quotes[o.ticker]?.price ?? null)?.label === '在進場區')
    : [...items]
  ).sort((a, b) => oppActionRank(a.action) - oppActionRank(b.action));
  if (onlyInZone && !shown.length) {
    return <p className="text-xs text-[var(--text-meta)]">今日 {items.length} 檔候選都還沒進入進場區。</p>;
  }

  // 報價是「最後收盤」不是即時，日期要寫出來，不然使用者會以為是現在的價
  const quoteDate = Object.values(quotes).map(q => q?.last_date).find(Boolean);
  const hasAnySource = items.some(o => o.sources?.length);

  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-sm font-semibold text-[var(--text-primary)]">
        <Target size={14} className="text-[var(--accent-cyan)]" aria-hidden="true" />
        {onlyInZone ? '今天就能執行' : '入圍機會'}{' '}
        <span className="text-xs font-normal text-[var(--text-meta)]">
          {onlyInZone ? `— ${shown.length}/${items.length} 檔現價已進入進場區` : `— ${items.length} 檔，一眼看重點`}
        </span>
      </p>
      {/* 現價是收盤價不是即時；來源要嘛有、要嘛講明為什麼沒有，不能靜靜留白 */}
      <p className="text-xs leading-relaxed text-[var(--text-meta)]">
        {quoteErr
          ? '現價載入失敗，以下只有報告當時的價位。'
          : quoteDate
            ? `現價為 ${quoteDate} 收盤，非即時報價。`
            : '現價載入中…'}
        {!hasAnySource && '　·　這份報告產出時尚未要求引用出處，來源連結從下一輪掃描開始才有。'}
      </p>
      {/* 條列而非一檔一張卡：20 檔卡片在手機上要捲四千多 px，而且每張卡的框線
          反而蓋掉「由上往下掃」的節奏。左緣色條讓 buy／watch／avoid 用餘光就分得出來。 */}
      <ol className="overflow-hidden rounded-xl border border-[var(--glass-border)] divide-y divide-[var(--glass-border)]">
        {shown.map((o, i) => {
          const actionKey = oppActionKey(o.action);
          const color = OPP_ACTION_COLOR[actionKey];
          const catalysts = splitPoints(o.catalyst);
          const orphanSources = (o.sources || []).slice(catalysts.length);
          const q = quotes[o.ticker];
          const status = entryStatus(o, q?.price ?? null);
          return (
            <li
              key={o.ticker}
              className="px-3 py-2.5 transition-colors hover:bg-[var(--bg-secondary)]"
              style={{ borderLeft: `3px solid ${color}` }}
            >
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="w-4 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--text-meta)]">{i + 1}</span>
                <Link href={`/stock/${o.ticker}`} className="font-mono text-[15px] font-extrabold text-[var(--text-primary)] hover:opacity-70">
                  {o.ticker}
                </Link>
                {o.isNew && (
                  <span className="rounded px-1 py-0.5 text-[10px] font-bold"
                        style={{ color: 'var(--accent-purple)', background: 'rgba(87,139,250,0.14)' }}>
                    🆕 首次
                  </span>
                )}
                {held.has(o.ticker) && (
                  <span className="rounded px-1 py-0.5 text-[10px] font-bold"
                        style={{ color: CYAN, background: tint(CYAN, 14) }}
                        title="這檔已經在持倉裡，不是陌生開發的新標的">
                    持有中
                  </span>
                )}
                {o.company && <span className="text-xs text-[var(--text-meta)]">{o.company}</span>}
                {/* 寬螢幕標題列右邊本來空著八百多 px，數字併上去可以少掉 20 行 */}
                <div className="order-last mt-1 flex w-full flex-wrap gap-x-3 gap-y-0.5 pl-6 text-xs tabular-nums text-[var(--text-secondary)] sm:order-none sm:mt-0 sm:ml-2 sm:w-auto sm:pl-0">
                  {q?.price != null && (
                    <span title={q.last_date ? `${q.last_date} 收盤` : undefined}>
                      現價 <b className="text-[var(--text-primary)]">{q.price.toFixed(2)}</b>
                      {q.change_percent != null && (
                        <b className="ml-1" style={{ color: q.change_percent >= 0 ? GREEN : RED }}>
                          {q.change_percent >= 0 ? '+' : ''}{q.change_percent.toFixed(2)}%
                        </b>
                      )}
                    </span>
                  )}
                  <span>進場 <b style={{ color: CYAN }}>{o.entry}</b></span>
                  {status && (
                    <span className="rounded px-1.5 font-semibold"
                          style={{ color: status.color, background: tint(status.color, 10) }}>
                      {status.label}
                    </span>
                  )}
                  <span>目標 <b style={{ color: GREEN }}>{o.target}</b></span>
                  <span>停損 <b style={{ color: RED }}>{o.stop}</b></span>
                  <span>RR <b className="text-[var(--text-primary)]">{o.rr}</b></span>
                </div>
                <span className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
                      style={{ color, background: tint(color, 10) }}>
                  {OPP_ACTION_LABEL[actionKey]}
                </span>
              </div>

              {/* 論點在上（正文級）、催化在下（琥珀點＋小字），靠顏色與字級分群，
                  不用再多一行寫「催化：」，也不用重複的 emoji 當標記。 */}
              {splitPoints(o.thesis).length > 0 && (
                <ul className="mt-1 space-y-0.5 pl-6">
                  {splitPoints(o.thesis).map((t, k) => (
                    <li key={k} className="flex gap-1.5 text-[13px] leading-relaxed text-[var(--text-primary)]">
                      <span className="shrink-0 text-[var(--text-meta)]" aria-hidden="true">•</span>
                      <span className="max-w-[76ch]">{t}</span>
                    </li>
                  ))}
                </ul>
              )}
              {(catalysts.length > 0 || orphanSources.length > 0) && (
                <ul className="mt-1 space-y-0.5 pl-6">
                  {catalysts.map((c, k) => (
                    <li key={k} className="flex gap-1.5 text-xs leading-relaxed text-[var(--text-meta)]">
                      <span className="shrink-0" style={{ color: AMBER_TEXT }}>•</span>
                      <span className="max-w-[76ch]">
                        {c}
                        <SourceTag src={o.sources?.[k]} />
                      </span>
                    </li>
                  ))}
                  {/* 來源比催化多的（或根本沒催化）不能吞掉 —— 單獨列出來 */}
                  {orphanSources.map((src, k) => (
                    <li key={`s${k}`} className="flex gap-1.5 text-xs leading-relaxed text-[var(--text-meta)]">
                      <span className="shrink-0" style={{ color: AMBER_TEXT }}>•</span>
                      <SourceTag src={src} bare />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── 分頁內容：總覽（一天整合版）─────────────────────────────────────────
// 三條 pipeline 各自獨立，原本要點 3 個分頁 + 4 個子分頁才看得完一天。
// 這裡不是把七塊接起來（那是兩萬多字的牆），而是各取「結論」按決策順序排：
// 今天要動什麼 → 組合姿態 → 今天就能執行的新機會 → 必讀新聞。深入看再跳分頁。
// 序號用實心徽章而不是 ①②③④ —— 圓圈數字是字型相依的字元，跨平台粗細不一，
// 也吃不到設計 token。徽章可控大小、顏色與對齊。
function OverviewSection({ step, title, source, onJump, jumpLabel, children }) {
  return (
    <section className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-[var(--glass-border)] pb-2">
        <span
          aria-hidden="true"
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-[var(--accent-cyan)] font-mono text-[11px] font-bold text-white"
        >
          {step}
        </span>
        <h3 className="text-base font-semibold tracking-[-0.01em] text-[var(--text-primary)]">{title}</h3>
        <span className="text-xs text-[var(--text-meta)]">來源：{source}</span>
        {onJump && (
          <button
            onClick={onJump}
            className="tap-44 digest-focusable ml-auto inline-flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-[var(--accent-cyan-text)] transition-colors duration-150 hover:bg-[var(--bg-tertiary)]"
          >
            {jumpLabel}<ArrowRight size={13} aria-hidden="true" />
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

function MissingNote({ label }) {
  return (
    <p className="flex items-start gap-1.5 text-xs leading-relaxed text-[var(--text-meta)]">
      <Info size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>這天沒有「{label}」這份報告 —— 可能是當天沒跑或產出失敗，不是沒有事情發生。</span>
    </p>
  );
}

function OverviewBody({ data, onJump, weights }) {
  const { review, market, notes } = data;

  const warroomMd = review?.report_md || '';
  const scanMd = market?.sections?.find(s => s.key === 'scan')?.content || '';
  const focus = useMemo(() => parseMorningBrief(warroomMd), [warroomMd]);
  const opportunities = useMemo(() => parseOpportunities(scanMd), [scanMd]);
  // 總覽用同一張逐檔表的精簡版（只列要動的那幾組），跟持倉復盤分頁是同一份資料、
  // 同一種排版 —— 不再另外做一份「優先處理」清單
  const base = useMemo(
    () => mergeDecisionsWithCards(review?.journal?.decisions, review?.ai_cards?.cards, review?.ai_cards?.holdings, weights),
    [review, weights],
  );
  const { quotes } = useQuotes(base);
  const rows = useMemo(
    () => buildHoldings({ journal: review?.journal, cards: review?.ai_cards, md: warroomMd, focus,
                          ledger: review?.ledger, weights, quotes }),
    [review, warroomMd, focus, weights, quotes],
  );
  const reviewRan = Boolean(review?.journal || review?.report_md);
  const heldTickers = useMemo(() => Object.keys(weights || {}), [weights]);

  // 新聞只挑有方向的（利多／利空）；中性的留給完整分頁
  const newsItems = (notes?.items || []).filter(it => it.impact === '利多' || it.impact === '利空');

  const nothing = !review && !market && !notes;
  if (nothing) {
    return <p className="py-6 text-center text-sm text-[var(--text-meta)]">這天三份日報都沒有。</p>;
  }

  return (
    <div className="space-y-5">
      <CompletenessBanner completeness={market?.completeness} />

      <OverviewSection step={1} title="今天要處理" source="持倉復盤"
                       onJump={review ? () => onJump('review') : null} jumpLabel="看完整復盤">
        {!review ? <MissingNote label="持倉復盤" />
          : (
            <div className="space-y-3">
              <ReviewStats journal={review.journal} cards={review.ai_cards} focus={focus} rows={rows} />
              {reviewRan
                ? <HoldingsTable rows={rows} account="all" compact />
                : <p className="text-xs text-[var(--text-meta)]">這天 War Room 復盤沒有產出，只有 05:00 的 AI 卡 —— 點「看完整復盤」可讀。</p>}
            </div>
          )}
      </OverviewSection>

      <OverviewSection step={2} title="今天就能執行的新機會" source="機會掃描"
                       onJump={market ? () => onJump('market', 'scan') : null} jumpLabel="看全部候選">
        {!market ? <MissingNote label="市場個股 · 機會掃描" />
          : opportunities.length
            ? <OpportunityList items={opportunities} onlyInZone heldTickers={heldTickers} />
            : <p className="text-xs text-[var(--text-meta)]">這天沒有機會掃描結果。</p>}
      </OverviewSection>

      <OverviewSection step={3} title="今日主線與必讀新聞" source="市場新聞"
                       onJump={notes ? () => onJump('notes') : null} jumpLabel="看全部新聞">
        {!notes ? <MissingNote label="市場新聞" /> : (
          <div className="space-y-2">
            {notes.market_summary && (
              <ul className="space-y-1">
                {splitSentences(notes.market_summary).map((t, k) => (
                  <li key={k} className="flex gap-1.5 text-sm leading-relaxed text-[var(--text-body)]">
                    <span className="shrink-0 text-[var(--accent-cyan-text)]" aria-hidden="true">•</span>
                    <span className="max-w-[76ch]">{t}</span>
                  </li>
                ))}
              </ul>
            )}
            {newsItems.length > 0 ? (
              <ol className="overflow-hidden rounded-xl border border-[var(--glass-border)] divide-y divide-[var(--glass-border)]">
                {newsItems.map((item, i) => <NewsRow key={i} item={item} index={i + 1} />)}
              </ol>
            ) : (
              <p className="text-xs text-[var(--text-meta)]">
                {/* 策展沒跑時 impact 是空的，不是「中性」—— 那是沒判過，不能寫成判過了 */}
                {notes.curated === false
                  ? `這天 AI 策展沒跑，${(notes.items || []).length} 則都是未整理的原始頭條，沒有影響判定 —— 點「看全部新聞」可讀。`
                  : `今日 ${(notes.items || []).length} 則新聞都判為中性，沒有明確利多／利空。`}
              </p>
            )}
          </div>
        )}
      </OverviewSection>
    </div>
  );
}

// ── 報告標頭：把散在四處的同一件事收成一條 ─────────────────────────────
// 每一份報告的開頭都是同一組東西：`# 🎖️ 盤後復盤（戰情中心）— 2026-09-01`、
// 一塊 ```text 的交易日／資料截至／產出時間／狀態、再一段 `> 產出驗證`。
// 但標題與日期在子分頁名和日期選擇器上已經各寫過一次，狀態在完整性橫幅裡又寫
// 一次，產出時間在面板頂端「產生於」再寫一次 —— 同一件事四個地方，這就是版面
// 讀起來亂的來源。這裡把它們抽出來合成一列，正文則從真正的內容開始。
// 認不出格式就整份原樣退回，不刪任何東西。
const META_LINE = /^([^：:]{1,12})[：:]\s*(.+?)\s*$/;

function splitFrontMatter(md) {
  const src = String(md || '');
  const lines = src.split('\n');
  let i = 0;
  const skipBlank = () => { while (i < lines.length && !lines[i].trim()) i++; };

  // 機會掃描帶 Obsidian YAML frontmatter（Markdown 的 clean() 也會清掉，這裡只是跳過）
  if (lines[0]?.trim() === '---') {
    const end = lines.indexOf('---', 1);
    if (end > 0) i = end + 1;
  }
  skipBlank();

  const h1 = /^#\s+(.+?)\s*$/.exec(lines[i] || '');
  if (!h1) return { front: null, body: src };
  const title = h1[1].replace(/[*`]/g, '').trim();
  i++;
  skipBlank();

  // ```text 區塊。整塊都是 `key：value` 才當成 meta —— 混了散文就原封不動留在正文。
  const meta = [];
  if (/^```/.test(lines[i] || '')) {
    const close = lines.indexOf('```', i + 1);
    if (close > i) {
      const raw = lines.slice(i + 1, close).filter(l => l.trim());
      const rows = raw.map(l => META_LINE.exec(l.replace(/\u3000/g, ' ').trim())).filter(Boolean);
      if (rows.length && rows.length === raw.length) {
        rows.forEach(m => meta.push({ k: m[1].replace(/\s+/g, ''), v: m[2] }));
        i = close + 1;
      }
    }
  }
  skipBlank();

  // 開頭的引言只收「產出驗證」這種例行的產出註記。機會掃描開頭的
  // 「本頁是研究候選，不是交易指令」是讀者必須看到的警告 —— 留在正文當醒目引言，
  // 不要被折進「明細」裡。
  const notes = [];
  while (/^>\s?/.test(lines[i] || '')) {
    const start = i;
    const buf = [];
    while (/^>\s?/.test(lines[i] || '')) { buf.push(lines[i].replace(/^>\s?/, '').trim()); i++; }
    const text = buf.join(' ').trim();
    if (!/產出驗證/.test(text)) { i = start; break; }
    notes.push(text);
    skipBlank();
  }

  return { front: { title, meta, notes }, body: lines.slice(i).join('\n').trim() };
}

// 摘要列只放「夠不夠新、可不可信」，完整的原值留給展開的明細。
const metaVal = (meta, key) => meta.find(m => m.k === key)?.v || '';
const shortDate = (s) => s.replace(/^\d{4}-/, '').replace(/-/g, '/');

function summaryChips(front) {
  if (!front) return [];
  const { meta, notes } = front;
  const chips = [];

  // 「2026-08-31 16:00 ET（收盤） （2026-09-01 04:00 台北）」→「08/31 16:00 ET 收盤」。
  // 只留到第一個右括號為止 —— 後面的台北時間是同一個時刻的第二種寫法，留在明細。
  const asOf = (/^[^）]*）|^\S+ \S+/.exec(metaVal(meta, '資料截至')) || [''])[0].trim();
  if (asOf) chips.push({ k: '資料', v: shortDate(asOf).replace(/[（）]/g, ' ').replace(/\s+/g, ' ').trim() });
  else if (metaVal(meta, '交易日')) chips.push({ k: '交易日', v: shortDate(metaVal(meta, '交易日')) });

  // 「LIVE／即時（收盤後產出）（收盤後 1.1 小時）」→「LIVE／即時」
  const state = metaVal(meta, '狀態').split('（')[0].trim();
  if (state) chips.push({ k: '狀態', v: state });

  // 產出驗證通過是例行結果，放明細就好；有異動才要在摘要列看得到。
  const verify = notes.find(n => /產出驗證/.test(n));
  if (verify && !/^✅/.test(verify)) chips.push({ k: '', v: '產出驗證有異動', tone: AMBER_TEXT });

  return chips;
}

// 完整性：摘要只寫「缺 N／降 N」，名單留在明細裡 —— 六個檔名橫著攤開就是一條噪音。
function ReportIdentity({ front, label, completeness, sections, activeKey, onPick }) {
  const [detail, setDetail] = useState(false);
  const chips = useMemo(() => summaryChips(front), [front]);
  const miss = completeness?.missing_labels || [];
  const deg = completeness?.degraded_labels || [];
  const st = COMPLETENESS_STYLE[completeness?.overall] || COMPLETENESS_STYLE.partial;
  const flawed = completeness && completeness.overall !== 'success' && (miss.length || deg.length);
  const hasDetail = Boolean(front?.meta?.length || front?.notes?.length || flawed);

  return (
    <div className="border-b border-[var(--glass-border)] pb-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {/* 子分頁的 lg:-mb-3 是把底線壓到整條帶的下緣，只有跟右邊的狀態字同一列時
            才成立；窄螢幕會換行，那時底線會落在下一行的字上 —— 改成自己帶一條。 */}
        {sections.length > 1 ? (
          <div className="flex w-full flex-wrap border-b border-[var(--glass-border)] lg:-mb-3 lg:w-auto lg:border-b-0">
            {sections.map(s => (
              <button
                key={s.key}
                onClick={() => onPick(s.key)}
                aria-current={s.key === activeKey ? 'page' : undefined}
                className={`digest-focusable -mb-px border-b-2 px-3 pb-2.5 pt-1 text-[14.5px] font-semibold transition-colors ${
                  s.key === activeKey
                    ? 'border-[var(--accent-cyan)] text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-meta)] hover:text-[var(--text-primary)]'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[13px] font-semibold text-[var(--text-primary)]">{front?.title || label}</p>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-[var(--text-meta)]">
          {chips.map((c, n) => (
            <span key={n} className="inline-flex items-baseline gap-1.5">
              {c.k && <span className="text-[var(--text-muted)]">{c.k}</span>}
              <span className="font-mono" style={c.tone ? { color: c.tone, fontWeight: 600 } : undefined}>
                {c.v}
              </span>
            </span>
          ))}
          {flawed && (
            <span className="inline-flex items-baseline gap-1.5">
              <span className="text-[var(--text-muted)]">今日產出</span>
              <span className="inline-flex items-center gap-1 font-semibold" style={{ color: st.color }}>
                <st.Icon size={12} className="translate-y-0.5" aria-hidden="true" />
                缺 {miss.length}／降級 {deg.length}
              </span>
            </span>
          )}
          {hasDetail && (
            <button
              onClick={() => setDetail(v => !v)}
              aria-expanded={detail}
              className="digest-focusable inline-flex items-center gap-0.5 rounded px-1 text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
            >
              明細 {detail ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>
          )}
        </div>
      </div>

      {detail && (
        <dl className="mt-2.5 grid gap-x-6 gap-y-1 rounded-lg bg-[var(--bg-secondary)] px-3 py-2.5 text-[13px] [grid-template-columns:repeat(auto-fill,minmax(min(100%,20rem),1fr))]">
          {front?.title && (
            <div className="flex gap-2">
              <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">報告</dt>
              <dd className="min-w-0 text-[var(--text-body)]">{front.title}</dd>
            </div>
          )}
          {(front?.meta || []).map((m, n) => (
            <div key={n} className="flex gap-2">
              <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">{m.k}</dt>
              <dd className="min-w-0 font-mono text-[var(--text-body)]">{m.v}</dd>
            </div>
          ))}
          {miss.length > 0 && (
            <div className="flex gap-2">
              <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">缺少</dt>
              <dd className="min-w-0 text-[var(--text-body)]">{miss.join('、')}</dd>
            </div>
          )}
          {deg.length > 0 && (
            <div className="flex gap-2">
              <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">降級</dt>
              <dd className="min-w-0 text-[var(--text-body)]">{deg.join('、')}</dd>
            </div>
          )}
          {completeness && !completeness.has_manifest && (
            <div className="flex gap-2">
              <dt className="w-[4.5rem] shrink-0 text-[var(--text-muted)]">註</dt>
              <dd className="min-w-0 text-[var(--text-muted)]">依實際檔案推算，完整性檢查尚未跑過</dd>
            </div>
          )}
          {(front?.notes || []).map((n, k) => (
            <p key={`n${k}`} className="col-span-full leading-relaxed text-[var(--text-body)]">{n}</p>
          ))}
        </dl>
      )}
    </div>
  );
}

// ── 分頁內容：市場個股（長文，給自己的捲動區） ───────────────────────────
function MarketDigestBody({ report, tickers, initialSection }) {
  // 盤後復盤有自己的分頁了（持倉復盤），這裡不再重複列一次同一份文件
  const sections = useMemo(() => (report?.sections || []).filter(s => s.key !== 'warroom'), [report]);
  // 從總覽跳過來時停在指定的子分頁（看完整復盤／看全部候選）
  const [key, setKey] = useState(
    () => (sections.some(s => s.key === initialSection) ? initialSection : sections[0]?.key) || '',
  );
  const current = sections.find(s => s.key === key) || sections[0];

  // 換日期後 section 可能不存在，退回第一個
  useEffect(() => {
    if (sections.length && !sections.some(s => s.key === key)) setKey(sections[0].key);
  }, [sections, key]);

  // 標頭（標題／meta 區塊／開頭引言）抽出來給上面的狀態列，正文只留真正的內容
  const doc = useMemo(() => splitFrontMatter(current?.content), [current]);
  // 機會掃描是陌生開發：機會清單改按動作排，已持有的標的在標題上標出來
  const isScan = current?.key === 'scan';
  const body = useMemo(
    () => (isScan ? stripScanPreamble(tableizeScanHighlights(sortScanOpportunities(doc.body))) : doc.body),
    [isScan, doc.body],
  );
  // 報告寫的是「進場 $253-260」，但那是產出當下的計畫 —— 沒有現價就不知道現在能不能動。
  // 一次 bulk 抓完（/prices 是單一 yfinance 呼叫 + 2 分鐘快取），掛在每檔的標題上。
  const scanOpps = useMemo(
    () => (isScan ? parseOpportunities(current?.content || '') : []),
    [isScan, current],
  );
  const { quotes } = useQuotes(scanOpps);

  const pick = (k) => {
    setKey(k);
    // 換段落等於換一份文件：正文不再自帶捲動框，不主動帶回開頭的話會停在上一份
    // 的捲動深度（多半已在附錄裡）。用 querySelector 而不是 ref —— 這個元件裡多
    // 一個 useRef 會讓 React Compiler 整個放棄它的 memo（兩萬字的 parse 靠那
    // 兩顆 useMemo 撐著）。
    document.querySelector('[data-report-body]')?.scrollIntoView({ block: 'start' });
  };

  if (!current) return null;

  const docChars = body.replace(/\s/g, '').length;

  // 四個帶：①看哪一份（面板頂端）②這一份是什麼、可不可信 ③今天要做什麼 ④全文。
  // 用整條分隔線切開，而不是讓五、六個區塊平鋪在同一個 space-y 裡 —— 平鋪時
  // 每一塊看起來都一樣重，讀者得自己判斷哪個是導覽、哪個是狀態、哪個是結論。
  return (
    <div>
      <ReportIdentity
        front={doc.front}
        label={current.label}
        completeness={report?.completeness}
        sections={sections}
        activeKey={current.key}
        onPick={pick}
      />

      {/* key：換段落時讓閱讀器重掛，附錄收合狀態與目前段落一起歸零 */}
      <div data-report-body className="scroll-mt-[5.5rem] pt-4">
        <p className="mb-3 flex items-baseline gap-2 text-base font-semibold text-[var(--text-primary)]">
          <FileText size={14} className="translate-y-0.5 text-[var(--text-muted)]" aria-hidden="true" />
          報告全文
          <span className="text-[13px] font-normal text-[var(--text-meta)]">
            約 {docChars.toLocaleString()} 字
          </span>
        </p>
        <ReportReader key={current.key} content={body} tickers={tickers} markHeld={isScan}
                      quotes={isScan ? quotes : null} />
      </div>
    </div>
  );
}

// ── 分頁內容：市場新聞 ───────────────────────────────────────────────────
const NEWS_GROUPS = [
  { key: 'market',    label: '🌍 大盤 · 宏觀' },
  { key: 'holding',   label: '💼 我的持倉' },
  { key: 'watchlist', label: '👀 觀察名單' },
];

function NotesBody({ report }) {
  if (!report) return null;
  const items = report.items || [];
  if (items.length === 0) {
    return <div className="text-sm text-[var(--text-muted)] text-center py-6">本日無新聞</div>;
  }
  // scope 沒對上任何一組的（舊檔／退場情境）全部歸到大盤，不能整則消失
  const known = new Set(NEWS_GROUPS.map(g => g.key));
  const groups = NEWS_GROUPS.map(g => ({
    ...g,
    items: items.filter(it => (known.has(it.scope) ? it.scope : 'market') === g.key),
  })).filter(g => g.items.length > 0);

  return (
    <div className="space-y-4">
      {report.market_summary && (
        <div className="rounded-xl border border-[var(--glass-border)] bg-[rgba(6,182,212,0.06)] p-4">
          <p className="text-[12px] font-semibold text-[var(--accent-cyan)] mb-1.5">今日主線</p>
          <ul className="space-y-1">
            {splitSentences(report.market_summary).map((t, k) => (
              <li key={k} className="flex gap-1.5 text-[15px] leading-relaxed text-[var(--text-body)]">
                <span className="shrink-0 text-[var(--accent-cyan-text)]" aria-hidden="true">•</span>
                <span className="max-w-[76ch]">{t}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.curated === false && (
        <div className="flex items-center gap-2 text-[13px] text-[var(--accent-amber)]">
          <AlertTriangle size={14} /> AI 策展未執行，以下為未整理的原始頭條。
        </div>
      )}

      {/* 三條管線的語氣在畫面上看起來同等權威，所以要寫明誰能給買賣結論 */}
      <p className="text-[12px] text-[var(--text-muted)]">
        {report.role_note || '純事實與來源；買賣結論只由 🎖️ War Room 盤後復盤提供。'}
      </p>

      {groups.map(g => (
        <div key={g.key} className="space-y-1.5">
          <p className="text-[13px] font-semibold text-[var(--text-muted)] tracking-wide">
            {g.label}（{g.items.length}）
          </p>
          <ol className="overflow-hidden rounded-xl border border-[var(--glass-border)] divide-y divide-[var(--glass-border)]">
            {g.items.map((item, i) => (
              <NewsRow key={`${report.date}-${g.key}-${i}`} item={item} index={i + 1} />
            ))}
          </ol>
        </div>
      ))}

      {report.skipped && (
        <p className="text-[12px] text-[var(--text-muted)] leading-relaxed pt-1">
          已濾掉：{report.skipped}
        </p>
      )}
    </div>
  );
}

/**
 * 每日報告面板 — 持倉復盤 / 市場個股 / 市場新聞三份日報合併掛在 Portfolio 頁上。
 * 三份是各自獨立的 pipeline、日期清單不同，但共用同一組日曆控制：
 * 切分頁時盡量停在同一天，那天沒有該份報告才退回該分頁的最新一天。
 */
/**
 * @param {object} weights   ticker → 權重%，持倉復盤對照表排序與代號白名單用
 * @param {'summary'|'full'} variant
 *        summary（Portfolio 頁）：只給總覽，要看細節去 /briefing
 *        full（/briefing）：四個分頁全開
 * @param {string} initialTab      full 模式的起始分頁（舊網址／總覽跳轉帶進來）
 * @param {string} initialSection  市場個股要停的子分頁（warroom / scan …）
 */
export default function DailyDigestPanel({ weights, variant = 'full', initialTab, initialSection }) {
  const summaryOnly = variant === 'summary';
  const [tab, setTab] = useState(() => {
    if (summaryOnly) return 'overview';
    // feedOnly 的（復盤）不是分頁 —— 舊的 localStorage 值也要擋掉，不然開頁是空白
    const ok = k => TAB_BY_KEY[k] && !TAB_BY_KEY[k].feedOnly;
    if (ok(initialTab)) return initialTab;
    try {
      const saved = localStorage.getItem(TAB_LS_KEY);
      return ok(saved) ? saved : 'overview';
    } catch { return 'overview'; }
  });
  const [datesByTab, setDatesByTab] = useState({ review: [], market: [], notes: [] });
  const [date, setDate] = useState(null);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [calOpen, setCalOpen] = useState(false);
  const [newsStatus, setNewsStatus] = useState('idle');  // 市場新聞：立即產生
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(OPEN_LS_KEY) !== '0'; } catch { return true; }
  });

  const cache = useRef({});          // `${tab}:${date}` → payload，切分頁/翻頁不重打 API
  const rootRef = useRef(null);
  const calRef = useRef(null);
  const newsPollRef = useRef(null);

  const [jumpSection, setJumpSection] = useState(initialSection || null);   // →「市場個股」要停的子分頁

  const meta = TAB_BY_KEY[tab];
  const TabIcon = meta.icon;
  // 總覽沒有自己的清單 —— 三份任一有這天就列得出來
  const overviewDates = useMemo(() => {
    const all = new Set(SOURCE_TABS.flatMap(t => datesByTab[t.key] || []));
    return [...all].sort().reverse();
  }, [datesByTab]);
  const dates = tab === 'overview' ? overviewDates : (datesByTab[tab] || []);

  // 三條管線不同步 —— 持倉復盤（AI 卡每天都預產）最勤，市場個股／市場新聞常常慢一兩天。
  // 直接取聯集最新一天會落在「只有持倉復盤、其餘全是『這天沒有』」的空白頁，
  // 使用者得自己用日曆倒回去找。預設改成最近一份「至少兩條管線齊備」的日子。
  const overviewDefault = useMemo(() => {
    if (!overviewDates.length) return null;
    const covered = d => SOURCE_TABS.filter(t => (datesByTab[t.key] || []).includes(d)).length;
    return overviewDates.find(d => covered(d) >= 2) || overviewDates[0];
  }, [overviewDates, datesByTab]);

  // 跳過了哪幾天、那幾天有什麼 —— 要寫出來，不能讓人以為最新一天不存在
  const skippedDates = useMemo(() => {
    if (tab !== 'overview' || !overviewDefault || date !== overviewDefault) return [];
    const i = overviewDates.indexOf(overviewDefault);
    return i > 0 ? overviewDates.slice(0, i) : [];
  }, [tab, date, overviewDefault, overviewDates]);

  const [, navigate] = useLocation();
  const jumpTo = useCallback((key, section) => {
    // 復盤沒有自己的分頁了 —— 完整畫面在 /review
    const target = TAB_BY_KEY[key];
    if (target?.href) { navigate(target.href); return; }
    if (summaryOnly) {
      // Portfolio 頁沒有那些分頁 —— 帶著目標分頁／子分頁去完整版
      navigate(`/briefing?tab=${key}${section ? `&section=${section}` : ''}`);
      return;
    }
    setJumpSection(section || null);
    setTab(key);
  }, [summaryOnly, navigate]);
  // 報告內文的 **XXX** 要連得出去；持倉清單當白名單，單字母的 C 這種只有它救得了
  const tickerList = useMemo(() => Object.keys(weights || {}), [weights]);

  useEffect(() => {
    try { localStorage.setItem(OPEN_LS_KEY, open ? '1' : '0'); } catch { /* private mode */ }
  }, [open]);
  useEffect(() => {
    if (summaryOnly) return;   // 總覽模式是被鎖住的，別覆蓋完整版記住的分頁
    try { localStorage.setItem(TAB_LS_KEY, tab); } catch { /* private mode */ }
  }, [tab, summaryOnly]);

  // 三份的日期清單一次抓齊：切分頁與月曆才不用等
  useEffect(() => {
    let alive = true;
    Promise.allSettled(TABS.map(t => axios.get(t.api)))
      .then(results => {
        if (!alive) return;
        const next = {};
        results.forEach((r, i) => {
          next[TABS[i].key] = r.status === 'fulfilled' ? (r.value.data.dates || []) : [];
        });
        setDatesByTab(next);
        if (next[tab]?.length === 0) setLoading(false);
      });
    return () => { alive = false; };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // 切分頁 / 清單到齊後對齊日期：同一天有就留著，沒有就退回這份的最新一天
  useEffect(() => {
    if (!dates.length) return;
    if (!date || !dates.includes(date)) {
      setDate(tab === 'overview' ? (overviewDefault || dates[0]) : dates[0]);
    }
  }, [tab, dates, overviewDefault]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!date || !open || !dates.includes(date)) return;
    const key = `${tab}:${date}`;
    if (cache.current[key]) { setReport(cache.current[key]); setError(''); setLoading(false); return; }
    let alive = true;
    setLoading(true);

    // 總覽把三份平行抓齊。哪一份這天本來就沒有 → 畫面寫明「沒有這份」；
    // 該有卻抓不到才算失敗，兩者不能混為一談。
    const fetcher = tab === 'overview'
      ? Promise.allSettled(SOURCE_TABS.map(t => axios.get(`${t.api}/${date}`))).then(rs => {
          const out = {};
          rs.forEach((r, i) => { out[SOURCE_TABS[i].key] = r.status === 'fulfilled' ? r.value.data : null; });
          const expected = SOURCE_TABS.filter(t => (datesByTab[t.key] || []).includes(date));
          if (expected.length && !expected.some(t => out[t.key])) throw new Error('all sources failed');
          return out;
        })
      : axios.get(`${meta.api}/${date}`).then(r => r.data);

    fetcher
      .then(payload => {
        if (!alive) return;
        cache.current[key] = payload;
        setReport(payload);
        setError('');
      })
      .catch(() => { if (alive) { setReport(null); setError(`無法載入 ${date} 的${meta.label}日報`); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tab, date, open]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 點面板外面關掉月曆
  useEffect(() => {
    if (!calOpen) return;
    const onDown = (e) => { if (!calRef.current?.contains(e.target)) setCalOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setCalOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [calOpen]);

  useEffect(() => () => clearInterval(newsPollRef.current), []);

  // 市場新聞：手動觸發今日策展（腳本要抓幾十檔新聞 + 一輪 claude，約 2-5 分鐘）
  const regenerateNews = async () => {
    setNewsStatus('running');
    setError('');
    try {
      await axios.post('/api/market-news/run');
      newsPollRef.current = setInterval(async () => {
        try {
          const { data } = await axios.get('/api/market-news/status');
          if (data.status === 'running') return;
          clearInterval(newsPollRef.current);
          setNewsStatus('idle');
          if (data.error) { setError(`市場新聞產生失敗：${data.error}`); return; }
          const ds = (await axios.get('/api/market-news')).data.dates || [];
          setDatesByTab(prev => ({ ...prev, notes: ds }));
          const latest = ds[0];
          if (!latest) return;
          delete cache.current[`notes:${latest}`];
          if (latest === date) {
            const r = await axios.get(`/api/market-news/${latest}`);
            cache.current[`notes:${latest}`] = r.data;
            setReport(r.data);
          } else {
            setDate(latest);
          }
        } catch { clearInterval(newsPollRef.current); setNewsStatus('idle'); }
      }, 5000);
    } catch (e) {
      setNewsStatus('idle');
      setError(e.response?.data?.detail || '觸發失敗');
    }
  };

  // dates 是新→舊，所以 index-1 是更新的一天、index+1 是更早的一天
  const idx = date ? dates.indexOf(date) : -1;
  const newer = idx > 0 ? dates[idx - 1] : null;
  const older = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null;
  const isLatest = idx === 0;

  const generatedAt = report?.generated_at || report?.generatedAt;
  const subtitle = date
    ? `${date} · 週${weekdayOf(date)}${isLatest ? ' · 最新' : ''}`
    : meta.blurb;

  // 完整版（/briefing）就是「這一頁＝這份報告」：頁面已經有 h1，面板不必再寫一次
  // 標題，也不必給一顆把整頁收起來的按鈕。分頁與日期併成同一列，少一層外框。
  const shown = summaryOnly ? open : true;

  // 帶 1「看哪一份」：完整版把分頁、日期、產生按鈕全收進卡片頂端的控制帶，跟
  // 底下的內容用整條邊界隔開 —— 導覽是導覽，內容是內容，不要混在同一個 space-y。
  const band = summaryOnly ? '' : 'rounded-t-2xl border-b border-[var(--glass-border)] bg-[var(--bg-secondary)] px-5 py-3';

  // 「產生於」只在沒有報告標頭可讀的分頁顯示 —— 市場個股的標頭本來就寫了產出時間，
  // 同一個時間寫兩個地方正是原本讀起來散的來源之一。
  const busy = newsStatus === 'running';
  const actions = (
    <div className="flex items-center gap-3">
      {generatedAt && tab !== 'market' && (
        <span className="text-xs text-[var(--text-meta)]">
          產生於 {String(generatedAt).slice(0, 16).replace('T', ' ')}
        </span>
      )}
      {tab === 'notes' && report && (
        <span className="text-xs text-[var(--text-meta)]">策展 {report.count}/{report.total} 則</span>
      )}
      {!summaryOnly && tab === 'notes' && (
        <button
          onClick={regenerateNews}
          disabled={busy}
          className="digest-focusable inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-[var(--text-muted)] transition-all hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] disabled:opacity-50"
        >
          <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
          {busy ? '策展中…（數分鐘）' : '立即產生今日'}
        </button>
      )}
    </div>
  );

  return (
    <div ref={rootRef} className={`card scroll-mt-24 ${summaryOnly ? 'space-y-4 p-5' : 'card-flat p-0'}`}>
      <div className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-3 ${band}`}>
        {summaryOnly ? (
          <button onClick={() => setOpen(v => !v)} className="group flex items-center gap-2.5 text-left">
            <TabIcon size={15} className="text-[var(--accent-cyan)]" />
            <div>
              {/* 上面那張「組合體檢」是純數據；這張是 AI 寫的判讀，標題要看得出差別 */}
              <p className="text-sm font-semibold text-[var(--text-secondary)] transition-colors group-hover:text-[var(--text-primary)]">
                AI 每日報告 · Daily Briefing
              </p>
              <p className="text-[11px] text-[var(--text-muted)]">AI 判讀 · 持倉復盤／市場個股／市場新聞</p>
              <p className="text-xs text-[var(--text-meta)]">{subtitle}</p>
            </div>
            {open ? <ChevronUp size={14} className="text-[var(--text-muted)]" /> : <ChevronDown size={14} className="text-[var(--text-muted)]" />}
          </button>
        ) : (
          <div className="flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-1">
            {NAV_TABS.map(t => {
              const Icon = t.icon;
              // 總覽沒有自己的清單，用三份的聯集算，不然它會被自己 disable 掉
              const count = t.key === 'overview' ? overviewDates.length : (datesByTab[t.key]?.length ?? 0);
              return (
                <button
                  key={t.key}
                  onClick={() => { setJumpSection(null); setTab(t.key); }}
                  disabled={count === 0}
                  title={count === 0 ? `${t.label}：尚無報告` : t.blurb}
                  className={`tap-44 digest-focusable inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 disabled:cursor-default disabled:opacity-40 ${
                    tab === t.key ? t.active : 'text-[var(--text-meta)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Icon size={13} />
                  {t.label}
                </button>
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {!summaryOnly && actions}
        {dates.length > 0 && (
          <div ref={calRef} className="relative flex items-center gap-2">
            <div className="flex items-center gap-1 p-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--glass-border)]">
              <button
                onClick={() => { if (older) { setDate(older); setOpen(true); } }}
                disabled={!older}
                title={older ? `前一份：${older}` : '沒有更早的了'}
                className="tap-44 digest-focusable cursor-pointer rounded-md p-1.5 text-[var(--text-meta)] transition-colors duration-150 hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent"
              >
                <ChevronLeft size={16} aria-hidden="true" />
              </button>
              <button
                onClick={() => { setCalOpen(v => !v); setOpen(true); }}
                className={`tap-44 digest-focusable inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13.5px] font-semibold transition-colors duration-150 ${
                  calOpen ? meta.active : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <CalendarDays size={13} />
                {labelDate(date)}
              </button>
              <button
                onClick={() => { if (newer) { setDate(newer); setOpen(true); } }}
                disabled={!newer}
                title={newer ? `後一份：${newer}` : '已經是最新一份'}
                className="tap-44 digest-focusable cursor-pointer rounded-md p-1.5 text-[var(--text-meta)] transition-colors duration-150 hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent"
              >
                <ChevronRight size={16} aria-hidden="true" />
              </button>
            </div>

            {!isLatest && (
              <button
                onClick={() => setDate(dates[0])}
                className="tap-44 digest-focusable flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-[var(--text-meta)] transition-colors duration-150 hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]"
              >
                <RefreshCw size={12} /> 回到最新
              </button>
            )}

            {calOpen && (
              <DigestCalendar
                dates={dates}
                active={date}
                accent={meta}
                onPick={setDate}
                onClose={() => setCalOpen(false)}
              />
            )}
          </div>
        )}
        </div>
      </div>

      {/* 總覽模式不能用 display:contents 當包裝 —— 卡片的 space-y 是 `> * + *`，
          間距會掛在這個沒有盒子的元素上，等於整段內容的垂直節奏全部消失。 */}
      {shown && (
        <div className={summaryOnly ? 'space-y-4' : 'px-5 pb-5 pt-4'}>
          {/* 總覽模式（Portfolio 頁）沒有子分頁列，用這一行說明現在看的是什麼；
              完整版的分頁、日期與產生按鈕都已經在上面的控制帶裡。
              去完整版的入口只留各區塊標題右邊那三顆（看完整復盤／看全部候選／看全部
              新聞）—— 它們帶得到分頁與子分頁；這裡再放一顆泛用的「看完整日報」是
              同一個目的地，兩顆並排只是重複。 */}
          {summaryOnly && (
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-[var(--glass-border)] pt-4">
              <p className="min-w-0 flex-1 text-xs leading-relaxed text-[var(--text-meta)]">{meta.blurb}</p>
              {actions}
            </div>
          )}

          {skippedDates.length > 0 && (
            <p className="-mt-1 flex items-start gap-1.5 text-xs leading-relaxed text-[var(--accent-amber-text)]">
              <Info size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
              <span>較新的 {skippedDates.map(d => labelDate(d).split('（')[0]).join('、')} 只有{' '}
              {[...new Set(skippedDates.flatMap(d =>
                SOURCE_TABS.filter(t => (datesByTab[t.key] || []).includes(d)).map(t => t.label),
              ))].join('、') || '（無）'}
              {' '}有產出 —— 已帶你到最近一份較齊全的 {labelDate(date)}。用日曆或「回到最新」可切過去。</span>
            </p>
          )}

          {loading && (
            <div className="flex items-center gap-2 text-[var(--text-muted)] py-8 justify-center text-sm">
              <Loader2 className="animate-spin" size={16} /> 載入中…
            </div>
          )}

          {!loading && error && (
            <div className="flex items-center justify-center gap-2 text-xs text-[var(--accent-danger)] py-6">
              <AlertTriangle size={14} /> {error}
            </div>
          )}

          {!loading && !error && dates.length === 0 && (
            <div className="text-xs text-[var(--text-muted)] py-6 text-center">
              尚無{meta.label}報告。
            </div>
          )}

          {!loading && !error && report && (
            <>
              {tab === 'overview' && <OverviewBody data={report} onJump={jumpTo} weights={weights} />}
              {tab === 'market' && (
                <MarketDigestBody key={report.date} report={report} tickers={tickerList}
                                  initialSection={jumpSection} />
              )}
              {tab === 'notes' && <NotesBody report={report} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
