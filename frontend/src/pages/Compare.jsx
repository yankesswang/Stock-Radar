import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link, useSearch, useLocation } from 'wouter';
import axios from 'axios';
import {
  ChevronUp, ChevronDown, ChevronsUpDown,
  Loader2, BarChart3, Table2,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine, LabelList,
} from 'recharts';

// ── Column definitions per tab ──────────────────────────────────────────────

const TABS = [
  {
    id: 'valuation',
    label: 'Valuation',
    columns: [
      { key: 'ticker',    label: 'Ticker',     path: null,          fmt: 'ticker' },
      { key: 'fwd_pe',    label: 'Fwd PE',     path: 'valuation',   fmt: 'num1' },
      { key: 'pe',        label: 'PE (TTM)',   path: 'valuation',   fmt: 'num1' },
      { key: 'ev_ebitda', label: 'EV/EBITDA',  path: 'valuation',   fmt: 'num1' },
      { key: 'peg',       label: 'PEG',        path: 'valuation',   fmt: 'num2' },
      { key: 'pb',        label: 'P/B',        path: 'valuation',   fmt: 'num2' },
      { key: 'ps',        label: 'P/S',        path: 'valuation',   fmt: 'num2' },
      { key: 'pfcf',      label: 'P/FCF',      path: 'valuation',   fmt: 'num1' },
    ],
  },
  {
    id: 'growth',
    label: 'Growth',
    columns: [
      { key: 'ticker',  label: 'Ticker',       path: null,     fmt: 'ticker' },
      { key: 'eps_fy',  label: 'EPS FY Est',   path: 'growth', fmt: 'pct', color: true },
      { key: 'eps_nfy', label: 'EPS Next FY',  path: 'growth', fmt: 'pct', color: true },
      { key: 'rev_fy',  label: 'Rev FY Est',   path: 'growth', fmt: 'pct', color: true },
      { key: 'rev_nfy', label: 'Rev Next FY',  path: 'growth', fmt: 'pct', color: true },
      { key: 'eps_5y',  label: 'EPS Fcst 5Y',  path: 'growth', fmt: 'pct', color: true },
      { key: 'rev_5y',  label: 'Rev Fcst 5Y',  path: 'growth', fmt: 'pct', color: true },
    ],
  },
  {
    id: 'profitability',
    label: 'Profitability',
    columns: [
      { key: 'ticker',       label: 'Ticker',      path: null,            fmt: 'ticker' },
      { key: 'gross_margin', label: 'Gross Margin',path: 'profitability', fmt: 'pct', color: true },
      { key: 'op_margin',    label: 'Op Margin',   path: 'profitability', fmt: 'pct', color: true },
      { key: 'net_margin',   label: 'Net Margin',  path: 'profitability', fmt: 'pct', color: true },
      { key: 'fcf_margin',   label: 'FCF Margin',  path: 'profitability', fmt: 'pct', color: true },
      { key: 'roe',          label: 'ROE',         path: 'profitability', fmt: 'pct', color: true },
      { key: 'roic',         label: 'ROIC',        path: 'profitability', fmt: 'pct', color: true },
      { key: 'roa',          label: 'ROA',         path: 'profitability', fmt: 'pct', color: true },
    ],
  },
  {
    id: 'analyst',
    label: 'Analyst',
    columns: [
      { key: 'ticker',      label: 'Ticker',    path: null,      fmt: 'ticker' },
      { key: 'target',      label: 'Target',    path: 'analyst', fmt: 'dollar' },
      { key: 'upside',      label: 'Upside %',  path: 'analyst', fmt: 'pct',   color: true },
      { key: 'target_high', label: 'High',      path: 'analyst', fmt: 'dollar' },
      { key: 'target_low',  label: 'Low',       path: 'analyst', fmt: 'dollar' },
      { key: 'consensus',   label: 'Consensus', path: 'analyst', fmt: 'str' },
      { key: 'beta',        label: 'Beta',      path: 'analyst', fmt: 'num2' },
      { key: 'return_52w',  label: '52W Return',path: 'analyst', fmt: 'pct',   color: true },
    ],
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtVal(value, fmt) {
  if (value == null) return '—';
  switch (fmt) {
    case 'num1':   return typeof value === 'number' ? value.toFixed(1) : value;
    case 'num2':   return typeof value === 'number' ? value.toFixed(2) : value;
    case 'pct':    return typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : value;
    case 'dollar': return typeof value === 'number' ? `$${value.toFixed(2)}` : value;
    case 'str':    return value;
    default:       return value;
  }
}

function colorClass(value, useColor) {
  if (!useColor || value == null || typeof value !== 'number') return '';
  return value >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]';
}

function getRawValue(row, col) {
  return col.path ? row[col.path]?.[col.key] : row[col.key];
}

// ── Sort icon ─────────────────────────────────────────────────────────────────

function SortIcon({ col, sortKey, sortDir }) {
  if (sortKey !== col.key) return <ChevronsUpDown size={13} className="opacity-30 ml-0.5" />;
  return sortDir === 'asc'
    ? <ChevronUp size={13} className="text-[var(--accent-purple)] ml-0.5" />
    : <ChevronDown size={13} className="text-[var(--accent-purple)] ml-0.5" />;
}

// ── Table view ────────────────────────────────────────────────────────────────

function MetricsTable({ columns, tickers }) {
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState('desc');

  const handleSort = (col) => {
    if (col.fmt === 'ticker') return;
    if (sortKey === col.key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(col.key);
      setSortDir('desc');
    }
  };

  const sorted = useMemo(() => [...tickers].sort((a, b) => {
    if (!sortKey) return 0;
    const col = columns.find(c => c.key === sortKey);
    if (!col) return 0;
    const av = getRawValue(a, col);
    const bv = getRawValue(b, col);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortDir === 'asc' ? av - bv : bv - av;
  }), [tickers, sortKey, sortDir, columns]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--glass-border)]">
            {columns.map(col => (
              <th
                key={col.key}
                onClick={() => handleSort(col)}
                className={`px-3 py-2.5 font-medium text-[var(--text-muted)] whitespace-nowrap select-none
                  ${col.fmt !== 'ticker' ? 'text-right cursor-pointer hover:text-[var(--text-primary)] transition-colors' : 'text-left'}
                `}
              >
                <span className="inline-flex items-center gap-0.5">
                  {col.label}
                  {col.fmt !== 'ticker' && <SortIcon col={col} sortKey={sortKey} sortDir={sortDir} />}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={row.ticker}
              className={`border-b border-[var(--glass-border)] hover:bg-[rgba(255,255,255,0.03)] transition-colors
                ${i % 2 !== 0 ? 'bg-[rgba(255,255,255,0.01)]' : ''}`}
            >
              {columns.map(col => {
                if (col.fmt === 'ticker') {
                  return (
                    <td key={col.key} className="px-3 py-2.5 font-medium">
                      <Link
                        href={`/stock/${row.ticker}`}
                        className="text-[var(--accent-cyan)] hover:text-[var(--accent-purple)] transition-colors font-semibold"
                      >
                        {row.ticker}
                      </Link>
                      <div className="text-xs text-[var(--text-muted)] truncate max-w-[140px]" title={row.company}>
                        {row.company}
                      </div>
                    </td>
                  );
                }
                const raw = getRawValue(row, col);
                const display = fmtVal(raw, col.fmt);
                const cls = colorClass(raw, col.color);
                return (
                  <td key={col.key} className={`px-3 py-2.5 text-right tabular-nums ${cls || 'text-[var(--text-primary)]'}`}>
                    {display === '—' ? <span className="text-[var(--text-muted)]">—</span> : display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Bar chart view ────────────────────────────────────────────────────────────

const GREEN  = 'var(--accent-success)';
const RED    = 'var(--accent-danger)';

// Tier colours: top 25% → upper-mid → lower-mid → bottom 25%
const TIER_COLORS = [
  'var(--accent-cyan)',    // top
  'var(--accent-success)', // upper-mid
  'var(--accent-amber)',   // lower-mid
  'var(--accent-danger)',  // bottom
];

function tierColor(index, total) {
  const pct = total <= 1 ? 0 : index / (total - 1);
  if (pct < 0.25) return TIER_COLORS[0];
  if (pct < 0.50) return TIER_COLORS[1];
  if (pct < 0.75) return TIER_COLORS[2];
  return TIER_COLORS[3];
}

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const { ticker, company, displayValue, fmt } = payload[0].payload;
  return (
    <div className="glass rounded-lg px-3 py-2 text-xs shadow-xl border border-[var(--glass-border)]">
      <p className="font-semibold text-[var(--text-primary)]">{ticker}</p>
      <p className="text-[var(--text-muted)] truncate max-w-[160px]">{company}</p>
      <p className="mt-1 font-mono font-bold" style={{ color: payload[0].fill }}>
        {displayValue}
      </p>
    </div>
  );
}

// Custom label rendered above/below each bar
function BarValueLabel(props) {
  const { x, y, width, height, value, index, chartData } = props;
  if (value == null) return null;
  const entry = chartData[index];
  const isNeg = value < 0;
  const labelY = isNeg ? y + height + 13 : y - 5;
  const fill = tierColor(index, chartData.length);
  return (
    <text
      x={x + width / 2}
      y={labelY}
      textAnchor="middle"
      fill={fill}
      fontSize={11}
      fontWeight={700}
      fontFamily="ui-monospace, monospace"
    >
      {entry?.displayValue ?? value}
    </text>
  );
}

function MetricsChart({ columns, tickers }) {
  const metricCols = columns.filter(c => c.fmt !== 'ticker' && c.fmt !== 'str');

  const [selectedKey, setSelectedKey] = useState(metricCols[0]?.key ?? null);

  useEffect(() => {
    setSelectedKey(metricCols[0]?.key ?? null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns]);

  const selectedCol = metricCols.find(c => c.key === selectedKey) ?? metricCols[0];

  const chartData = useMemo(() => {
    if (!selectedCol) return [];
    return tickers
      .map(row => {
        const raw = getRawValue(row, selectedCol);
        return {
          ticker: row.ticker,
          company: row.company,
          value: typeof raw === 'number' ? raw : null,
          displayValue: fmtVal(raw, selectedCol.fmt),
        };
      })
      .filter(d => d.value != null)
      .sort((a, b) => b.value - a.value);
  }, [tickers, selectedCol]);

  const hasNegative = chartData.some(d => d.value < 0);

  // Each bar 72px wide; extra room for top labels (36px top margin)
  const BAR_W    = 72;
  const chartW   = Math.max(640, chartData.length * BAR_W + 80);
  const chartH   = 480;

  if (!selectedCol) return (
    <div className="py-10 text-center text-sm text-[var(--text-muted)]">No chartable metrics</div>
  );

  return (
    <div className="space-y-4 px-3 py-3">
      {/* Metric selector */}
      <div className="flex flex-wrap gap-2">
        {metricCols.map(col => (
          <button
            key={col.key}
            onClick={() => setSelectedKey(col.key)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-all duration-150 border
              ${selectedKey === col.key
                ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)] border-[rgba(139,92,246,0.5)]'
                : 'text-[var(--text-muted)] border-[var(--glass-border)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
              }
            `}
          >
            {col.label}
          </button>
        ))}
      </div>

      {chartData.length === 0 ? (
        <div className="py-10 text-center text-sm text-[var(--text-muted)]">No data for this metric</div>
      ) : (
        <div className="overflow-x-auto">
          <div style={{ width: chartW, height: chartH }}>
            <BarChart
              width={chartW}
              height={chartH}
              data={chartData}
              margin={{ top: 36, right: 16, bottom: 40, left: 16 }}
              barCategoryGap="22%"
            >
              <XAxis
                dataKey="ticker"
                tick={{ fill: 'var(--text-secondary)', fontSize: 11, fontWeight: 600 }}
                tickLine={false}
                axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
              />
              <YAxis
                tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={v =>
                  selectedCol.fmt === 'pct'    ? `${v}%`   :
                  selectedCol.fmt === 'dollar' ? `$${v}`   :
                  v.toFixed(selectedCol.fmt === 'num2' ? 1 : 0)
                }
                width={38}
              />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ fill: 'rgba(255,255,255,0.04)' }}
              />
              {hasNegative && (
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="3 3" />
              )}
              <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={52}>
                <LabelList
                  dataKey="value"
                  content={(props) => (
                    <BarValueLabel
                      {...props}
                      chartData={chartData}
                      fmt={selectedCol.fmt}
                    />
                  )}
                />
                {chartData.map((entry, i) => (
                  <Cell
                    key={entry.ticker}
                    fill={tierColor(i, chartData.length)}
                    fillOpacity={0.85}
                  />
                ))}
              </Bar>
            </BarChart>
          </div>
        </div>
      )}

      <p className="text-xs text-[var(--text-muted)] text-right pr-1">
        Sorted by {selectedCol.label} — {chartData.length} stocks with data
      </p>
    </div>
  );
}

// ── View toggle button ────────────────────────────────────────────────────────

function ViewToggle({ view, onChange }) {
  return (
    <div className="flex items-center rounded-lg border border-[var(--glass-border)] overflow-hidden text-xs">
      <button
        onClick={() => onChange('table')}
        title="Table view"
        className={`flex items-center gap-1.5 px-2.5 py-1.5 transition-colors
          ${view === 'table'
            ? 'bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)]'
            : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'}
        `}
      >
        <Table2 size={13} />
        <span className="hidden sm:inline">Table</span>
      </button>
      <div className="w-px h-5 bg-[var(--glass-border)]" />
      <button
        onClick={() => onChange('chart')}
        title="Bar chart view"
        className={`flex items-center gap-1.5 px-2.5 py-1.5 transition-colors
          ${view === 'chart'
            ? 'bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)]'
            : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'}
        `}
      >
        <BarChart3 size={13} />
        <span className="hidden sm:inline">Chart</span>
      </button>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Compare() {
  const search = useSearch();
  const [, setLocation] = useLocation();

  const params      = new URLSearchParams(search);
  const activeGroup = params.get('group') || null;

  const [groups,    setGroups]    = useState([]);
  const [groupData, setGroupData] = useState(null);
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState(null);
  const [activeTab, setActiveTab] = useState('valuation');
  const [viewMode,  setViewMode]  = useState('table'); // 'table' | 'chart'

  // Fetch group list on mount
  useEffect(() => {
    axios.get('/api/industry-compare')
      .then(r => setGroups(r.data.groups || []))
      .catch(() => setGroups([]));
  }, []);

  // Fetch group data when activeGroup changes
  useEffect(() => {
    if (!activeGroup) { setGroupData(null); return; }
    setLoading(true);
    setError(null);
    axios.get(`/api/industry-compare?group=${encodeURIComponent(activeGroup)}`)
      .then(r => setGroupData(r.data))
      .catch(() => setError('Failed to load group data'))
      .finally(() => setLoading(false));
  }, [activeGroup]);

  const selectGroup = useCallback((name) => {
    setLocation(`/compare?group=${encodeURIComponent(name)}`);
    setActiveTab('valuation');
  }, [setLocation]);

  const activeTabDef = TABS.find(t => t.id === activeTab) || TABS[0];
  const tickers      = groupData?.tickers || [];

  return (
    <div className="container py-6 space-y-6">

      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg shadow-purple-500/20">
          <BarChart3 size={18} className="text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold">Industry Compare</h1>
          <p className="text-sm text-[var(--text-muted)]">Compare all stocks in an industry group side-by-side</p>
        </div>
      </div>

      {/* Group selector */}
      <div className="glass rounded-xl p-4">
        <p className="text-xs text-[var(--text-muted)] mb-3 font-medium uppercase tracking-wider">Industry Groups</p>
        <div className="flex flex-wrap gap-2">
          {groups.map(g => (
            <button
              key={g.name}
              onClick={() => selectGroup(g.name)}
              className={`px-3.5 py-1.5 rounded-full text-sm font-medium transition-all duration-200 border
                ${activeGroup === g.name
                  ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)] border-[rgba(139,92,246,0.5)] shadow-sm shadow-purple-500/20'
                  : 'text-[var(--text-secondary)] border-[var(--glass-border)] hover:text-[var(--text-primary)] hover:border-[rgba(255,255,255,0.2)] hover:bg-[rgba(255,255,255,0.04)]'
                }
              `}
            >
              {g.name}
              <span className="ml-1.5 text-xs opacity-60">({g.tickers.length})</span>
            </button>
          ))}
        </div>
      </div>

      {/* Empty state */}
      {!activeGroup && (
        <div className="glass rounded-xl p-12 text-center text-[var(--text-muted)]">
          <BarChart3 size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Select an industry group above to compare stocks</p>
        </div>
      )}

      {/* Loading */}
      {activeGroup && loading && (
        <div className="glass rounded-xl p-12 flex items-center justify-center gap-3 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-purple)]" />
          <span className="text-sm">Loading {activeGroup}…</span>
        </div>
      )}

      {/* Error */}
      {activeGroup && error && !loading && (
        <div className="glass rounded-xl p-8 text-center text-[var(--accent-danger)] text-sm">{error}</div>
      )}

      {/* Main panel */}
      {activeGroup && !loading && !error && groupData && (
        <div className="glass rounded-xl overflow-hidden">

          {/* Group title + view toggle */}
          <div className="px-5 pt-5 pb-3 border-b border-[var(--glass-border)] flex items-center justify-between gap-3">
            <div className="flex items-baseline gap-3">
              <h2 className="text-base font-semibold">{activeGroup}</h2>
              <span className="text-xs text-[var(--text-muted)]">{tickers.length} stocks</span>
            </div>
            <ViewToggle view={viewMode} onChange={setViewMode} />
          </div>

          {/* Tab bar */}
          <div className="flex border-b border-[var(--glass-border)] px-5 pt-3 gap-1">
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-all duration-200 -mb-px border-b-2
                  ${activeTab === tab.id
                    ? 'text-[var(--accent-purple)] border-[var(--accent-purple)]'
                    : 'text-[var(--text-muted)] border-transparent hover:text-[var(--text-primary)]'
                  }
                `}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className={viewMode === 'chart' ? 'px-2 py-3' : 'px-2 py-2'}>
            {tickers.length === 0 ? (
              <div className="py-10 text-center text-sm text-[var(--text-muted)]">No data available</div>
            ) : viewMode === 'table' ? (
              <MetricsTable columns={activeTabDef.columns} tickers={tickers} />
            ) : (
              <MetricsChart columns={activeTabDef.columns} tickers={tickers} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
