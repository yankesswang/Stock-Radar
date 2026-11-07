import { useEffect, useMemo, useState } from 'react';
import axios from '../lib/apiClient';
import { Link } from 'wouter';
import { Layers, ArrowUpRight, RefreshCw, ChevronDown, Briefcase, Eye } from 'lucide-react';

/**
 * Sector price-trend strip for the home page.
 *
 * Answers "what is the market actually doing today" at a glance: all 11 SPDR
 * sector ETFs sorted strongest → weakest for the selected window, each with a
 * price trend line over that same window and a diverging bar for magnitude.
 * Data comes from /api/sectors/rs (cached 30 min server-side).
 *
 * Each row expands into the single stocks behind the move: the ETF's top
 * constituents quoted over the same window, plus my own positions in that
 * sector (/api/sectors/{symbol}/holdings). Sector-level exposure comes from
 * /api/sectors/exposure so the collapsed rows already show where I am invested.
 */

const GROUP_LABEL = { cyclical: '循環', defensive: '防禦', sensitive: '敏感' };
const GROUP_COLOR = {
  cyclical:  'var(--accent-amber)',
  defensive: 'var(--accent-success)',
  sensitive: 'var(--accent-cyan)',
};

// value key on the payload + how many trading days of price history to draw
const WINDOWS = [
  { key: 'today', label: '今日', sparkDays: 10 },
  { key: '1w',    label: '1週',  sparkDays: 10 },
  { key: '1m',    label: '1月',  sparkDays: 21 },
  { key: '3m',    label: '3月',  sparkDays: 63 },
];

const UP   = '#059669';
const DOWN = '#dc2626';

function returnFor(item, windowKey) {
  if (windowKey === 'today') return item.change_pct ?? null;
  return item.returns?.[windowKey] ?? null;
}

function fmtPct(v, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${v >= 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
}

function TrendLine({ data, positive, width = 76, height = 24 }) {
  if (!data || data.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 2;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={width} height={height} className="shrink-0 block">
      <polyline
        points={pts}
        fill="none"
        stroke={positive ? UP : DOWN}
        strokeWidth="1.4"
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity="0.85"
      />
    </svg>
  );
}

/** Dot + weight for a name I hold; hollow eye for one I only watch. */
function OwnedBadge({ owned }) {
  if (!owned) return null;
  if (!owned.held) {
    return (
      <span
        className="inline-flex items-center gap-0.5 rounded-full bg-[rgba(148,163,184,0.14)] px-1 py-px text-[9px] font-semibold text-[var(--text-muted)]"
        title={`追蹤中：${(owned.watchlists || []).join('、')}`}
      >
        <Eye size={8} />追蹤
      </span>
    );
  }
  const weight = owned.cost_weight_pct;
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded-full bg-[rgba(0,82,255,0.1)] px-1 py-px text-[9px] font-bold text-[var(--accent-cyan)]"
      title={`${(owned.portfolios || []).join('、')} · ${owned.shares} 股 · 佔組合成本 ${weight ?? '—'}%`}
    >
      <Briefcase size={8} />
      {weight != null ? `${weight}%` : '持有'}
    </span>
  );
}

/** One constituent / position line inside an expanded sector. */
function StockLine({ item, windowKey, sparkDays }) {
  const value = returnFor(item, windowKey);
  const pos = (value ?? 0) >= 0;
  const spark = (item.price_spark || []).slice(-sparkDays);
  const sparkUp = spark.length >= 2 ? spark[spark.length - 1] >= spark[0] : pos;

  return (
    <Link
      href={`/stock/${encodeURIComponent(item.symbol)}`}
      className="flex items-center gap-2 rounded-md py-[3px] pl-1 pr-1.5 transition-colors hover:bg-[rgba(0,82,255,0.05)]"
    >
      <span className="w-[42px] shrink-0 font-mono text-[11px] font-bold leading-tight text-[var(--text-primary)]">
        {item.symbol}
      </span>
      <span className="hidden min-w-0 flex-1 truncate text-[10px] text-[var(--text-muted)] sm:block">
        {item.name}
      </span>
      <OwnedBadge owned={item.owned} />
      {item.weight_pct != null && (
        <span className="w-[34px] shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-muted)]">
          {item.weight_pct}%
        </span>
      )}
      <TrendLine data={spark} positive={sparkUp} width={44} height={16} />
      <span
        className="w-[50px] shrink-0 text-right font-mono text-[11px] font-bold tabular-nums"
        style={{ color: value == null ? 'var(--text-muted)' : pos ? UP : DOWN }}
      >
        {fmtPct(value)}
      </span>
    </Link>
  );
}

/** Lazily-loaded constituents for one sector; fetched on first expand. */
function HoldingsPanel({ symbol, windowKey, sparkDays }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [showAllMine, setShowAllMine] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(false);
    axios.get(`/api/sectors/${encodeURIComponent(symbol)}/holdings`)
      .then(r => alive && setData(r.data))
      .catch(() => alive && setError(true));
    return () => { alive = false; };
  }, [symbol]);

  if (error) {
    return (
      <p className="py-3 pl-3 text-[11px] text-[var(--text-muted)]">成分股資料暫時無法取得</p>
    );
  }
  if (!data) {
    return (
      <div className="space-y-1 py-2 pl-3 pr-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-[18px] animate-pulse rounded bg-[var(--bg-tertiary)] opacity-60" />
        ))}
      </div>
    );
  }

  const items = data.items || [];
  const mine = data.my_others || [];
  const shownMine = showAllMine ? mine : mine.slice(0, 5);
  const summary = data.my_summary || {};

  return (
    <div className="mb-1 ml-3 border-l border-[var(--glass-border)] pb-1 pl-2">
      {items.length > 0 ? (
        <>
          <div className="flex items-center justify-between gap-2 py-1 pl-1 pr-1.5">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-[var(--text-muted)]">
              前 {items.length} 大成分股
            </span>
            <span className="font-mono text-[9px] text-[var(--text-muted)]">
              {summary.held_count > 0 && (
                <span className="mr-2 text-[var(--accent-cyan)]">
                  我持有 {summary.held_count} 檔
                  {summary.cost_weight_pct != null && ` · 倉位 ${summary.cost_weight_pct}%`}
                </span>
              )}
              覆蓋 {data.coverage_pct}%
            </span>
          </div>
          {items.map(item => (
            <StockLine key={item.symbol} item={item} windowKey={windowKey} sparkDays={sparkDays} />
          ))}
        </>
      ) : (
        <p className="py-1.5 pl-1 text-[10px] text-[var(--text-muted)]">成分股資料暫時無法取得</p>
      )}

      {mine.length > 0 && (
        <>
          <div className="mt-1.5 flex items-center justify-between gap-2 border-t border-[var(--glass-border)] py-1 pl-1 pr-1.5 pt-1.5">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-[var(--text-muted)]">
              我的其他部位
            </span>
            <span className="font-mono text-[9px] text-[var(--text-muted)]">
              {mine.length} 檔（不在前 {items.length} 大內）
            </span>
          </div>
          {shownMine.map(item => (
            <StockLine key={item.symbol} item={item} windowKey={windowKey} sparkDays={sparkDays} />
          ))}
          {mine.length > shownMine.length && (
            <button
              onClick={() => setShowAllMine(true)}
              className="mt-0.5 pl-1 text-[10px] font-semibold text-[var(--accent-cyan)] hover:underline"
            >
              +{mine.length - shownMine.length} 檔
            </button>
          )}
        </>
      )}
    </div>
  );
}

function SectorRow({ item, windowKey, sparkDays, maxAbs, exposure, expanded, onToggle }) {
  const value = returnFor(item, windowKey);
  const pos = (value ?? 0) >= 0;
  const color = pos ? UP : DOWN;
  const spark = (item.price_spark || []).slice(-sparkDays);
  // the line is coloured by its own start→end direction, not by `value`: over the
  // 今日 window those can legitimately disagree (down today, up on the week)
  const sparkUp = spark.length >= 2 ? spark[spark.length - 1] >= spark[0] : pos;
  // half-width bar: 50% of the track is the largest move in the group
  const barW = value == null || !maxAbs ? 0 : Math.min(50, (Math.abs(value) / maxAbs) * 50);

  return (
    <div className={expanded ? 'rounded-lg bg-[rgba(0,82,255,0.025)]' : ''}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
        }}
        className="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1.5 transition-colors hover:bg-[rgba(0,82,255,0.04)] sm:gap-2.5 sm:px-2"
      >
        <span
          className="h-7 w-1 shrink-0 rounded-full"
          style={{ background: GROUP_COLOR[item.group] }}
          title={GROUP_LABEL[item.group]}
        />
        <div className="w-[66px] shrink-0 sm:w-[74px]">
          <p className="text-xs font-semibold leading-tight text-[var(--text-primary)]">{item.zh}</p>
          <Link
            href={`/market/${encodeURIComponent(item.symbol)}`}
            onClick={e => e.stopPropagation()}
            className="font-mono text-[9px] leading-tight text-[var(--text-muted)] transition-colors hover:text-[var(--accent-cyan)]"
          >
            {item.symbol}
          </Link>
        </div>

        {/* how much of my own book sits in this sector — visible without expanding */}
        <span className="w-[46px] shrink-0 text-left">
          {exposure?.held_count > 0 && (
            <span
              className="inline-flex items-center gap-0.5 rounded-full bg-[rgba(0,82,255,0.1)] px-1 py-px font-mono text-[9px] font-bold text-[var(--accent-cyan)]"
              title={
                exposure.cost_weight_pct != null
                  ? `我持有 ${exposure.held_count} 檔 · 佔組合成本 ${exposure.cost_weight_pct}%`
                  : `我持有 ${exposure.held_count} 檔`
              }
            >
              <Briefcase size={8} />
              {exposure.held_count}
              {exposure.cost_weight_pct != null && `·${Math.round(exposure.cost_weight_pct)}%`}
            </span>
          )}
        </span>

        <TrendLine data={spark} positive={sparkUp} />

        <div className="relative h-2 min-w-[32px] flex-1 rounded-full bg-[rgba(148,163,184,0.12)]">
          <div className="absolute inset-y-[-3px] left-1/2 w-px bg-[rgba(148,163,184,0.5)]" />
          <div
            className="absolute inset-y-0 rounded-full transition-all duration-300"
            style={{
              left: pos ? '50%' : `${50 - barW}%`,
              width: `${barW}%`,
              background: color,
              opacity: 0.85,
            }}
          />
        </div>

        <span
          className="w-[52px] shrink-0 text-right font-mono text-xs font-bold tabular-nums sm:w-[56px]"
          style={{ color: value == null ? 'var(--text-muted)' : color }}
        >
          {fmtPct(value)}
        </span>
        <ChevronDown
          size={13}
          className={`shrink-0 text-[var(--text-muted)] transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
        />
      </div>

      {expanded && (
        <HoldingsPanel symbol={item.symbol} windowKey={windowKey} sparkDays={sparkDays} />
      )}
    </div>
  );
}

function GroupChip({ group, value }) {
  if (value == null) return null;
  const pos = value >= 0;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-[var(--glass-border)] bg-[var(--bg-primary)] px-2 py-0.5 text-[10px]">
      <span className="h-2 w-2 rounded-full" style={{ background: GROUP_COLOR[group] }} />
      <span className="text-[var(--text-muted)]">{GROUP_LABEL[group]}</span>
      <span className="font-mono font-bold" style={{ color: pos ? UP : DOWN }}>{fmtPct(value, 1)}</span>
    </span>
  );
}

export default function SectorPulse() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [win, setWin] = useState('today');
  const [exposure, setExposure] = useState({});
  const [openSymbol, setOpenSymbol] = useState(null);

  const load = (bust = false) => {
    setLoading(true);
    const req = bust
      ? axios.delete('/api/sectors/cache').then(() => axios.get('/api/sectors/rs'))
      : axios.get('/api/sectors/rs');
    req.then(r => setData(r.data))
       .catch(() => setData(null))
       .finally(() => setLoading(false));

    axios.get('/api/sectors/exposure')
      .then(r => setExposure(r.data?.sectors || {}))
      .catch(() => setExposure({}));
  };

  useEffect(() => { load(); }, []);

  const items = data?.items || [];
  const winMeta = WINDOWS.find(w => w.key === win) || WINDOWS[0];

  const sorted = useMemo(() => {
    return [...items]
      .filter(i => i.ok)
      .sort((a, b) => (returnFor(b, win) ?? -999) - (returnFor(a, win) ?? -999));
  }, [items, win]);

  const values = sorted.map(i => returnFor(i, win)).filter(v => v != null);
  const maxAbs = values.length ? Math.max(...values.map(Math.abs)) : 1;
  const upCount = values.filter(v => v > 0).length;
  const best = sorted[0];
  const worst = sorted[sorted.length - 1];

  const groupAvg = useMemo(() => {
    const acc = {};
    ['cyclical', 'defensive', 'sensitive'].forEach(g => {
      const vs = sorted.filter(i => i.group === g).map(i => returnFor(i, win)).filter(v => v != null);
      acc[g] = vs.length ? vs.reduce((s, v) => s + v, 0) / vs.length : null;
    });
    return acc;
  }, [sorted, win]);

  // split into two columns on wide screens, keeping the ranking readable
  const half = Math.ceil(sorted.length / 2);
  const columns = [sorted.slice(0, half), sorted.slice(half)];

  return (
    <section className="container rounded-[28px] border border-[var(--glass-border)] bg-[var(--bg-primary)] p-4 shadow-[0_18px_45px_rgba(15,23,42,0.05)] sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-2 rounded-full border border-[rgba(0,82,255,0.18)] bg-[rgba(0,82,255,0.06)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            <Layers size={12} />
            行業板塊
          </div>
          {!loading && values.length > 0 && (
            <>
              <span className="text-xs text-[var(--text-muted)]">
                <span className="font-bold text-[var(--text-primary)]">{upCount}</span>
                <span className="mx-0.5">/</span>
                {values.length} 上漲
              </span>
              {best && worst && best !== worst && (
                <span className="hidden text-xs text-[var(--text-muted)] sm:inline">
                  領漲 <span className="font-semibold text-[var(--text-primary)]">{best.zh}</span>
                  <span className="ml-1 font-bold" style={{ color: UP }}>{fmtPct(returnFor(best, win))}</span>
                  <span className="mx-1.5 opacity-40">·</span>
                  領跌 <span className="font-semibold text-[var(--text-primary)]">{worst.zh}</span>
                  <span className="ml-1 font-bold" style={{ color: DOWN }}>{fmtPct(returnFor(worst, win))}</span>
                </span>
              )}
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-full border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-0.5">
            {WINDOWS.map(w => (
              <button
                key={w.key}
                onClick={() => setWin(w.key)}
                className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                  win === w.key
                    ? 'bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => load(true)}
            disabled={loading}
            className="btn btn-ghost px-2.5 py-1.5 text-xs"
            title="重新抓取板塊資料"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
          <Link
            href="/sectors"
            className="inline-flex items-center gap-0.5 text-xs font-semibold text-[var(--text-muted)] transition-colors hover:text-[var(--accent-cyan)]"
          >
            RS 分析 <ArrowUpRight size={12} />
          </Link>
        </div>
      </div>

      {loading && !sorted.length ? (
        <div className="grid gap-1 sm:grid-cols-2 sm:gap-x-6">
          {Array.from({ length: 11 }).map((_, i) => (
            <div key={i} className="h-[38px] animate-pulse rounded-lg bg-[var(--bg-tertiary)] opacity-60" />
          ))}
        </div>
      ) : !sorted.length ? (
        <p className="py-6 text-center text-sm text-[var(--text-muted)]">板塊資料暫時無法取得</p>
      ) : (
        <>
          <div className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
            {columns.map((col, ci) => (
              <div key={ci}>
                {col.map(item => (
                  <SectorRow
                    key={item.symbol}
                    item={item}
                    windowKey={win}
                    sparkDays={winMeta.sparkDays}
                    maxAbs={maxAbs}
                    exposure={exposure[item.symbol]}
                    expanded={openSymbol === item.symbol}
                    onToggle={() => setOpenSymbol(s => (s === item.symbol ? null : item.symbol))}
                  />
                ))}
              </div>
            ))}
          </div>

          <div className="mt-3.5 flex flex-wrap items-center gap-2 border-t border-[var(--glass-border)] pt-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)]">
              類型平均
            </span>
            {['cyclical', 'sensitive', 'defensive'].map(g => (
              <GroupChip key={g} group={g} value={groupAvg[g]} />
            ))}
            <span className="ml-auto text-[10px] text-[var(--text-muted)]">
              點板塊看成分股 · <Briefcase size={9} className="inline -mt-px" /> = 我的持股（佔組合成本）·
              走勢線 = 近 {winMeta.sparkDays} 個交易日收盤價
            </span>
          </div>
        </>
      )}
    </section>
  );
}
