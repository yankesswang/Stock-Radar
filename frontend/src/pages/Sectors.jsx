import { useState, useEffect, Fragment } from 'react';
import axios from 'axios';
import {
  TrendingUp, TrendingDown, Layers, RefreshCw, Loader2, Minus,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceLine,
} from 'recharts';
import { CHART_GRID, CHART_REFERENCE } from '../components/chartTheme';

// ── Helpers ───────────────────────────────────────────────────────────────────

const PERIOD_LABELS = { '1w': '1週', '1m': '1月', '3m': '3月', '6m': '6月' };

const GROUP_LABEL = {
  cyclical: '循環',
  defensive: '防禦',
  sensitive: '敏感',
};
const GROUP_COLOR = {
  cyclical: 'var(--accent-amber)',
  defensive: 'var(--accent-success)',
  sensitive: 'var(--accent-cyan)',
};

function pct(v) {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

// map an RS value (excess return, pp) to a red↔green background
function rsBg(v) {
  if (v == null) return 'transparent';
  const clamped = Math.max(-8, Math.min(8, v));
  const t = Math.abs(clamped) / 8;
  if (clamped >= 0) return `rgba(52,211,153,${(0.08 + t * 0.32).toFixed(3)})`;
  return `rgba(248,113,113,${(0.08 + t * 0.32).toFixed(3)})`;
}

function RatingBadge({ rating }) {
  if (rating == null) return <span className="text-[var(--text-muted)]">—</span>;
  const strong = rating >= 67;
  const weak = rating <= 33;
  const color = strong ? 'var(--accent-success)' : weak ? 'var(--accent-danger)' : 'var(--accent-amber)';
  return (
    <span
      className="inline-flex items-center justify-center min-w-[34px] px-2 py-0.5 rounded-md text-xs font-bold font-mono tabular-nums"
      style={{ color, background: `${color}1a`, border: `1px solid ${color}33` }}
    >
      {rating}
    </span>
  );
}

function TrendIcon({ v }) {
  if (v == null) return <Minus size={12} className="text-[var(--text-muted)]" />;
  if (v > 0.1) return <TrendingUp size={12} className="text-[var(--accent-success)]" />;
  if (v < -0.1) return <TrendingDown size={12} className="text-[var(--accent-danger)]" />;
  return <Minus size={12} className="text-[var(--text-muted)]" />;
}

// tiny inline sparkline for the RS line
function MiniSpark({ data, color }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 84, h = 24;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={w} height={h} className="overflow-visible">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ── RS line detail chart (loaded on row expand) ─────────────────────────────────

const BENCHMARK_CHOICES = [
  { symbol: 'SPY', label: 'S&P 500' },
  { symbol: 'QQQ', label: 'Nasdaq 100' },
  { symbol: 'DIA', label: 'Dow 30' },
  { symbol: 'IWM', label: 'Russell 2000' },
  { symbol: 'RSP', label: 'S&P 500 等權' },
  { symbol: 'ACWI', label: '全球股市' },
];

function RsLineChart({ symbol, label }) {
  const [data, setData] = useState(null);
  const [bench, setBench] = useState('SPY');

  useEffect(() => {
    let alive = true;
    setData(null);
    axios.get(`/api/sectors/rs-line/${symbol}`, { params: { benchmark: bench } })
      .then(r => { if (alive) setData(r.data.rs_line || []); })
      .catch(() => { if (alive) setData([]); });
    return () => { alive = false; };
  }, [symbol, bench]);

  return (
    <div className="pt-2">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <p className="text-[11px] text-[var(--text-muted)]">
          {label} <span className="font-mono">{symbol}</span> / <span className="font-mono">{bench}</span> 相對強弱線 (起點 = 100，6 個月) — 線走高代表贏過所選指數
        </p>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-[var(--text-muted)] mr-0.5">基準：</span>
          {BENCHMARK_CHOICES.map(b => (
            <button
              key={b.symbol}
              onClick={(e) => { e.stopPropagation(); setBench(b.symbol); }}
              title={b.label}
              className={`px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold transition-colors ${
                bench === b.symbol
                  ? 'bg-[rgba(0,82,255,0.16)] text-[var(--accent-cyan)] border border-[rgba(0,82,255,0.3)]'
                  : 'text-[var(--text-muted)] border border-[var(--glass-border)] hover:text-[var(--text-primary)]'
              }`}
            >
              {b.symbol}
            </button>
          ))}
        </div>
      </div>
      {data === null ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 size={18} className="animate-spin text-[var(--text-muted)]" />
        </div>
      ) : (
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" />
          <ReferenceLine y={100} stroke={CHART_REFERENCE} strokeDasharray="4 4" />
          <XAxis dataKey="date" tick={{ fill: 'var(--text-muted)', fontSize: 9 }}
            tickLine={false} axisLine={false}
            interval={Math.floor(data.length / 5)} tickFormatter={d => d?.slice(5)} />
          <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 9 }} tickLine={false}
            axisLine={false} domain={['auto', 'auto']} width={38} />
          <Tooltip
            contentStyle={{
              background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)',
              borderRadius: 8, fontSize: 11, color: 'var(--text-primary)',
            }}
            formatter={v => [v?.toFixed(2), 'RS']} />
          <Line type="monotone" dataKey="rs" stroke="var(--accent-cyan)" strokeWidth={1.75}
            dot={false} activeDot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
      )}
    </div>
  );
}

// ── Pairwise sector compare (A / B ratio line) ──────────────────────────────────

function PairCompare({ sectors }) {
  const [a, setA] = useState('XLY');
  const [b, setB] = useState('XLV');
  const [data, setData] = useState(null);

  useEffect(() => {
    if (a === b) { setData([]); return; }
    let alive = true;
    setData(null);
    axios.get(`/api/sectors/rs-line/${a}`, { params: { benchmark: b } })
      .then(r => { if (alive) setData(r.data.rs_line || []); })
      .catch(() => { if (alive) setData([]); });
    return () => { alive = false; };
  }, [a, b]);

  const meta = (sym) => sectors.find(s => s.symbol === sym) || { zh: '', label: sym };
  const last = data && data.length ? data[data.length - 1].rs : null;
  // rebased to 100 → >100 means A outperformed B over the window
  const aWins = last != null && last >= 100;
  const change = last != null ? last - 100 : null;

  const Select = ({ value, onChange }) => (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-lg px-2.5 py-1.5 text-sm font-mono font-semibold text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-cyan)]"
    >
      {sectors.map(s => (
        <option key={s.symbol} value={s.symbol}>{s.symbol} · {s.zh}</option>
      ))}
    </select>
  );

  return (
    <div className="glass rounded-xl p-4">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">板塊兩兩對比</span>
        <div className="flex items-center gap-2">
          <Select value={a} onChange={setA} />
          <span className="text-[var(--text-muted)] font-bold">÷</span>
          <Select value={b} onChange={setB} />
          <button
            onClick={() => { setA(b); setB(a); }}
            title="對調"
            className="px-2 py-1.5 rounded-lg border border-[var(--glass-border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          >
            <RefreshCw size={13} />
          </button>
        </div>
        {last != null && a !== b && (
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-[var(--text-muted)]">近 6 月：</span>
            <span
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-sm font-bold"
              style={{
                color: aWins ? 'var(--accent-success)' : 'var(--accent-danger)',
                background: aWins ? 'rgba(52,211,153,0.12)' : 'rgba(248,113,113,0.12)',
              }}
            >
              {aWins ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
              <span className="font-mono">{a}</span>
              {aWins ? '領先' : '落後'}
              <span className="font-mono">{b}</span>
              <span className="opacity-70">{change >= 0 ? '+' : ''}{change.toFixed(1)}%</span>
            </span>
          </div>
        )}
      </div>

      <p className="text-[11px] text-[var(--text-muted)] mb-2">
        <span className="font-mono">{a}</span> {meta(a).zh} ÷ <span className="font-mono">{b}</span> {meta(b).zh} 比值線 (起點 = 100，6 個月) — 線走高代表 <span className="font-mono">{a}</span> 相對走強
      </p>

      {a === b ? (
        <div className="flex items-center justify-center h-40 text-sm text-[var(--text-muted)]">請選擇兩個不同板塊</div>
      ) : data === null ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 size={18} className="animate-spin text-[var(--text-muted)]" />
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
            <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" />
            <ReferenceLine y={100} stroke={CHART_REFERENCE} strokeDasharray="4 4"
              label={{ value: '持平', fill: 'var(--text-muted)', fontSize: 9, position: 'insideRight' }} />
            <XAxis dataKey="date" tick={{ fill: 'var(--text-muted)', fontSize: 9 }}
              tickLine={false} axisLine={false}
              interval={Math.floor(data.length / 6)} tickFormatter={d => d?.slice(5)} />
            <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 9 }} tickLine={false}
              axisLine={false} domain={['auto', 'auto']} width={40} />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)',
                borderRadius: 8, fontSize: 11, color: 'var(--text-primary)',
              }}
              formatter={v => [v?.toFixed(2), `${a}/${b}`]} />
            <Line type="monotone" dataKey="rs" stroke="var(--accent-cyan)" strokeWidth={2}
              dot={false} activeDot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Sectors() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState(null);

  const load = (bust = false) => {
    if (bust) setRefreshing(true); else setLoading(true);
    const req = bust
      ? axios.delete('/api/sectors/cache').then(() => axios.get('/api/sectors/rs'))
      : axios.get('/api/sectors/rs');
    req
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => { setLoading(false); setRefreshing(false); });
  };

  useEffect(() => { load(); }, []);

  const periods = data?.periods || ['1w', '1m', '3m', '6m'];
  const items = data?.items || [];

  return (
    <div className="container py-6 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Layers size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold">板塊相對強弱 · Sector RS</h1>
            <p className="text-sm text-[var(--text-muted)]">
              11 檔 SPDR 板塊 ETF vs {data?.benchmark || 'SPY'} · 綠強紅弱
            </p>
          </div>
        </div>
        <button
          onClick={() => load(true)}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[var(--text-muted)] border border-[var(--glass-border)] rounded-lg hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)] transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {loading && (
        <div className="glass rounded-xl p-16 flex items-center justify-center gap-3 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-cyan)]" />
          <span className="text-sm">Fetching sector data…</span>
        </div>
      )}

      {!loading && data && (
        <>
          <PairCompare sectors={items} />

          <div className="glass rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] border-b border-[var(--glass-border)]">
                    <th className="text-left font-semibold px-4 py-3">板塊</th>
                    <th className="text-center font-semibold px-2 py-3">RS 評分</th>
                    <th className="text-right font-semibold px-3 py-3">價格</th>
                    <th className="text-right font-semibold px-3 py-3">今日</th>
                    {periods.map(p => (
                      <th key={p} className="text-right font-semibold px-3 py-3">
                        RS {PERIOD_LABELS[p] || p}
                      </th>
                    ))}
                    <th className="text-center font-semibold px-3 py-3">趨勢</th>
                    <th className="text-center font-semibold px-3 py-3 hidden md:table-cell">RS 線</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(it => {
                    const open = expanded === it.symbol;
                    const trendColor = it.rs_trend == null
                      ? 'var(--text-muted)'
                      : it.rs_trend >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
                    return (
                      <Fragment key={it.symbol}>
                        <tr
                          onClick={() => setExpanded(open ? null : it.symbol)}
                          className="border-b border-[rgba(255,255,255,0.04)] hover:bg-[rgba(255,255,255,0.02)] cursor-pointer transition-colors"
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2.5">
                              <span
                                className="w-1.5 h-8 rounded-full shrink-0"
                                style={{ background: GROUP_COLOR[it.group] }}
                                title={GROUP_LABEL[it.group]}
                              />
                              <div>
                                <div className="flex items-center gap-2">
                                  <span className="font-mono font-bold">{it.symbol}</span>
                                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.05)] text-[var(--text-muted)]">
                                    {GROUP_LABEL[it.group]}
                                  </span>
                                </div>
                                <div className="text-xs text-[var(--text-muted)]">{it.zh} · {it.label}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-2 py-3 text-center"><RatingBadge rating={it.rs_rating} /></td>
                          <td className="px-3 py-3 text-right font-mono tabular-nums">
                            {it.price != null ? `$${it.price.toFixed(2)}` : '—'}
                          </td>
                          <td className={`px-3 py-3 text-right font-mono tabular-nums text-xs ${
                            (it.change_pct ?? 0) >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                          }`}>
                            {pct(it.change_pct)}
                          </td>
                          {periods.map(p => (
                            <td key={p} className="px-3 py-3 text-right font-mono tabular-nums text-xs"
                              style={{ background: rsBg(it.rs?.[p]) }}>
                              {pct(it.rs?.[p])}
                            </td>
                          ))}
                          <td className="px-3 py-3">
                            <div className="flex items-center justify-center gap-1">
                              <TrendIcon v={it.rs_trend} />
                              <span className="text-xs font-mono tabular-nums" style={{ color: trendColor }}>
                                {it.rs_trend != null ? (it.rs_trend >= 0 ? '+' : '') + it.rs_trend : '—'}
                              </span>
                            </div>
                          </td>
                          <td className="px-3 py-3 hidden md:table-cell">
                            <div className="flex justify-center">
                              <MiniSpark
                                data={it.rs_line_spark}
                                color={(it.rs_trend ?? 0) >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)'}
                              />
                            </div>
                          </td>
                        </tr>
                        {open && (
                          <tr className="bg-[rgba(0,0,0,0.15)]">
                            <td colSpan={5 + periods.length} className="px-4 pb-4">
                              <RsLineChart symbol={it.symbol} label={it.zh} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-[var(--text-muted)]">
            <span className="font-semibold text-[var(--text-secondary)]">說明：</span>
            <span><b className="text-[var(--text-secondary)]">RS 評分</b> = 3 月相對強弱在 11 板塊中的百分位 (1–99，越高越強)</span>
            <span><b className="text-[var(--text-secondary)]">RS %</b> = 該期間報酬 − SPY 報酬 (百分點)</span>
            <span><b className="text-[var(--text-secondary)]">趨勢</b> = RS 線近 1 週斜率</span>
            <span className="flex items-center gap-1.5">
              {Object.entries(GROUP_LABEL).map(([k, v]) => (
                <span key={k} className="inline-flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: GROUP_COLOR[k] }} />
                  {v}
                </span>
              ))}
            </span>
          </div>

          <p className="text-[10px] text-[var(--text-muted)] text-right">
            Data via yfinance · cached 30 min · 點列可展開 RS 線 · last fetch {data.fetched_at?.slice(0, 19).replace('T', ' ')} UTC
          </p>
        </>
      )}
    </div>
  );
}
