import { useState, useEffect } from 'react';
import axios from 'axios';
import {
  TrendingUp, TrendingDown, Activity, RefreshCw, Loader2,
  AlertTriangle,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { CHART_GRID } from '../components/chartTheme';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(val, unit) {
  if (val == null) return '—';
  const n = Number(val);
  if (unit === '%') return `${n.toFixed(3)}%`;
  if (unit === '$/bbl' || unit === '$/oz' || unit === '$/MMBtu')
    return `$${n.toFixed(2)}`;
  return n.toFixed(2);
}

function ChangeChip({ val }) {
  if (val == null) return <span className="text-[var(--text-muted)]">—</span>;
  const pos = val >= 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-semibold
      ${pos ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
      {pos ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {pos ? '+' : ''}{val.toFixed(2)}%
    </span>
  );
}

function MacroCard({ item }) {
  return (
    <div className="glass rounded-xl p-4 flex flex-col gap-1 hover:border-[rgba(255,255,255,0.12)] transition-colors">
      <span className="text-xs text-[var(--text-muted)] font-medium">{item.label}</span>
      <span className="text-xl font-bold font-mono tabular-nums">
        {fmt(item.price, item.unit)}{item.unit === '%' ? '' : item.unit ? ` ${item.unit.replace(/\$.*/, '').trim()}` : ''}
      </span>
      <div className="flex items-center justify-between mt-0.5">
        <ChangeChip val={item.change_pct} />
        <span className="text-[10px] text-[var(--text-muted)]">{item.last_date}</span>
      </div>
    </div>
  );
}

const HISTORY_SYMBOLS = [
  { symbol: '^TNX', label: '10Y Treasury', color: 'var(--accent-cyan)' },
  { symbol: '^IRX', label: '3M T-Bill',    color: 'var(--accent-purple)' },
  { symbol: 'CL=F', label: 'Crude Oil',    color: 'var(--accent-amber)' },
];

function HistoryChart({ symbol, label, color }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    axios.get(`/api/macro/history/${symbol}`)
      .then(r => setData(r.data.history || []))
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [symbol]);

  if (loading) return (
    <div className="glass rounded-xl p-4 flex items-center justify-center h-40">
      <Loader2 size={20} className="animate-spin text-[var(--text-muted)]" />
    </div>
  );

  return (
    <div className="glass rounded-xl p-4">
      <p className="text-xs font-semibold text-[var(--text-muted)] mb-3 uppercase tracking-wider">{label} — 1Y</p>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tick={{ fill: 'var(--text-muted)', fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval={Math.floor(data.length / 4)}
            tickFormatter={d => d?.slice(5)}
          />
          <YAxis
            tick={{ fill: 'var(--text-muted)', fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            domain={['auto', 'auto']}
            width={34}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--glass-border)',
              borderRadius: 8,
              fontSize: 11,
              color: 'var(--text-primary)',
            }}
            formatter={v => [v?.toFixed(3), label]}
            labelFormatter={d => d}
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3, fill: color }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Macro() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = (bust = false) => {
    if (bust) setRefreshing(true);
    else setLoading(true);
    const req = bust
      ? axios.delete('/api/macro/cache').then(() => axios.get('/api/macro/overview'))
      : axios.get('/api/macro/overview');
    req
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => { setLoading(false); setRefreshing(false); });
  };

  useEffect(() => { load(); }, []);

  const byType = (type) => (data?.items || []).filter(i => i.type === type);
  const yc = data?.yield_curve;

  return (
    <div className="container py-6 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Activity size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold">Macro Overview</h1>
            <p className="text-sm text-[var(--text-muted)]">Treasury yields · Commodities · DXY · VIX</p>
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
          <span className="text-sm">Fetching macro data…</span>
        </div>
      )}

      {!loading && data && (
        <>
          {/* Yield curve banner */}
          {yc && (
            <div className={`glass rounded-xl px-5 py-4 flex items-center gap-4 border
              ${yc.inverted_10y_3m
                ? 'border-[rgba(248,113,113,0.3)] bg-[rgba(248,113,113,0.05)]'
                : 'border-[rgba(52,211,153,0.2)] bg-[rgba(52,211,153,0.04)]'
              }`}
            >
              {yc.inverted_10y_3m
                ? <AlertTriangle size={18} className="text-[var(--accent-danger)] shrink-0" />
                : <TrendingUp size={18} className="text-[var(--accent-success)] shrink-0" />
              }
              <div>
                <p className="text-sm font-semibold">
                  Yield Curve {yc.inverted_10y_3m ? 'Inverted' : 'Normal'}
                </p>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">
                  10Y – 3M spread: <span className="font-mono font-semibold">{yc.spread_10y_3m > 0 ? '+' : ''}{yc.spread_10y_3m}%</span>
                  &nbsp;·&nbsp;
                  10Y – 5Y spread: <span className="font-mono font-semibold">{yc.spread_10y_5y > 0 ? '+' : ''}{yc.spread_10y_5y}%</span>
                </p>
              </div>
            </div>
          )}

          {/* Treasury yields */}
          <section>
            <p className="text-xs text-[var(--text-muted)] font-semibold uppercase tracking-widest mb-3">Treasury Yields</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {byType('yield').map(item => <MacroCard key={item.symbol} item={item} />)}
            </div>
          </section>

          {/* Commodities */}
          <section>
            <p className="text-xs text-[var(--text-muted)] font-semibold uppercase tracking-widest mb-3">Commodities</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {byType('commodity').map(item => <MacroCard key={item.symbol} item={item} />)}
            </div>
          </section>

          {/* FX + Volatility */}
          <section>
            <p className="text-xs text-[var(--text-muted)] font-semibold uppercase tracking-widest mb-3">FX / Volatility</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[...byType('currency'), ...byType('volatility')].map(item => (
                <MacroCard key={item.symbol} item={item} />
              ))}
            </div>
          </section>

          {/* History charts */}
          <section>
            <p className="text-xs text-[var(--text-muted)] font-semibold uppercase tracking-widest mb-3">1-Year Trend</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {HISTORY_SYMBOLS.map(s => (
                <HistoryChart key={s.symbol} symbol={s.symbol} label={s.label} color={s.color} />
              ))}
            </div>
          </section>

          <p className="text-[10px] text-[var(--text-muted)] text-right">
            Data via yfinance · cached 30 min · last fetch {data.fetched_at?.slice(0, 19).replace('T', ' ')} UTC
          </p>
        </>
      )}
    </div>
  );
}
