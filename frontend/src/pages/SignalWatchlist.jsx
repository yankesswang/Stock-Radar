/**
 * SignalWatchlist — 訊號雷達（/signals）
 *
 * 2026-09 改版：原本每檔一張卡、把 3–11 個徽章全攤出來、依徽章數排序 ——
 * 240 檔裡 234 檔都有訊號、97 檔多空混雜，等於沒有重點。
 *
 * 後端（routers/signals.py `_summarize`）現在每檔給一句話：
 *   setup（型態：趨勢轉多／超賣反彈啟動／動能轉空…）+ headline（主角訊號）
 *   + supporting（其餘佐證，震盪族摺成「N 個震盪指標超賣」）+ tier（1 行動 → 4 盤整）。
 * 這頁只做三件事：
 *   1. 排序 —— 預設依 trend_strength（訊號分數＋ADX＋5 日幅度）由強到弱排成一張平表；
 *      「分層」檢視才把事件（tier 1–2）／狀態（tier 3）／盤整（tier 4）分開，盤整預設收起
 *   2. 一列一句 —— 代號｜價格與漲跌｜型態徽章｜標題＋佐證｜多空票數｜估值
 *   3. 漸進揭露 —— 點列才展開完整指標清單；持倉優先標記與篩選
 *
 * 視覺與 WarRoom / PostMarketReview 同家族（opsTheme 暗色終端機調色盤，不跟 app 主題）。
 * 色彩皆為語意色（多方=teal、空方=red、中性=amber），且一律配文字徽章，不做 color-alone。
 */
import { useState, useEffect, useMemo, useCallback } from 'react';
import { Link } from 'wouter';
import axios from '../lib/apiClient';
import {
  Radar, RefreshCw, ChevronDown, Clock, Search, ArrowUpRight, Briefcase, TrendingUp, TrendingDown, Flame, Layers, Percent,
} from 'lucide-react';
import ClusterTabs from '../components/ClusterTabs';
import { OPS_TABS } from '../components/clusterTabsDef';
import { C, alpha, fmt, signPct } from '../components/opsTheme';
import { Panel, FilterChip, Empty } from '../components/opsUi';

// ── Setup taxonomy（順序 = 頁面上的排序；key 對應後端 SETUP_META）────────────
const SETUPS = [
  { key: 'trend_up',   label: '趨勢轉多',     dir: 'bullish', tier: 1 },
  { key: 'bounce',     label: '超賣反彈啟動', dir: 'bullish', tier: 1 },
  { key: 'trend_down', label: '趨勢轉空',     dir: 'bearish', tier: 1 },
  { key: 'fading',     label: '高檔轉弱',     dir: 'bearish', tier: 1 },
  { key: 'mom_up',     label: '動能轉多',     dir: 'bullish', tier: 2 },
  { key: 'mom_down',   label: '動能轉空',     dir: 'bearish', tier: 2 },
  { key: 'conflict',   label: '多空對撞',     dir: 'neutral', tier: 2 },
  { key: 'oversold',   label: '超賣觀察',     dir: 'bullish', tier: 3 },
  { key: 'overbought', label: '超買觀察',     dir: 'bearish', tier: 3 },
  { key: 'strong',     label: '強勢延續',     dir: 'bullish', tier: 3 },
  { key: 'weak',       label: '弱勢延續',     dir: 'bearish', tier: 3 },
  { key: 'coiling',    label: '蓄勢盤整',     dir: 'neutral', tier: 4 },
  { key: 'noise',      label: '零星訊號',     dir: 'neutral', tier: 4 },
];
const SETUP_BY_KEY = Object.fromEntries(SETUPS.map((s) => [s.key, s]));

const dirColor = (dir) => (dir === 'bullish' ? C.long : dir === 'bearish' ? C.short : dir === 'neutral' ? C.amber : C.muted);
const sevColor = (sev) => (sev === 'bullish' ? C.long : sev === 'bearish' ? C.short : C.dim);

const SECTIONS = [
  { key: 'events', label: '事件訊號', hint: '交叉、翻轉、突破 —— 這根 K 才發生的結構或動能改變', tiers: [1, 2], collapsed: false },
  { key: 'states', label: '狀態觀察', hint: '讀數極端但還沒有事件 —— 等確認', tiers: [3], collapsed: false },
  { key: 'quiet',  label: '盤整・零星', hint: '只有壓縮或單一資金流讀數 —— 沒有動作', tiers: [4], collapsed: true },
];

// 排序檢視：預設「趨勢強度」平表；「分層」是原本的事件／狀態／盤整三段；「漲跌幅」看今天誰在動。
const SORTS = [
  { key: 'strength', label: '趨勢強度', icon: Flame,   hint: '訊號分數 ＋ ADX 強度 ＋ 5 日幅度，由強到弱' },
  { key: 'tier',     label: '分層',     icon: Layers,  hint: '事件 → 狀態 → 盤整，層內依趨勢強度' },
  { key: 'move',     label: '漲跌幅',   icon: Percent, hint: '今日漲跌幅絕對值，由大到小' },
];
const SORT_KEY = 'stockradar.signals.sort';
const readSort = () => {
  try { const v = localStorage.getItem(SORT_KEY); return SORTS.some((x) => x.key === v) ? v : 'strength'; } catch { return 'strength'; }
};

// ── Small pieces ─────────────────────────────────────────────────────────────
function Pct({ v, strong = false }) {
  if (v === null || v === undefined) return <span style={{ color: C.muted }}>—</span>;
  const color = v > 0 ? C.long : v < 0 ? C.short : C.dim;
  return (
    <span className={`tabular-nums ${strong ? 'font-semibold' : ''}`} style={{ color: strong ? color : C.dim }}>
      {signPct(v)}
    </span>
  );
}

// 估值跟個股頁標頭用同一組欄位（valuation_status / valuation_diff_pct），預估 PE ≤ 0 的
// 虧損股不顯示 —— 這欄是脈絡不是主角，所以只給狀態字＋幅度，不再列合理價。
function valuationRead(v) {
  if (!v || !(v.estimated_pe > 0) || !v.valuation_status || v.valuation_diff_pct == null) return null;
  const under = v.valuation_status === 'undervalued';
  const over = v.valuation_status === 'overvalued';
  return {
    label: under ? '低估' : over ? '高估' : '合理',
    pct: Math.abs(Number(v.valuation_diff_pct)).toFixed(0),
    color: under ? C.long : over ? C.short : C.dim,
  };
}

function SetupCounter({ setup, count, active, onClick }) {
  const color = dirColor(setup.dir);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={`只看「${setup.label}」`}
      className="flex min-w-[92px] cursor-pointer flex-col items-start gap-0.5 border px-3 py-2 text-left transition-colors duration-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--ops-accent)]"
      style={{
        borderColor: active ? color : C.border,
        background: active ? `${alpha(color, 0.078)}` : 'transparent',
        borderTopWidth: 2,
        borderTopColor: color,
      }}
    >
      <span className="font-mono text-[11px] uppercase tracking-[0.12em]" style={{ color: C.dim }}>
        {setup.label}
      </span>
      <span className="font-mono text-xl font-bold tabular-nums" style={{ color: active ? color : C.text }}>
        {count}
      </span>
    </button>
  );
}

function SignalDetail({ item }) {
  const s = item.summary;
  return (
    <div className="border-t px-3 py-3 sm:px-4" style={{ borderColor: C.border, background: C.deep }}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
          全部 {item.signals.length} 個訊號 · 事件 {s.event_count} · 分數 {s.score > 0 ? '+' : ''}{s.score}
        </span>
        <Link
          href={`/stock/${item.ticker}`}
          className="inline-flex items-center gap-1 font-mono text-[12px] font-bold uppercase tracking-wider transition-opacity duration-150 hover:opacity-80"
          style={{ color: C.accent }}
        >
          開啟個股頁 <ArrowUpRight size={12} />
        </Link>
      </div>
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {item.signals.map((sig, i) => {
          const color = sevColor(sig.severity);
          return (
            <li key={`${sig.indicator}-${sig.type}-${i}`} className="flex items-start gap-2 text-[13px]">
              <span
                className="mt-0.5 inline-flex shrink-0 items-center rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold uppercase"
                style={{ color, background: `${alpha(color, 0.102)}`, border: `1px solid ${alpha(color, 0.333)}` }}
              >
                {sig.kind === 'event' ? '事件' : '狀態'}
              </span>
              <span className="min-w-0">
                <span style={{ color: C.text }}>{sig.zh}</span>
                <span className="ml-1.5 font-mono tabular-nums" style={{ color: C.dim }}>{sig.value}</span>
                <span className="block truncate text-[12px]" style={{ color: C.muted }} title={sig.desc}>
                  {sig.indicator} · {sig.type} — {sig.desc}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SignalRow({ item, held, expanded, onToggle, compact, rank }) {
  const s = item.summary;
  const setup = SETUP_BY_KEY[s.setup] || { label: s.setup_label, dir: s.direction };
  const color = dirColor(setup.dir);
  const val = valuationRead(item.valuation);
  const support = s.supporting || [];
  const shown = support.slice(0, compact ? 2 : 3);
  const more = support.length - shown.length;

  const onKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onToggle();
    }
  };

  return (
    <li className="border-b last:border-b-0" style={{ borderColor: C.border }}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={onKey}
        className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-l-2 px-3 py-2.5 transition-colors duration-200 hover:bg-[var(--ops-deep)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--ops-accent)] md:grid-cols-[156px_140px_112px_minmax(0,1fr)_72px_118px_20px]"
        style={{
          borderLeftColor: held ? C.accent : color,
          background: expanded ? C.deep : held ? `${alpha(C.accent, 0.031)}` : 'transparent',
        }}
      >
        {/* 1. 代號 / 公司 */}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {rank != null && (
              <span className="w-6 shrink-0 font-mono text-[11px] tabular-nums" style={{ color: C.muted }} aria-label={`第 ${rank} 名`}>
                {rank}
              </span>
            )}
            <Link
              href={`/stock/${item.ticker}`}
              onClick={(e) => e.stopPropagation()}
              className="font-mono text-[15px] font-bold tracking-wide transition-opacity duration-150 hover:opacity-80"
              style={{ color: C.text }}
              title={`開啟 ${item.ticker} 個股頁`}
            >
              {item.ticker}
            </Link>
            {held && (
              <span
                className="inline-flex items-center gap-1 rounded px-1.5 py-px font-mono text-[10px] font-bold uppercase tracking-wider"
                style={{ color: C.accent, background: `${alpha(C.accent, 0.102)}`, border: `1px solid ${alpha(C.accent, 0.333)}` }}
              >
                <Briefcase size={9} /> 持倉
              </span>
            )}
          </div>
          <div className={`truncate text-[12px] ${rank != null ? 'pl-8' : ''}`} style={{ color: C.dim }} title={`${item.company_name} · ${item.industry}`}>
            {item.company_name} <span style={{ color: C.muted }}>· {item.industry}</span>
          </div>
        </div>

        {/* 2. 價格 / 漲跌 */}
        <div className="row-start-1 col-start-2 text-right md:col-auto md:row-auto md:text-left">
          <div className="font-mono text-[14px] font-semibold tabular-nums" style={{ color: C.text }}>
            {item.latest_close != null ? `$${fmt(item.latest_close)}` : '—'}
          </div>
          <div className="flex justify-end gap-2 whitespace-nowrap font-mono text-[12px] md:justify-start">
            <span title="最近一個交易日"><Pct v={item.change_1d_pct} strong /></span>
            <span style={{ color: C.muted }} title="近 5 個交易日">
              5d <Pct v={item.change_5d_pct} />
            </span>
          </div>
        </div>

        {/* 3. 型態 */}
        <div className="col-span-2 flex items-center gap-2 md:col-span-1">
          <span
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[12px] font-bold whitespace-nowrap"
            style={{ color, background: `${alpha(color, 0.102)}`, border: `1px solid ${alpha(color, 0.333)}` }}
          >
            {setup.dir === 'bullish' && <TrendingUp size={11} />}
            {setup.dir === 'bearish' && <TrendingDown size={11} />}
            {setup.label}
          </span>
          {s.trend_ctx && (
            <span className="font-mono text-[11px]" style={{ color: C.muted }} title="ADX ≥ 25 的趨勢方向">
              ADX{s.trend_ctx === 'up' ? '↑' : '↓'}
            </span>
          )}
        </div>

        {/* 4. 標題 + 佐證 */}
        <div className="col-span-2 min-w-0 md:col-span-1">
          <div className="truncate text-[14px] font-medium" style={{ color: C.text }} title={s.headline}>
            {s.headline}
          </div>
          {shown.length > 0 && (
            <div className="truncate text-[12px]" style={{ color: C.dim }} title={support.map((x) => x.zh).join(' · ')}>
              {shown.map((x, i) => (
                <span key={i}>
                  {i > 0 && <span style={{ color: C.muted }}> · </span>}
                  <span style={{ color: x.severity === 'neutral' ? C.dim : sevColor(x.severity) }}>
                    {x.severity === 'bullish' ? '▲' : x.severity === 'bearish' ? '▼' : '◆'}
                  </span>{' '}
                  {x.zh}
                </span>
              ))}
              {more > 0 && <span style={{ color: C.muted }}> · +{more}</span>}
            </div>
          )}
        </div>

        {/* 5. 趨勢強度 + 多空票數 */}
        <div className="hidden font-mono text-[12px] tabular-nums md:block" title="趨勢強度 = 訊號分數 + ADX + 5 日幅度；下行是多方 / 空方訊號數">
          <div className="font-semibold" style={{ color }}>
            {s.trend_strength != null ? s.trend_strength.toFixed(1) : '—'}
          </div>
          <span style={{ color: s.bull_count ? C.long : C.muted }}>▲{s.bull_count}</span>
          <span style={{ color: C.muted }}> / </span>
          <span style={{ color: s.bear_count ? C.short : C.muted }}>▼{s.bear_count}</span>
          {s.volume_spike && (
            <span className="ml-1.5" style={{ color: C.amber }} title="量能 ≥ 2 倍 20 日均量">
              量
            </span>
          )}
        </div>

        {/* 6. 估值 */}
        <div className="hidden font-mono text-[12px] md:block" title="本益比估值狀態（與個股頁標頭同一來源）">
          {val ? (
            <>
              <span className="font-semibold" style={{ color: val.color }}>{val.label}</span>
              <span className="ml-1 tabular-nums" style={{ color: C.dim }}>{val.pct}%</span>
              <span className="ml-1" style={{ color: C.muted }}>PE {fmt(item.valuation.estimated_pe, 0)}x</span>
            </>
          ) : (
            <span style={{ color: C.muted }}>估值 —</span>
          )}
        </div>

        {/* 7. 展開 */}
        <ChevronDown
          size={16}
          className="hidden transition-transform duration-200 md:block"
          style={{ color: C.muted, transform: expanded ? 'rotate(180deg)' : 'none' }}
          aria-hidden
        />
      </div>
      {expanded && <SignalDetail item={item} />}
    </li>
  );
}

function SectionPanel({ section, rows, expandedTicker, onToggle, heldSet, forceOpen }) {
  const [open, setOpen] = useState(!section.collapsed);
  const isOpen = forceOpen || open;
  if (!rows.length) return null;
  return (
    <Panel
      label={section.label}
      status={
        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[11px] sm:inline" style={{ color: C.muted }}>{section.hint}</span>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={isOpen}
            className="inline-flex cursor-pointer items-center gap-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-opacity duration-150 hover:opacity-80"
            style={{ color: C.accent }}
          >
            {rows.length} 檔
            <ChevronDown size={12} style={{ transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform 200ms' }} />
          </button>
        </div>
      }
    >
      {isOpen ? (
        <ul className="-mx-4 -mb-4 border-t" style={{ borderColor: C.border }}>
          {rows.map((item) => (
            <SignalRow
              key={item.ticker}
              item={item}
              held={heldSet.has(item.ticker)}
              expanded={expandedTicker === item.ticker}
              onToggle={() => onToggle(item.ticker)}
              compact={section.key !== 'events'}
            />
          ))}
        </ul>
      ) : (
        <div className="-mt-1 truncate font-mono text-[12px]" style={{ color: C.muted }}>
          {rows.slice(0, 12).map((r) => r.ticker).join(' · ')}
          {rows.length > 12 ? ` · +${rows.length - 12}` : ''}
        </div>
      )}
    </Panel>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function SignalWatchlist() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [scannedAt, setScannedAt] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [heldSet, setHeldSet] = useState(() => new Set());

  const [dirFilter, setDirFilter] = useState('all'); // all | bullish | bearish
  const [heldOnly, setHeldOnly] = useState(false);
  const [setupFilter, setSetupFilter] = useState(null);
  const [search, setSearch] = useState('');
  const [expandedTicker, setExpandedTicker] = useState(null);
  const [sort, setSort] = useState(readSort);

  const fetchData = useCallback(async (bust = false) => {
    setLoading(true);
    setError(null);
    if (bust) {
      setScanning(true);
      await axios.delete('/api/signals/watchlist/cache').catch(() => {});
    }
    try {
      const res = await axios.get('/api/signals/watchlist');
      setItems(Array.isArray(res.data) ? res.data : []);
      setScannedAt(new Date());
    } catch (e) {
      setError(e.response?.data?.detail ?? '掃描失敗');
    } finally {
      setLoading(false);
      setScanning(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => { try { localStorage.setItem(SORT_KEY, sort); } catch { /* private mode */ } }, [sort]);

  // 持倉標記：跟 War Room 用同一份 overview，抓不到就當沒有持倉，不擋頁面。
  useEffect(() => {
    let alive = true;
    axios.get('/api/war-room/overview')
      .then((res) => {
        if (!alive) return;
        const hs = Array.isArray(res.data?.holdings) ? res.data.holdings : [];
        setHeldSet(new Set(hs.map((h) => String(h.ticker || '').toUpperCase())));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const setupCounts = useMemo(() => {
    const m = {};
    for (const it of items) m[it.summary?.setup] = (m[it.summary?.setup] || 0) + 1;
    return m;
  }, [items]);

  const heldCount = useMemo(() => items.filter((it) => heldSet.has(it.ticker)).length, [items, heldSet]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const strength = (it) => it.summary?.trend_strength ?? it.summary?.attention ?? 0;
    const cmp = sort === 'move'
      ? (a, b) => Math.abs(b.change_1d_pct ?? 0) - Math.abs(a.change_1d_pct ?? 0) || strength(b) - strength(a)
      : sort === 'tier'
        ? (a, b) => (a.summary?.tier ?? 9) - (b.summary?.tier ?? 9) || strength(b) - strength(a)
        : (a, b) => strength(b) - strength(a);
    return items.filter((it) => {
      const s = it.summary || {};
      if (setupFilter && s.setup !== setupFilter) return false;
      if (dirFilter !== 'all' && s.direction !== dirFilter) return false;
      if (heldOnly && !heldSet.has(it.ticker)) return false;
      if (q && !(
        it.ticker.toLowerCase().includes(q)
        || (it.company_name || '').toLowerCase().includes(q)
        || (it.industry || '').toLowerCase().includes(q)
      )) return false;
      return true;
    }).sort(cmp);
  }, [items, setupFilter, dirFilter, heldOnly, heldSet, search, sort]);

  const bySection = useMemo(() => {
    const m = {};
    for (const sec of SECTIONS) m[sec.key] = filtered.filter((it) => sec.tiers.includes(it.summary?.tier));
    return m;
  }, [filtered]);

  const tierCount = (tiers) => items.filter((it) => tiers.includes(it.summary?.tier)).length;
  const anyFilter = setupFilter || dirFilter !== 'all' || heldOnly || search.trim();
  const toggleExpanded = (t) => setExpandedTicker((cur) => (cur === t ? null : t));

  return (
    <div className="-mt-4 min-h-screen px-3 pb-10 pt-4 sm:px-5" style={{ background: C.bg, color: C.text, fontFamily: "'Fira Sans', sans-serif" }}>
      <div className="mx-auto max-w-6xl space-y-4">
        {/* 決策層的三個切面 —— War Room 的 SIGNAL FEED 是這一頁的摘要版 */}
        <ClusterTabs items={OPS_TABS} active="/signals" theme="ops" className="mt-2 w-fit" />

        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Radar size={20} style={{ color: C.accent }} />
            <div>
              <h1 className="font-mono text-lg font-bold tracking-wide" style={{ color: C.text }}>
                訊號雷達 <span style={{ color: C.muted }}>/ SIGNAL RADAR</span>
              </h1>
              <p className="text-[13px]" style={{ color: C.dim }}>
                {loading
                  ? '掃描全部追蹤股最近一個交易日的技術訊號'
                  : `${tierCount([1, 2])} 檔有事件 · ${tierCount([3])} 檔讀數極端 · ${tierCount([4])} 檔盤整零星 —— 預設趨勢最強的排最前，點列展開全部指標`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {scannedAt && (
              <span className="inline-flex items-center gap-1.5 font-mono text-[12px]" style={{ color: C.dim }}>
                <Clock size={12} />
                {scannedAt.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            <button
              type="button"
              onClick={() => fetchData(true)}
              disabled={loading}
              className="inline-flex cursor-pointer items-center gap-2 border px-3 py-1.5 font-mono text-[12px] font-bold uppercase tracking-wider transition-colors duration-200 hover:bg-[var(--ops-deep)] disabled:cursor-not-allowed disabled:opacity-40"
              style={{ borderColor: C.border, color: C.text }}
            >
              <RefreshCw size={13} className={scanning ? 'animate-spin' : ''} />
              {scanning ? 'Scanning' : 'Rescan'}
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="border px-4 py-3 font-mono text-[13px]" style={{ borderColor: C.short, color: C.short }}>
            {error}
          </div>
        )}

        {/* Loading */}
        {loading && !error && (
          <Panel label="掃描中">
            <p className="mb-3 font-mono text-[12px]" style={{ color: C.dim }}>
              逐檔抓一年日線算指標，約 20–40 秒；五分鐘內再開會直接用快取。
            </p>
            <div className="space-y-1.5" aria-busy="true" aria-label="載入中">
              {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="h-10 w-full animate-pulse" style={{ background: C.border, opacity: 0.6 }} />
              ))}
            </div>
          </Panel>
        )}

        {!loading && !error && (
          <>
            {/* Setup counters —— 今天有哪幾種型態、各幾檔；點一下只看那一種 */}
            <Panel label="今日型態">
              <div className="flex flex-wrap gap-2">
                {SETUPS.filter((s) => setupCounts[s.key]).map((s) => (
                  <SetupCounter
                    key={s.key}
                    setup={s}
                    count={setupCounts[s.key]}
                    active={setupFilter === s.key}
                    onClick={() => setSetupFilter((cur) => (cur === s.key ? null : s.key))}
                  />
                ))}
              </div>
            </Panel>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-2">
              <FilterChip active={dirFilter === 'all'} onClick={() => setDirFilter('all')}>ALL</FilterChip>
              <FilterChip active={dirFilter === 'bullish'} color={C.long} onClick={() => setDirFilter('bullish')}>
                <TrendingUp size={12} /> 多方
              </FilterChip>
              <FilterChip active={dirFilter === 'bearish'} color={C.short} onClick={() => setDirFilter('bearish')}>
                <TrendingDown size={12} /> 空方
              </FilterChip>
              <FilterChip
                active={heldOnly}
                color={C.accent}
                onClick={() => setHeldOnly((v) => !v)}
                title={heldCount ? `${heldCount} 檔持倉有訊號` : '目前持倉沒有訊號，或 overview 抓不到'}
              >
                <Briefcase size={12} /> 持倉 {heldCount}
              </FilterChip>

              <span className="mx-1 hidden h-5 w-px sm:block" style={{ background: C.border }} aria-hidden />
              <span className="font-mono text-[11px] uppercase tracking-[0.12em]" style={{ color: C.muted }}>Sort</span>
              {SORTS.map((o) => {
                const Icon = o.icon;
                return (
                  <FilterChip key={o.key} active={sort === o.key} color={C.accent} onClick={() => setSort(o.key)} title={o.hint}>
                    <Icon size={12} /> {o.label}
                  </FilterChip>
                );
              })}

              <label className="relative ml-auto block">
                <span className="sr-only">搜尋代號、公司或產業</span>
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: C.muted }} />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="代號 / 公司 / 產業"
                  className="w-52 border bg-transparent py-1.5 pl-8 pr-2 font-mono text-[13px] outline-none transition-colors duration-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--ops-accent)]"
                  style={{ borderColor: C.border, color: C.text }}
                />
              </label>
              <span className="font-mono text-[12px] tabular-nums" style={{ color: C.muted }}>
                {filtered.length} / {items.length} 檔
                {anyFilter && (
                  <button
                    type="button"
                    onClick={() => { setSetupFilter(null); setDirFilter('all'); setHeldOnly(false); setSearch(''); }}
                    className="ml-2 cursor-pointer underline-offset-2 hover:underline"
                    style={{ color: C.accent }}
                  >
                    清除
                  </button>
                )}
              </span>
            </div>

            {/* Sections */}
            {filtered.length === 0 ? (
              <Panel>
                <Empty>{items.length === 0 ? '全部追蹤股都沒有觸發訊號' : '沒有符合篩選的股票'}</Empty>
              </Panel>
            ) : sort !== 'tier' ? (
              <Panel
                label={sort === 'move' ? 'RANKED / 依今日漲跌幅' : 'RANKED / 依趨勢強度'}
                status={
                  <span className="font-mono text-[11px]" style={{ color: C.muted }}>
                    {SORTS.find((o) => o.key === sort)?.hint}
                  </span>
                }
              >
                <ul className="-mx-4 -mb-4 border-t" style={{ borderColor: C.border }}>
                  {filtered.map((item, i) => (
                    <SignalRow
                      key={item.ticker}
                      item={item}
                      rank={i + 1}
                      held={heldSet.has(item.ticker)}
                      expanded={expandedTicker === item.ticker}
                      onToggle={() => toggleExpanded(item.ticker)}
                      compact={(item.summary?.tier ?? 9) >= 3}
                    />
                  ))}
                </ul>
              </Panel>
            ) : (
              SECTIONS.map((sec) => (
                <SectionPanel
                  key={sec.key}
                  section={sec}
                  rows={bySection[sec.key]}
                  expandedTicker={expandedTicker}
                  onToggle={toggleExpanded}
                  heldSet={heldSet}
                  forceOpen={!!anyFilter}
                />
              ))
            )}
          </>
        )}
      </div>
    </div>
  );
}
