import { useState, useEffect, useMemo, useRef } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import GroupSelect from '../components/GroupSelect';
import { useExtendedQuotes } from '../components/useExtendedQuotes';
import ExtendedQuoteBadge from '../components/ExtendedQuote';
import {
  RefreshCw, TrendingUp, TrendingDown, DollarSign,
  BarChart2, Layers, Trash2, Plus, CloudDownload, Check, X,
  Wallet, ChevronDown, ChevronUp, CalendarRange,
} from 'lucide-react';

const PIE_COLORS = [
  '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b',
  '#ef4444', '#6366f1', '#ec4899', '#84cc16',
  '#f97316', '#a855f7',
];

const ACCOUNTS = ['All', 'eToro', 'Firstrade'];

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtPct(n) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`;
}

function fmtSignedDollar(n) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : '-'}$${fmt(Math.abs(n))}`;
}

/* ─── Portfolio Diagnosis: one shared visual language ────────────────────────
   Colour carries exactly one meaning in this block: direction.
     green = 偏多 / 上漲   red = 偏空 / 下跌   amber = 需留意（方向未定）
   Brand blue stays reserved for links, so a coloured number is never ambiguous.
   Surfaces use the light-theme tokens; rgba(255,255,255,x) renders invisible
   on this white background.
─────────────────────────────────────────────────────────────────────────── */
const DIR = {
  bull:    { text: 'text-[var(--accent-green)]',   chip: 'bg-[rgba(5,150,105,0.10)]',  bar: 'bg-[var(--accent-green)]',  ring: 'border-[rgba(5,150,105,0.32)]',  wash: 'bg-[rgba(5,150,105,0.035)]' },
  bear:    { text: 'text-[var(--accent-danger)]',  chip: 'bg-[rgba(220,38,38,0.09)]',  bar: 'bg-[var(--accent-danger)]', ring: 'border-[rgba(220,38,38,0.32)]',  wash: 'bg-[rgba(220,38,38,0.035)]' },
  watch:   { text: 'text-[var(--accent-yellow)]',  chip: 'bg-[rgba(217,119,6,0.10)]',  bar: 'bg-[var(--accent-yellow)]', ring: 'border-[rgba(217,119,6,0.32)]',  wash: 'bg-[rgba(217,119,6,0.035)]' },
  neutral: { text: 'text-[var(--text-secondary)]', chip: 'bg-[var(--bg-tertiary)]',    bar: 'bg-[var(--glass-border)]',  ring: 'border-[var(--glass-border)]',   wash: 'bg-[var(--bg-secondary)]' },
};

// The API still speaks the old five-tone vocabulary. Fold it onto the three-way lock.
const toDir = (tone) =>
  tone === 'bull' || tone === 'green' || tone === 'cyan' ? 'bull'
  : tone === 'bear' || tone === 'red' ? 'bear'
  : tone === 'watch' || tone === 'orange' || tone === 'yellow' ? 'watch'
  : 'neutral';

// Portfolio impact: weight% × day change% lands directly in basis points of
// total portfolio. Answers "which of these actually moved my money", which raw
// day-change % on its own does not.
const impactBp = (x) =>
  x?.weight_pct != null && x?.day_change_pct != null ? x.weight_pct * x.day_change_pct : null;

const fmtBp = (bp) => (bp == null ? '—' : `${bp >= 0 ? '+' : '-'}${Math.abs(bp).toFixed(1)}`);

// Decisive signals lead. `large_moves` sinks to the bottom because the Δ% column
// already states it. Falls back to tone for feeds that carry no severity.
const rankSignals = (signals = []) =>
  [...signals].sort((a, b) => {
    const redundant = (s) => (s.category === 'large_moves' ? 1 : 0);
    if (redundant(a) !== redundant(b)) return redundant(a) - redundant(b);
    if ((b.severity ?? 0) !== (a.severity ?? 0)) return (b.severity ?? 0) - (a.severity ?? 0);
    const w = (s) => (toDir(s.tone) === 'neutral' ? 2 : toDir(s.tone) === 'watch' ? 1 : 0);
    return w(a) - w(b);
  });

/* The backend composes suggestions as "。".join(parts), one part per signal, and
   every part is written as 觀察，…，動作. Recover that structure instead of
   printing a 150-character run-on: one line per signal, action set in the
   primary text colour so the thing to actually do is the thing that stands out. */
function SuggestionList({ text }) {
  const lines = useMemo(() => (
    (text || '')
      .split('。')
      .map(s => s.trim())
      .filter(Boolean)
      // 持倉佔比…重倉股…建議優先關注 repeats the 持倉 / 今日影響 fields rendered
      // directly above, and the group heading already says 重點關注.
      .filter(s => !s.startsWith('持倉佔比'))
      .map(s => {
        const cut = s.lastIndexOf('，');
        return cut > 0
          ? { observation: s.slice(0, cut + 1), action: s.slice(cut + 1) }
          : { observation: '', action: s };
      })
  ), [text]);

  if (lines.length === 0) return null;
  return (
    <ul className="space-y-1.5">
      {lines.map((l, i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-[9px] h-[3px] w-[3px] shrink-0 rounded-full bg-[var(--text-muted)]" />
          <p className="text-[12.5px] leading-[1.8] text-[var(--text-secondary)]">
            {l.observation}
            <span className="font-medium text-[var(--text-primary)]">{l.action}</span>
          </p>
        </li>
      ))}
    </ul>
  );
}

function SignalChips({ signals, max = 4 }) {
  const ranked = rankSignals(signals);
  const shown = ranked.slice(0, max);
  const rest = ranked.length - shown.length;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((s, i) => {
        const d = DIR[toDir(s.tone)];
        return (
          <span key={i} className={`text-[10.5px] font-semibold px-1.5 py-0.5 rounded ${d.text} ${d.chip}`}>
            {s.label ?? s.tag}
          </span>
        );
      })}
      {rest > 0 && (
        <span className="text-[10.5px] font-semibold px-1.5 py-0.5 rounded text-[var(--text-muted)] bg-[var(--bg-tertiary)]">
          +{rest}
        </span>
      )}
    </div>
  );
}

// Sector slice → which tickers contribute (used as the pie's hover tooltip).
const _sectorOf = (h) => (h.ticker === 'CASH' ? 'Cash' : (h.industry || 'Unknown'));

function SectorTickerTooltip({ active, payload, holdings = [], totalValue = 0 }) {
  if (!active || !payload || !payload.length) return null;
  const sector = payload[0]?.payload?.name;
  if (!sector) return null;
  const rows = holdings
    .filter(h => _sectorOf(h) === sector)
    .map(h => ({ ticker: h.ticker, value: h.market_value ?? h.cost_basis ?? 0 }))
    .sort((a, b) => b.value - a.value);
  const sectorValue = rows.reduce((s, r) => s + r.value, 0);
  return (
    <div style={{
      background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)',
      borderRadius: 8, fontSize: 12, padding: '8px 10px', minWidth: 190, maxWidth: 260,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontWeight: 700, marginBottom: 6 }}>
        <span style={{ color: 'var(--text-primary)' }}>{sector}</span>
        <span style={{ color: 'var(--text-secondary)' }}>
          ${fmt(sectorValue)} · {totalValue > 0 ? (sectorValue / totalValue * 100).toFixed(1) : 0}%
        </span>
      </div>
      {rows.slice(0, 8).map(r => (
        <div key={r.ticker} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, lineHeight: 1.6 }}>
          <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{r.ticker}</span>
          <span style={{ color: 'var(--text-secondary)' }}>
            ${fmt(r.value)} <span style={{ color: 'var(--text-muted)' }}>({sectorValue > 0 ? (r.value / sectorValue * 100).toFixed(0) : 0}%)</span>
          </span>
        </div>
      ))}
      {rows.length > 8 && (
        <div style={{ color: 'var(--text-muted)', marginTop: 4 }}>+{rows.length - 8} more</div>
      )}
    </div>
  );
}

function StatusBadge({ status }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  const s = status.toLowerCase();
  const map = {
    undervalued:     'text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]',
    overvalued:      'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
    'fairly valued': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  };
  const cls = map[s] || 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]';
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize ${cls}`}>{status}</span>;
}

const EMPTY_ADD = { account: 'Firstrade', ticker: '', shares: '', avg_cost: '', notes: '' };

function buildTechNote(tech) {
  if (!tech) return null;
  const parts = [];
  let score = 0; // positive = bullish, negative = bearish

  const rsi = tech.rsi_14;
  if (rsi != null) {
    if (rsi >= 85)      { parts.push(`RSI ${rsi} 極端超買`); score -= 3; }
    else if (rsi >= 70) { parts.push(`RSI ${rsi} 超買`);     score -= 1; }
    else if (rsi <= 25) { parts.push(`RSI ${rsi} 極端超賣`); score += 3; }
    else if (rsi <= 35) { parts.push(`RSI ${rsi} 超賣`);     score += 1; }
    else                  parts.push(`RSI ${rsi}`);
  }

  if (tech.ma_cross === 'golden')      { parts.push('黃金交叉'); score += 2; }
  else if (tech.ma_cross === 'death')  { parts.push('死亡交叉'); score -= 2; }
  else if (tech.price_vs_ma20_pct != null && Math.abs(tech.price_vs_ma20_pct) >= 10) {
    const dev = tech.price_vs_ma20_pct;
    parts.push(`偏離MA20 ${dev > 0 ? '+' : ''}${dev}%`);
    if (dev >= 15) score -= 1;
  }

  const s = (tech.near_support || [])[0];
  const r = (tech.near_resistance || [])[0];
  if (s) { parts.push(`支撐 $${s.price}↓${s.dist_pct}%`); score += 1; }
  if (r) { parts.push(`壓力 $${r.price}↑${r.dist_pct}%`); score -= 1; }

  if (tech.volume_ratio != null && tech.volume_ratio >= 2) {
    parts.push(`量能 ${tech.volume_ratio}×`);
  }

  if (!parts.length) return null;

  let tone;
  if (score <= -3)     tone = 'red';
  else if (score <= -1) tone = 'orange';
  else if (score >= 3)  tone = 'green';
  else if (score >= 1)  tone = 'cyan';
  else                  tone = 'muted';

  return { text: parts.join(' · '), tone };
}

export default function GSheetPortfolio() {
  const [allData,    setAllData]    = useState(null);
  const [selected,   setSelected]   = useState(null);
  const [acct,       setAcct]       = useState('All');
  const [weightDir,  setWeightDir]  = useState('desc');
  const [loading,    setLoading]    = useState(true);
  const [syncing,    setSyncing]    = useState(false);
  const [error,      setError]      = useState(null);
  // cell-level edit: { ticker, account, field } | null
  const [editCell,        setEditCell]        = useState(null);
  const [editVal,         setEditVal]         = useState('');
  // add row state
  const [adding,          setAdding]          = useState(false);
  const [addVals,         setAddVals]         = useState(EMPTY_ADD);
  const [addError,        setAddError]        = useState('');
  const [addSaving,       setAddSaving]       = useState(false);
  // deposit state
  const [depositsOpen,    setDepositsOpen]    = useState(false);
  const [techOpen,        setTechOpen]        = useState(false);
  const [newDeposit,      setNewDeposit]      = useState({ date: '', amount: '' });
  const [depositSaving,   setDepositSaving]   = useState(false);
  const [depositError,    setDepositError]    = useState('');
  const [reportPeriod,    setReportPeriod]    = useState('daily');
  const [report,          setReport]          = useState(null);
  const [reportLoading,   setReportLoading]   = useState(false);
  const [reportError,     setReportError]     = useState('');
  const [perfInterval,    setPerfInterval]    = useState('monthly');
  const [perfHistory,     setPerfHistory]     = useState(null);
  const [perfLoading,     setPerfLoading]     = useState(false);
  const [perfError,       setPerfError]       = useState('');
  const [showPerfPanel,   setShowPerfPanel]   = useState(false);
  const [riskExposure,    setRiskExposure]    = useState(null);
  const [riskLoading,     setRiskLoading]     = useState(false);
  const [riskError,       setRiskError]       = useState('');
  const [showHoldings,    setShowHoldings]    = useState(false);
  const [selectedSector,  setSelectedSector]  = useState(null);
  const [scrapingTickers, setScrapingTickers] = useState(new Set());
  const [groups,          setGroups]          = useState([]);
  const [changingGroup,   setChangingGroup]   = useState(null);
  const [autoRefresh,     setAutoRefresh]     = useState(true);   // 即時自動刷新
  const [lastUpdated,     setLastUpdated]     = useState(null);
  const pendingReloadRef    = useRef(false);
  const loadDataRef         = useRef(null);
  const scrapeAttemptedRef  = useRef(new Set());
  const scrapePolls         = useRef([]);

  const _scrapeMissing = (data) => {
    const SKIP = new Set(['CASH', '']);
    const toScrape = [];
    for (const pname of (data.portfolio_names || [])) {
      for (const h of (data.portfolios?.[pname]?.holdings || [])) {
        const t = (h.ticker || '').toUpperCase();
        if (!SKIP.has(t) && h.valuation_status == null && h.current_price != null
            && !scrapeAttemptedRef.current.has(h.ticker)) {
          toScrape.push(h.ticker);
          scrapeAttemptedRef.current.add(h.ticker);
        }
      }
    }
    if (toScrape.length === 0) return;

    setScrapingTickers(prev => new Set([...prev, ...toScrape]));
    let done = 0;

    const onOneDone = (ticker) => {
      setScrapingTickers(prev => { const n = new Set(prev); n.delete(ticker); return n; });
      done++;
      if (done >= toScrape.length && loadDataRef.current) {
        setTimeout(() => loadDataRef.current({ force: true }), 500);
      }
    };

    for (const ticker of toScrape) {
      axios.post('/api/scrape', { ticker, group: '其他' })
        .then(({ data: job }) => {
          const poll = setInterval(async () => {
            try {
              const { data: s } = await axios.get(`/api/scrape/${job.job_id}`);
              if (s.status === 'completed' || s.status === 'failed') {
                clearInterval(poll);
                onOneDone(ticker);
              }
            } catch { clearInterval(poll); onOneDone(ticker); }
          }, 3000);
          scrapePolls.current.push(poll);
        })
        .catch(() => onOneDone(ticker));
    }
  };

  const loadData = ({ force = false, silent = false } = {}) => {
    // Avoid clobbering user input while editing inline cells.
    if (!force && editCell) {
      pendingReloadRef.current = true;
      return;
    }
    // `silent` = background auto-refresh: skip the full-page spinner.
    if (!silent) setLoading(true);
    setError(null);
    axios.get('/api/portfolio/csv')
      .then(r => {
        setAllData(r.data);
        setLastUpdated(new Date());
        setSelected(prev =>
          r.data.portfolio_names.includes(prev) ? prev : r.data.portfolio_names[0] ?? null
        );
        _scrapeMissing(r.data);
      })
      .catch(e => { if (!silent) setError(e?.response?.data?.detail || 'Failed to load portfolio'); })
      .finally(() => { if (!silent) setLoading(false); });
  };

  loadDataRef.current = loadData;

  const syncFromSheet = () => {
    if (editCell) {
      setError('Finish editing the current cell first, then sync.');
      return;
    }
    setSyncing(true);
    axios.post('/api/portfolio/csv/sync')
      .then(() => loadData({ force: true }))
      .catch(e => setError(e?.response?.data?.detail || 'Sync failed'))
      .finally(() => setSyncing(false));
  };

  // Reassign a ticker's sector/group, then reload so the pie regroups.
  const changeGroup = (ticker, newGroup) => {
    if (!ticker || !newGroup || newGroup === selectedSector) return;
    setChangingGroup(ticker);
    setError(null);
    axios.patch(`/api/stocks/${ticker}/industry`, { industry: newGroup })
      .then(() => {
        setGroups(prev => prev.includes(newGroup) ? prev : [...prev, newGroup].sort());
        loadData({ force: true });
      })
      .catch(e => setError(e?.response?.data?.detail || `Failed to move ${ticker} to ${newGroup}`))
      .finally(() => setChangingGroup(null));
  };

  useEffect(() => {
    // Load local CSV cache only. Remote Google Sheet sync is manual via "Sync Sheet".
    loadData({ force: true });
  }, []);

  // 即時自動刷新：每 60 秒在背景重新抓取即時股價（不打斷編輯、分頁隱藏時暫停）。
  useEffect(() => {
    if (!autoRefresh) return;
    const REFRESH_MS = 60_000;
    const tick = () => {
      if (document.hidden) return;          // pause when tab is in background
      if (editCell) return;                 // don't clobber inline edits
      loadDataRef.current?.({ silent: true });
    };
    const id = setInterval(tick, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, editCell]);

  // Available sector/group names for the per-ticker "change group" dropdown.
  useEffect(() => {
    axios.get('/api/scrape')
      .then(r => setGroups(r.data.groups || []))
      .catch(() => {});
  }, []);

  // If a reload was deferred during editing, run it after editing ends.
  useEffect(() => {
    if (!editCell && pendingReloadRef.current) {
      pendingReloadRef.current = false;
      loadData({ force: true });
    }
  }, [editCell]);

  useEffect(() => {
    if (!selected) return;
    setReportLoading(true);
    setReportError('');
    axios.get('/api/portfolio/csv/report', { params: { portfolio: selected, period: reportPeriod } })
      .then(r => setReport(r.data))
      .catch(e => {
        setReport(null);
        setReportError(e?.response?.data?.detail || 'Failed to load report');
      })
      .finally(() => setReportLoading(false));
  }, [selected, reportPeriod, allData]);

  useEffect(() => {
    if (!showPerfPanel) return;
    if (!selected) return;
    setPerfLoading(true);
    setPerfError('');
    axios.get('/api/portfolio/csv/performance-history', { params: { portfolio: selected, interval: perfInterval, limit: 12 } })
      .then(r => setPerfHistory(r.data))
      .catch(e => {
        setPerfHistory(null);
        setPerfError(e?.response?.data?.detail || 'Failed to load performance history');
      })
      .finally(() => setPerfLoading(false));
  }, [selected, perfInterval, allData, showPerfPanel]);

  useEffect(() => {
    if (!selected) return;
    setRiskLoading(true);
    setRiskError('');
    axios.get('/api/portfolio/csv/beta', { params: { name: selected } })
      .then(r => setRiskExposure(r.data.portfolios?.[selected] || null))
      .catch(e => {
        setRiskExposure(null);
        setRiskError(e?.response?.data?.detail || 'Failed to load beta exposure');
      })
      .finally(() => setRiskLoading(false));
  }, [selected, allData]);

  // ── cell edit handlers ────────────────────────────────────────────────────
  const startCell = (ticker, account, field, currentVal) => {
    setEditCell({ ticker, account, field });
    setEditVal(String(currentVal ?? ''));
  };

  const saveCell = () => {
    const { ticker, account, field } = editCell;
    const parsed = parseFloat(editVal);
    if (isNaN(parsed)) { setEditCell(null); return; }
    setEditCell(null);
    axios.put('/api/portfolio/csv/holding', { portfolio: selected, account, ticker, [field]: parsed })
      .then(() => loadData({ force: true }))
      .catch(e => setError(e?.response?.data?.detail || 'Save failed'));
  };

  const cancelCell = () => setEditCell(null);

  // ── deposit handlers ──────────────────────────────────────────────────────
  const saveDeposit = () => {
    setDepositError('');
    if (!newDeposit.date)   { setDepositError('Date is required'); return; }
    if (!newDeposit.amount) { setDepositError('Amount is required'); return; }
    const amount = parseFloat(newDeposit.amount);
    if (isNaN(amount) || amount <= 0) { setDepositError('Amount must be positive'); return; }
    setDepositSaving(true);
    axios.post('/api/portfolio/csv/deposits', { portfolio: selected, date: newDeposit.date, amount })
      .then(() => { setNewDeposit({ date: '', amount: '' }); loadData(); })
      .catch(e => setDepositError(e?.response?.data?.detail || 'Failed to save'))
      .finally(() => setDepositSaving(false));
  };

  const deleteDeposit = (date, amount) => {
    axios.delete(`/api/portfolio/csv/deposits?portfolio=${encodeURIComponent(selected)}&date=${encodeURIComponent(date)}&amount=${amount}`)
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Failed to delete deposit'));
  };

  const deleteHolding = (ticker, account) => {
    if (!window.confirm(`Remove ${ticker} (${account}) from ${selected}?`)) return;
    axios.delete(`/api/portfolio/csv/holding?portfolio=${encodeURIComponent(selected)}&account=${encodeURIComponent(account)}&ticker=${encodeURIComponent(ticker)}`)
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Delete failed'));
  };

  // ── add handlers ───────────────────────────────────────────────────────────
  const saveAdd = () => {
    setAddError('');
    const ticker = addVals.ticker.toUpperCase().trim();
    if (!ticker)             { setAddError('Ticker is required'); return; }
    if (!addVals.shares)     { setAddError('Shares is required'); return; }
    if (!addVals.avg_cost)   { setAddError('Avg cost is required'); return; }
    setAddSaving(true);
    axios.post('/api/portfolio/csv/holding', {
      portfolio: selected,
      account:   addVals.account,
      ticker,
      shares:    parseFloat(addVals.shares),
      avg_cost:  parseFloat(addVals.avg_cost),
      notes:     addVals.notes,
    })
      .then(() => { setAdding(false); setAddVals(EMPTY_ADD); loadData(); })
      .catch(e => setAddError(e?.response?.data?.detail || 'Add failed'))
      .finally(() => setAddSaving(false));
  };

  // Pre/post-market quotes for the selected portfolio. Must stay above the
  // early returns below — it's a hook.
  const extendedQuotes = useExtendedQuotes(
    useMemo(
      () => (allData?.portfolios?.[selected]?.holdings || []).map(h => h.ticker),
      [allData, selected]
    )
  );

  // ── render states ─────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-3">
        <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
        <p className="text-xs text-[var(--text-muted)]">Loading portfolio…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-20 text-center space-y-3">
        <p className="text-[var(--accent-danger)]">{error}</p>
        <button onClick={loadData} className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
          Retry
        </button>
      </div>
    );
  }

  if (!allData || !selected) return null;

  const { portfolio_names, portfolios } = allData;
  const current  = portfolios[selected];
  if (!current) return null;

  const { summary, holdings, deposits = [] } = current;
  const { total_value, total_cost, total_pnl, return_pct, net_gain, net_return_pct, total_deposited, mwr, deposits_incomplete, sectors, starting_capital, total_cash, cash_pct, total_value_twd, usd_twd_rate } = summary;
  const gainValue = net_gain ?? total_pnl;
  const gainPct = net_return_pct ?? return_pct;

  const filtered = (acct === 'All'
    ? holdings
    : holdings.filter(h => h.account?.toLowerCase() === acct.toLowerCase())
  ).slice().sort((a, b) => {
    const wa = a.market_value ?? a.cost_basis ?? 0;
    const wb = b.market_value ?? b.cost_basis ?? 0;
    return weightDir === 'desc' ? wb - wa : wa - wb;
  });

  const filteredValue = filtered.reduce((s, h) => s + (h.market_value ?? h.cost_basis ?? 0), 0);
  const selectedSectorHoldings = selectedSector
    ? holdings
        .filter(h => (h.ticker === 'CASH' ? 'Cash' : (h.industry || 'Unknown')) === selectedSector)
        .slice()
        .sort((a, b) => (b.market_value ?? b.cost_basis ?? 0) - (a.market_value ?? a.cost_basis ?? 0))
    : [];
  const selectedSectorValue = selectedSectorHoldings.reduce((s, h) => s + (h.market_value ?? h.cost_basis ?? 0), 0);

  // Per-account breakdown
  const accountBreakdown = holdings.reduce((acc, h) => {
    const name = h.account || 'Unknown';
    if (!acc[name]) acc[name] = { value: 0, cost: 0, pnl: 0 };
    acc[name].value += h.market_value ?? h.cost_basis ?? 0;
    acc[name].cost  += h.cost_basis ?? 0;
    acc[name].pnl   += h.unrealized_pnl ?? 0;
    return acc;
  }, {});

  const headline = report?.headline;
  const flow = report?.position_flow;
  const rotation = report?.weight_rotation;
  const techSignals = report?.technical_signals || {};
  const perHoldingTech = techSignals.per_holding || {};
  const abnormalStocks = report?.abnormal_stocks;
  const stockInsights = report?.stock_insights || [];
  const perfRows = perfHistory?.rows || [];

  return (
    <div className="container py-10 space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Layers size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">{selected}</h1>
            <p className="text-xs text-[var(--text-muted)]">Local CSV · live price &amp; valuation overlay</p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Portfolio tabs */}
          <div className="flex items-center gap-1 p-1 rounded-xl bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
            {portfolio_names.map(name => (
              <button
                key={name}
                onClick={() => { setSelected(name); setAcct('All'); setEditCell(null); setAdding(false); setDepositsOpen(false); setDepositError(''); setNewDeposit({ date: '', amount: '' }); setSelectedSector(null); }}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                  selected === name
                    ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                {name}
              </button>
            ))}
          </div>

          {/* Sync from sheet */}
          <button
            onClick={syncFromSheet}
            disabled={syncing || !!editCell}
            title="Re-fetch from Google Sheets"
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--accent-cyan)] hover:bg-[rgba(6,182,212,0.07)] transition-all disabled:opacity-50"
          >
            {syncing
              ? <RefreshCw size={14} className="animate-spin" />
              : <CloudDownload size={14} />}
            Sync Sheet
          </button>

          {/* Refresh prices */}
          <button
            onClick={loadData}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
          >
            <RefreshCw size={14} /> Refresh
          </button>

          {/* 即時自動刷新 toggle + last-updated */}
          <button
            onClick={() => setAutoRefresh(v => !v)}
            title={autoRefresh ? '即時自動刷新已開啟（每 60 秒）' : '即時自動刷新已關閉'}
            className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
              autoRefresh
                ? 'text-[var(--accent-green)] bg-[rgba(16,185,129,0.08)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)]'
            }`}
          >
            <span className={`inline-block w-2 h-2 rounded-full ${autoRefresh ? 'bg-[var(--accent-green)] animate-pulse' : 'bg-[var(--text-muted)]'}`} />
            即時 Live
          </button>
          {lastUpdated && (
            <span className="text-[11px] text-[var(--text-muted)] whitespace-nowrap">
              更新 {lastUpdated.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
            </span>
          )}

          <button
            onClick={() => setShowPerfPanel(v => !v)}
            className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
              showPerfPanel
                ? 'text-[var(--accent-purple)] bg-[rgba(139,92,246,0.12)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)]'
            }`}
          >
            <BarChart2 size={14} /> 績效追蹤
            {showPerfPanel ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>

      {/* Monthly / Quarterly performance trend (toggle) */}
      {showPerfPanel && (
        <div className="card p-5 space-y-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2.5">
              <BarChart2 size={15} className="text-[var(--accent-purple)]" />
              <div>
                <p className="text-sm font-semibold text-[var(--text-secondary)]">績效變化追蹤 Performance Trend</p>
                <p className="text-[11px] text-[var(--text-muted)]">以每月/每季最後一筆快照整理績效，方便追蹤變化。</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 p-1 rounded-lg bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
                {[
                  { value: 'monthly', label: 'Monthly' },
                  { value: 'quarterly', label: 'Quarterly' },
                ].map(it => (
                  <button
                    key={it.value}
                    onClick={() => setPerfInterval(it.value)}
                    className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-all ${
                      perfInterval === it.value
                        ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)]'
                        : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {it.label}
                  </button>
                ))}
              </div>
              <button
                onClick={() => {
                  if (!selected) return;
                  setPerfLoading(true);
                  setPerfError('');
                  axios.get('/api/portfolio/csv/performance-history', { params: { portfolio: selected, interval: perfInterval, limit: 12 } })
                    .then(r => setPerfHistory(r.data))
                    .catch(e => setPerfError(e?.response?.data?.detail || 'Failed to load performance history'))
                    .finally(() => setPerfLoading(false));
                }}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
              >
                <RefreshCw size={12} className={perfLoading ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>
          </div>

          {perfError && (
            <div className="text-xs text-[var(--accent-danger)]">{perfError}</div>
          )}

          {perfRows.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">目前快照資料不足，持續每日打開頁面刷新後，會自動累積月/季績效紀錄。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[var(--glass-border)]">
                    {['Period', 'Snapshot', 'Total Value', 'Period Change', 'Net Return', 'MWR', 'Cash %', 'Positions'].map(h => (
                      <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...perfRows].reverse().map((row) => (
                    <tr key={row.period} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors">
                      <td className="px-3 py-2 font-semibold text-[var(--text-secondary)]">{row.period_label}</td>
                      <td className="px-3 py-2 text-[var(--text-muted)] font-mono">{row.snapshot_date}</td>
                      <td className="px-3 py-2 font-medium">${fmt(row.total_value)}</td>
                      <td className={`px-3 py-2 font-medium ${row.period_change == null ? 'text-[var(--text-muted)]' : row.period_change >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                        {row.period_change == null ? '—' : `${fmtSignedDollar(row.period_change)} (${fmtPct(row.period_change_pct)})`}
                      </td>
                      <td className={`px-3 py-2 font-medium ${row.net_return_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                        {fmtPct(row.net_return_pct)}
                      </td>
                      <td className={`px-3 py-2 font-medium ${row.mwr == null ? 'text-[var(--text-muted)]' : row.mwr >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                        {row.mwr == null ? '—' : fmtPct(row.mwr)}
                      </td>
                      <td className="px-3 py-2">{fmt(row.cash_pct, 1)}%</td>
                      <td className="px-3 py-2">{row.position_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Market Value"
          value={`$${fmt(total_value)}`}
          subValue={total_value_twd ? `NT$ ${Math.round(total_value_twd).toLocaleString('zh-TW')}` : null}
          subValueColor="cyan"
          color="cyan"
        />
        <SummaryCard
          icon={<DollarSign size={15} />}
          label={starting_capital ? 'Starting Capital' : 'Cost Basis'}
          value={`$${fmt(starting_capital ?? total_cost)}`}
          color="purple"
        />
        <SummaryCard
          icon={gainValue >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
          label="Net Gain"
          value={`${gainValue >= 0 ? '+' : ''}$${fmt(gainValue)}`}
          subValue={gainPct != null ? `${gainPct >= 0 ? '+' : ''}${fmt(gainPct)}%` : null}
          subValue2={usd_twd_rate ? `${gainValue >= 0 ? '+' : '-'}NT$ ${Math.round(Math.abs(gainValue) * usd_twd_rate).toLocaleString('zh-TW')}` : null}
          subValue2Color={gainValue >= 0 ? 'green' : 'red'}
          color={gainValue >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={mwr != null ? (mwr >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />) : <BarChart2 size={15} />}
          label="MWR (annualised)"
          value={mwr != null ? fmtPct(mwr) : '—'}
          subValue={
            deposits_incomplete
              ? '⚠ 存款記錄不完整'
              : mwr != null
                ? `${deposits.length} deposit${deposits.length !== 1 ? 's' : ''}${total_deposited ? ` · $${fmt(total_deposited)}` : ''}`
                : deposits.length === 0
                  ? 'Add deposits ↓'
                  : null
          }
          color={deposits_incomplete ? 'yellow' : mwr == null ? 'purple' : mwr >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Cash"
          value={total_cash > 0 ? `$${fmt(total_cash)}` : '—'}
          subValue={total_cash > 0 ? `${cash_pct}% of portfolio` : null}
          subValueColor="orange"
          color="orange"
        />
      </div>

      {/* Beta / CAPM risk exposure */}
      <div className="card p-5 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2.5">
            <BarChart2 size={15} className="text-[var(--accent-purple)]" />
            <div>
              <p className="text-sm font-semibold text-[var(--text-secondary)]">Beta / CAPM 風險敞口</p>
              <p className="text-[11px] text-[var(--text-muted)]">
                以 SPY 近一年日報酬估 beta；現金 beta = 0，leverage 會放大 beta contribution。
              </p>
            </div>
          </div>
          <button
            onClick={() => {
              if (!selected) return;
              setRiskLoading(true);
              setRiskError('');
              axios.get('/api/portfolio/csv/beta', { params: { name: selected } })
                .then(r => setRiskExposure(r.data.portfolios?.[selected] || null))
                .catch(e => setRiskError(e?.response?.data?.detail || 'Failed to load beta exposure'))
                .finally(() => setRiskLoading(false));
            }}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
          >
            <RefreshCw size={12} className={riskLoading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {riskError && <div className="text-xs text-[var(--accent-danger)]">{riskError}</div>}

        {riskExposure ? (
          <>
            {/* ── 6 stat cards ── */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <MiniStat
                label="Portfolio Beta"
                value={riskExposure.portfolio_beta ?? '—'}
                sub={`vs ${riskExposure.benchmark || 'SPY'} ${riskExposure.period || '1y'}`}
                tone={riskExposure.portfolio_beta == null ? 'neutral' : riskExposure.portfolio_beta >= 1 ? 'orange' : 'green'}
              />
              <MiniStat
                label="Volatility (ann.)"
                value={riskExposure.volatility_pct != null ? `${fmt(riskExposure.volatility_pct, 1)}%` : '—'}
                sub="portfolio σ"
                tone={riskExposure.volatility_pct == null ? 'neutral' : riskExposure.volatility_pct >= 30 ? 'orange' : 'cyan'}
              />
              <MiniStat
                label="Max Drawdown"
                value={riskExposure.max_drawdown_pct != null ? `${fmt(riskExposure.max_drawdown_pct, 1)}%` : '—'}
                sub="1y worst"
                tone={riskExposure.max_drawdown_pct == null ? 'neutral' : riskExposure.max_drawdown_pct <= -20 ? 'red' : 'orange'}
              />
              <MiniStat
                label="Sharpe Ratio"
                value={riskExposure.sharpe_ratio != null ? fmt(riskExposure.sharpe_ratio, 2) : '—'}
                sub={`Rf ${fmt(riskExposure.risk_free_rate_pct, 1)}%`}
                tone={riskExposure.sharpe_ratio == null ? 'neutral' : riskExposure.sharpe_ratio >= 1 ? 'green' : riskExposure.sharpe_ratio >= 0 ? 'cyan' : 'red'}
              />
              <MiniStat
                label="CAPM Exp. Return"
                value={riskExposure.capm_expected_return_pct != null ? fmtPct(riskExposure.capm_expected_return_pct) : '—'}
                sub={`SPY ${fmtPct(riskExposure.benchmark_return_pct)}`}
                tone={riskExposure.capm_expected_return_pct >= 0 ? 'green' : 'red'}
              />
              <MiniStat
                label="Alpha"
                value={riskExposure.alpha_pct != null ? fmtPct(riskExposure.alpha_pct) : '—'}
                sub="actual − CAPM"
                tone={riskExposure.alpha_pct == null ? 'neutral' : riskExposure.alpha_pct >= 0 ? 'green' : 'red'}
              />
            </div>

            {/* ── Holdings table (collapsible) ── */}
            <button
              onClick={() => setShowHoldings(v => !v)}
              className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ transform: showHoldings ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}>
                <path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {showHoldings ? '隱藏' : '顯示'} 個股明細
            </button>

            {showHoldings && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[var(--glass-border)]">
                      {['Ticker', 'Weight', 'Beta', 'Beta contrib', '1y Return', 'Vol', 'Max DD', 'CAPM exp.'].map(h => (
                        <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(riskExposure.holdings || []).slice(0, 20).map(row => (
                      <tr key={`${row.ticker}-${row.market_value}`} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors">
                        <td className="px-3 py-2 font-bold text-[var(--accent-cyan)]">{row.ticker}</td>
                        <td className="px-3 py-2">{row.weight_pct != null ? `${fmt(row.weight_pct, 1)}%` : '—'}</td>
                        <td className={`px-3 py-2 font-semibold ${row.beta == null ? 'text-[var(--text-muted)]' : row.beta >= 1 ? 'text-[var(--accent-yellow)]' : 'text-[var(--text-secondary)]'}`}>
                          {row.beta ?? '—'}
                        </td>
                        <td className={`px-3 py-2 font-semibold ${row.beta_contribution_pct == null ? 'text-[var(--text-muted)]' : row.beta_contribution_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                          {row.beta_contribution_pct != null ? `${fmt(row.beta_contribution_pct, 2)}%` : '—'}
                        </td>
                        <td className={`px-3 py-2 font-medium ${row.return_1y_pct == null ? 'text-[var(--text-muted)]' : row.return_1y_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                          {row.return_1y_pct != null ? fmtPct(row.return_1y_pct) : '—'}
                        </td>
                        <td className="px-3 py-2 text-[var(--text-secondary)]">
                          {row.vol_pct != null ? `${fmt(row.vol_pct, 1)}%` : '—'}
                        </td>
                        <td className={`px-3 py-2 ${row.max_drawdown_pct == null ? 'text-[var(--text-muted)]' : row.max_drawdown_pct <= -30 ? 'text-[var(--accent-danger)]' : 'text-[var(--text-secondary)]'}`}>
                          {row.max_drawdown_pct != null ? `${fmt(row.max_drawdown_pct, 1)}%` : '—'}
                        </td>
                        <td className={`px-3 py-2 font-medium ${row.capm_expected_return_pct == null ? 'text-[var(--text-muted)]' : row.capm_expected_return_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                          {row.capm_expected_return_pct != null ? fmtPct(row.capm_expected_return_pct) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-[var(--text-muted)]">
            {riskLoading ? 'Calculating beta exposure…' : 'Beta exposure not available yet.'}
          </p>
        )}
      </div>

      {/* Daily / Weekly diagnosis */}
      <div className="card p-5 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2.5">
            <CalendarRange size={15} className="text-[var(--accent-cyan)]" />
            <div>
              <p className="text-sm font-semibold text-[var(--text-secondary)]">日報 · Portfolio Diagnosis</p>
              <p className="text-[11px] text-[var(--text-muted)]">
                {report?.baseline_date
                  ? `As of ${report.as_of} · compare vs ${report.baseline_date}`
                  : 'First snapshot created. A comparison baseline will be available after enough daily history.'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 p-1 rounded-lg bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
              {[
                { value: 'daily', label: 'Daily' },
                { value: 'weekly', label: 'Weekly' },
                { value: 'monthly', label: 'Monthly' },
                { value: 'quarterly', label: 'Quarterly' },
              ].map(p => (
                <button
                  key={p.value}
                  onClick={() => setReportPeriod(p.value)}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-all ${
                    reportPeriod === p.value
                      ? 'bg-[rgba(6,182,212,0.18)] text-[var(--accent-cyan)]'
                      : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => {
                if (!selected) return;
                setReportLoading(true);
                setReportError('');
                axios.get('/api/portfolio/csv/report', { params: { portfolio: selected, period: reportPeriod } })
                  .then(r => setReport(r.data))
                  .catch(e => setReportError(e?.response?.data?.detail || 'Failed to load report'))
                  .finally(() => setReportLoading(false));
              }}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
            >
              <RefreshCw size={12} className={reportLoading ? 'animate-spin' : ''} />
              Refresh
            </button>
          </div>
        </div>

        {reportError && (
          <div className="text-xs text-[var(--accent-danger)]">{reportError}</div>
        )}

        {headline && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <MiniStat
              label="今日漲跌 Daily P&L"
              value={fmtSignedDollar(headline.daily_pnl)}
              sub={headline.daily_pnl_pct != null ? fmtPct(headline.daily_pnl_pct) : '—'}
              tone={headline.daily_pnl == null ? 'neutral' : headline.daily_pnl >= 0 ? 'green' : 'red'}
            />
            <MiniStat
              label="期間變化 Period"
              value={fmtSignedDollar(headline.value_change)}
              sub={headline.value_change_pct != null ? fmtPct(headline.value_change_pct) : '—'}
              tone={headline.value_change == null ? 'neutral' : headline.value_change >= 0 ? 'green' : 'red'}
            />
            <MiniStat
              label="現金水位 Cash"
              value={headline.cash_pct != null ? `${fmt(headline.cash_pct, 1)}%` : '—'}
              sub={headline.cash_pct_change != null ? `${headline.cash_pct_change >= 0 ? '+' : ''}${fmt(headline.cash_pct_change, 1)} pts` : '—'}
            />
            <MiniStat
              label="持股檔數 Positions"
              value={String(headline.position_count ?? '—')}
              sub={headline.position_count_change != null ? `${headline.position_count_change >= 0 ? '+' : ''}${headline.position_count_change} 檔` : '—'}
            />
            <MiniStat
              label="總損益 Net Gain"
              value={fmtSignedDollar(headline.net_gain)}
              sub={headline.net_return_pct != null ? fmtPct(headline.net_return_pct) : '—'}
              tone={headline.net_gain >= 0 ? 'green' : 'red'}
            />
            <MiniStat
              label="資金報酬 MWR"
              value={headline.mwr != null ? fmtPct(headline.mwr) : '—'}
              sub={`${report?.snapshots_available ?? 0} 筆快照`}
              tone={headline.mwr == null ? 'neutral' : headline.mwr >= 0 ? 'green' : 'red'}
            />
          </div>
        )}

        {/* Abnormal Stocks */}
        {abnormalStocks?.summary?.length > 0 && (
          <div className="border-t border-[var(--glass-border)] pt-4 space-y-3">
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <div>
                <p className="text-sm font-bold text-[var(--text-primary)]">異常股票 · Breakout / Breakdown Radar</p>
                <p className="text-[11px] text-[var(--text-muted)]">技術面掃描，依對投組的影響排序</p>
              </div>
              <span className="font-mono tabular-nums text-[11px] text-[var(--text-muted)]">
                {abnormalStocks.summary.length} 檔 / {Object.values(abnormalStocks.counts || {}).reduce((s, n) => s + n, 0)} 個訊號
              </span>
            </div>
            <AbnormalStocksPanel abnormal={abnormalStocks} />
          </div>
        )}

        {/* Stock Insights */}
        {stockInsights.length > 0 && (
          <div className="border-t border-[var(--glass-border)] pt-4 space-y-4">
            <div>
              <p className="text-sm font-bold text-[var(--text-primary)]">個股分析 · Notable Movers</p>
              <p className="text-[11px] text-[var(--text-muted)]">同一批標的，依處理優先順序分組並附操作建議</p>
            </div>
            <StockInsightsGrouped insights={stockInsights} />
          </div>
        )}

        {/* Risk Flags */}
        {report?.risk_flags?.length > 0 && <RiskAlerts flags={report.risk_flags} />}

        {report && (
          <div className="space-y-4">
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <p className="text-xs font-semibold text-[var(--text-secondary)]">Position Flow</p>
                <FlowRow label="New" tone="green" items={flow?.new_positions || []} />
                <FlowRow label="Removed" tone="red" items={flow?.removed_positions || []} />
                <FlowDeltaRow label="Increased" tone="cyan" items={flow?.increased_positions || []} />
                <FlowDeltaRow label="Reduced" tone="orange" items={flow?.reduced_positions || []} />
              </div>
              <div className="space-y-2">
                <p className="text-xs font-semibold text-[var(--text-secondary)]">Weight Rotation</p>
                <WeightRow label="Weight Up" tone="green" items={rotation?.up || []} />
                <WeightRow label="Weight Down" tone="red" items={rotation?.down || []} />
              </div>
            </div>

            {/* Technical Signals by Ticker — the same signals the radar above
                already ranks, kept as a per-ticker index behind a disclosure so
                the card is not three copies of one list. */}
            {Object.keys(techSignals).some(k => k !== 'per_holding' && techSignals[k]?.length > 0) && (
              <div className="border-t border-[var(--glass-border)] pt-4 space-y-2.5">
                <button
                  type="button"
                  onClick={() => setTechOpen(o => !o)}
                  aria-expanded={techOpen}
                  className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  {techOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  技術訊號總覽 · Technical Signals by Ticker
                  <span className="font-normal text-[var(--text-muted)]">（依代號分組）</span>
                </button>
                {techOpen && <TechSignalsByTicker techSignals={techSignals} />}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Deposits panel */}
      <div className="card overflow-hidden">
        <button
          onClick={() => setDepositsOpen(o => !o)}
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-[rgba(255,255,255,0.02)] transition-colors"
        >
          <div className="flex items-center gap-2.5">
            <Wallet size={15} className="text-[var(--accent-purple)]" />
            <span className="text-sm font-semibold">入金紀錄 · Deposits</span>
            {deposits.length > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)] font-semibold">
                {deposits.length} entries · ${fmt(deposits.reduce((s, d) => s + d.amount, 0))}
              </span>
            )}
          </div>
          {depositsOpen ? <ChevronUp size={14} className="text-[var(--text-muted)]" /> : <ChevronDown size={14} className="text-[var(--text-muted)]" />}
        </button>

        {depositsOpen && (
          <div className="border-t border-[var(--glass-border)] px-5 pb-5 pt-4 space-y-4">
            {/* Existing deposits */}
            {deposits.length > 0 ? (
              <div className="space-y-1.5">
                {[...deposits].sort((a, b) => a.date.localeCompare(b.date)).map((d, i) => (
                  <div key={i} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[rgba(255,255,255,0.03)] group">
                    <div className="flex items-center gap-4">
                      <span className="text-xs text-[var(--text-muted)] font-mono">{d.date}</span>
                      <span className="text-sm font-semibold text-[var(--accent-cyan)]">${fmt(d.amount)}</span>
                    </div>
                    <button
                      onClick={() => deleteDeposit(d.date, d.amount)}
                      className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all opacity-0 group-hover:opacity-100"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-[var(--text-muted)] py-1">No deposits yet. Add your first deposit to calculate MWR.</p>
            )}

            {/* Add new deposit */}
            <div className="flex items-end gap-2 flex-wrap">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Date</label>
                <input
                  type="date"
                  value={newDeposit.date}
                  onChange={e => setNewDeposit(v => ({ ...v, date: e.target.value }))}
                  className="px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Amount (USD)</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  placeholder="e.g. 5000"
                  value={newDeposit.amount}
                  onChange={e => setNewDeposit(v => ({ ...v, amount: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && saveDeposit()}
                  className="w-36 px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors"
                />
              </div>
              <button
                onClick={saveDeposit}
                disabled={depositSaving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 transition-all disabled:opacity-50"
              >
                <Plus size={12} /> {depositSaving ? 'Saving…' : 'Add'}
              </button>
              {depositError && <span className="text-[11px] text-[var(--accent-danger)]">{depositError}</span>}
            </div>
          </div>
        )}
      </div>

      {/* Account breakdown */}
      {Object.keys(accountBreakdown).length > 1 && (
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Object.keys(accountBreakdown).length}, minmax(0, 1fr))` }}>
          {Object.entries(accountBreakdown).map(([name, stat]) => {
            const pct = total_value > 0 ? stat.value / total_value * 100 : 0;
            const pnlPct = stat.cost > 0 ? stat.pnl / stat.cost * 100 : 0;
            const isActive = acct.toLowerCase() === name.toLowerCase();
            return (
              <div
                key={name}
                onClick={() => setAcct(isActive ? 'All' : name)}
                className={`card p-4 space-y-2 cursor-pointer transition-all hover:border-[var(--accent-cyan)] hover:bg-[rgba(6,182,212,0.05)] ${isActive ? 'border-[var(--accent-cyan)] bg-[rgba(6,182,212,0.07)]' : ''}`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-semibold ${isActive ? 'text-[var(--accent-cyan)]' : 'text-[var(--text-secondary)]'}`}>{name}</span>
                  <span className="text-[10px] text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)] px-2 py-0.5 rounded-full">{pct.toFixed(1)}% of total</span>
                </div>
                <p className="text-xl font-extrabold text-[var(--accent-cyan)]">${fmt(stat.value)}</p>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-[var(--text-muted)]">Cost ${fmt(stat.cost)}</span>
                  <span className={stat.pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}>
                    {stat.pnl >= 0 ? '+' : ''}${fmt(stat.pnl)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                  </span>
                </div>
                <div className="h-1 rounded-full bg-[rgba(255,255,255,0.06)] overflow-hidden">
                  <div className="h-full rounded-full bg-gradient-to-r from-[var(--accent-cyan)] to-[var(--accent-purple)]" style={{ width: `${Math.min(pct, 100)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Sector allocation */}
      {sectors.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-4">Sector Allocation</h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={sectors}
                  dataKey="value"
                  nameKey="name"
                  cx="50%" cy="50%"
                  innerRadius={55} outerRadius={85}
                  paddingAngle={2}
                  cursor="pointer"
                  onClick={(entry) => setSelectedSector(prev => prev === entry.name ? null : entry.name)}
                  label={({ name, value, cx, cy, midAngle, outerRadius }) => {
                    const pct = total_value > 0 ? (value / total_value * 100).toFixed(1) : 0;
                    const RADIAN = Math.PI / 180;
                    const r = outerRadius + 22;
                    const x = cx + r * Math.cos(-midAngle * RADIAN);
                    const y = cy + r * Math.sin(-midAngle * RADIAN);
                    if (pct < 3) return null;
                    return (
                      <text x={x} y={y} fill="var(--text-secondary)" textAnchor={x > cx ? 'start' : 'end'} dominantBaseline="central" fontSize={10}>
                        {pct}%
                      </text>
                    );
                  }}
                  labelLine={false}
                >
                  {sectors.map((sector, i) => (
                    <Cell
                      key={sector.name}
                      fill={PIE_COLORS[i % PIE_COLORS.length]}
                      opacity={!selectedSector || selectedSector === sector.name ? 1 : 0.35}
                      stroke={selectedSector === sector.name ? 'var(--text-primary)' : 'transparent'}
                      strokeWidth={selectedSector === sector.name ? 2 : 0}
                    />
                  ))}
                </Pie>
                <Tooltip content={<SectorTickerTooltip holdings={holdings} totalValue={total_value} />} />
                <Legend
                  onClick={(entry) => setSelectedSector(prev => prev === entry.value ? null : entry.value)}
                  formatter={(v) => (
                    <span
                      style={{
                        fontSize: 11,
                        color: selectedSector === v ? 'var(--text-primary)' : 'var(--text-secondary)',
                        fontWeight: selectedSector === v ? 700 : 500,
                        cursor: 'pointer',
                      }}
                    >
                      {v}
                    </span>
                  )}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          {selectedSector && (
            <div className="mt-4 rounded-lg border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)] overflow-hidden">
              <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--glass-border)]">
                <div>
                  <p className="text-sm font-semibold text-[var(--text-primary)]">{selectedSector}</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {selectedSectorHoldings.length} holdings · ${fmt(selectedSectorValue)} · {total_value > 0 ? (selectedSectorValue / total_value * 100).toFixed(1) : '0.0'}% of portfolio
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedSector(null)}
                  className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)]"
                  title="Clear sector selection"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="divide-y divide-[var(--glass-border)]">
                {selectedSectorHoldings.map(h => {
                  const value = h.market_value ?? h.cost_basis ?? 0;
                  const pct = selectedSectorValue > 0 ? value / selectedSectorValue * 100 : 0;
                  return (
                    <div key={`${h.account}-${h.ticker}`} className="grid grid-cols-[minmax(64px,0.7fr)_minmax(90px,1.4fr)_minmax(58px,0.6fr)_minmax(80px,0.9fr)_minmax(104px,1fr)] gap-2 px-4 py-2.5 text-xs items-center">
                      {h.ticker === 'CASH' ? (
                        <span className="font-mono font-semibold text-[var(--text-primary)]">{h.ticker}</span>
                      ) : (
                        <Link href={`/stock/${h.ticker}`} className="font-mono font-semibold text-[var(--accent-cyan)] hover:underline">
                          {h.ticker}
                        </Link>
                      )}
                      <span className="text-[var(--text-secondary)] truncate">{h.company_name || h.notes || '—'}</span>
                      <span className="text-[var(--text-muted)] truncate">{h.account || 'Unknown'}</span>
                      <span className="text-right text-[var(--text-primary)]">${fmt(value)} <span className="text-[var(--text-muted)]">({pct.toFixed(1)}%)</span></span>
                      {h.ticker === 'CASH' ? (
                        <span />
                      ) : (
                        <GroupSelect
                          value={selectedSector}
                          options={groups}
                          loading={changingGroup === h.ticker}
                          onChange={g => changeGroup(h.ticker, g)}
                          size="sm"
                          align="right"
                          ariaLabel={`Change sector for ${h.ticker}`}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Account filter */}
      <div className="flex items-center gap-2">
        {ACCOUNTS.map(a => (
          <button
            key={a}
            onClick={() => setAcct(a)}
            className={`px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              acct === a
                ? 'bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
            }`}
          >
            {a}
            {a !== 'All' && (
              <span className="ml-1.5 text-[10px] opacity-60">
                {holdings.filter(h => h.account?.toLowerCase() === a.toLowerCase()).length}
              </span>
            )}
          </button>
        ))}
        {acct !== 'All' && (
          <span className="ml-auto text-xs text-[var(--text-muted)]">
            ${fmt(filteredValue)} of ${fmt(total_value)}
          </span>
        )}
      </div>

      {/* Holdings table */}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--glass-border)]">
              {['Account','Ticker','Company','Shares','Avg Cost','Lev','Price','Market Value','今日 Today','Weight','P&L $','P&L %','Fair Value (PE)','Valuation','備注','Vol','RSI','MA',''].map(h => (
                h === 'Weight' ? (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold whitespace-nowrap">
                    <button
                      onClick={() => setWeightDir(d => d === 'desc' ? 'asc' : 'desc')}
                      className="flex items-center gap-1 text-[var(--accent-purple)] hover:opacity-80 transition-opacity"
                    >
                      Weight {weightDir === 'desc' ? '↓' : '↑'}
                    </button>
                  </th>
                ) : (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
                )
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((h, i) => {
              const weight = total_value > 0 ? (h.market_value ?? h.cost_basis ?? 0) / total_value * 100 : null;
              const isCash = h.ticker === 'CASH';

              return (
                <tr key={i} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors group">
                  <td className="px-4 py-3 text-xs text-[var(--text-muted)]">{h.account}</td>
                  <td className="px-4 py-3">
                    {isCash ? (
                      <span className="font-bold text-[var(--text-secondary)]">CASH</span>
                    ) : (
                      <Link href={`/stock/${h.ticker}`}>
                        <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{h.ticker}</span>
                      </Link>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--text-secondary)] max-w-[140px] truncate">
                    {isCash ? 'Cash Balance' : (h.company_name || h.ticker)}
                  </td>

                  {/* Editable: Shares */}
                  <td className="px-3 py-2">
                    {isCash ? <span className="text-[var(--text-muted)] px-1">—</span> : (
                      <EditableCell
                        value={h.shares}
                        display={h.shares != null ? fmt(h.shares, 4).replace(/\.?0+$/, '') : '—'}
                        isEditing={editCell?.ticker === h.ticker && editCell?.account === h.account && editCell?.field === 'shares'}
                        editVal={editVal}
                        onStart={() => startCell(h.ticker, h.account, 'shares', h.shares)}
                        onChange={setEditVal}
                        onSave={saveCell}
                        onCancel={cancelCell}
                        width="w-20"
                      />
                    )}
                  </td>

                  {/* Editable: Avg Cost (or cash balance for CASH) */}
                  <td className="px-3 py-2">
                    <EditableCell
                      value={h.avg_cost}
                      display={h.avg_cost != null ? `$${fmt(h.avg_cost)}` : '—'}
                      isEditing={editCell?.ticker === h.ticker && editCell?.account === h.account && editCell?.field === 'avg_cost'}
                      editVal={editVal}
                      onStart={() => startCell(h.ticker, h.account, 'avg_cost', h.avg_cost)}
                      onChange={setEditVal}
                      onSave={saveCell}
                      onCancel={cancelCell}
                      width="w-24"
                    />
                  </td>

                  {/* Leverage */}
                  <td className="px-4 py-3 text-center">
                    {h.leverage > 1
                      ? <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.15)]">{h.leverage}×</span>
                      : <span className="text-[var(--text-muted)] text-xs">1×</span>}
                  </td>

                  <td className="px-4 py-3">
                    {h.current_price != null ? (
                      <div className="flex flex-col">
                        <span className="font-medium">${fmt(h.current_price)}</span>
                        {h.day_change != null && (
                          <span className={`text-[10px] font-medium ${h.day_change >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                            {h.day_change >= 0 ? '+' : ''}{fmt(h.day_change)} ({h.day_change_pct >= 0 ? '+' : ''}{fmt(h.day_change_pct)}%)
                          </span>
                        )}
                        {extendedQuotes[h.ticker] && (
                          <ExtendedQuoteBadge
                            quote={extendedQuotes[h.ticker]}
                            showPrice
                            upColor="#059669"
                            downColor="#dc2626"
                            className="mt-0.5"
                          />
                        )}
                      </div>
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>

                  <td className="px-4 py-3 font-medium">
                    {h.market_value != null ? `$${fmt(h.market_value)}` : <span className="text-[var(--text-muted)]">—</span>}
                  </td>

                  {/* 今日價值改變 — today's $ change of this position (day_change × shares) */}
                  <td className="px-4 py-3 font-medium">
                    {!isCash && h.day_change != null && h.shares != null ? (() => {
                      const dayVal = h.day_change * h.shares;
                      return (
                        <div className="flex flex-col leading-tight">
                          <span className={dayVal >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}>
                            {dayVal >= 0 ? '+' : ''}${fmt(dayVal)}
                          </span>
                          {h.day_change_pct != null && (
                            <span className={`text-[10px] ${h.day_change_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                              {h.day_change_pct >= 0 ? '+' : ''}{fmt(h.day_change_pct)}%
                            </span>
                          )}
                        </div>
                      );
                    })() : <span className="text-[var(--text-muted)]">—</span>}
                  </td>

                  <td className="px-4 py-3">
                    {weight != null ? (
                      <div className="flex flex-col gap-1 min-w-[64px]">
                        <span className="text-xs font-medium text-[var(--text-secondary)]">{fmt(weight, 1)}%</span>
                        <div className="h-1 rounded-full bg-[rgba(255,255,255,0.08)] overflow-hidden">
                          <div className="h-full rounded-full bg-[var(--accent-purple)]" style={{ width: `${Math.min(weight, 100)}%` }} />
                        </div>
                      </div>
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className={`px-4 py-3 font-medium ${h.unrealized_pnl == null ? '' : h.unrealized_pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                    {isCash ? <span className="text-[var(--text-muted)]">—</span>
                      : h.unrealized_pnl != null
                        ? <span className="flex items-center gap-1">
                            {h.unrealized_pnl >= 0 ? '+' : ''}${fmt(h.unrealized_pnl)}
                            {h.leverage > 1 && <span className="text-[9px] text-[var(--accent-yellow)] font-bold opacity-80">{h.leverage}×</span>}
                          </span>
                        : '—'}
                  </td>
                  <td className={`px-4 py-3 font-medium ${h.return_pct == null ? '' : h.return_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                    {isCash ? <span className="text-[var(--text-muted)]">—</span>
                      : h.return_pct != null
                        ? <span className="flex items-center gap-1">
                            {fmtPct(h.return_pct)}
                            {h.leverage > 1 && <span className="text-[9px] text-[var(--accent-yellow)] font-bold opacity-80">{h.leverage}×</span>}
                          </span>
                        : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {h.pe_median_price ? (
                      <div className="flex flex-col">
                        <span className="text-xs font-medium">${fmt(h.pe_median_price)}</span>
                        {h.pct_to_fair_pe != null && (
                          <span className={`text-[10px] ${h.pct_to_fair_pe >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                            {h.pct_to_fair_pe >= 0 ? '+' : ''}{h.pct_to_fair_pe}%
                          </span>
                        )}
                      </div>
                    ) : scrapingTickers.has(h.ticker) ? (
                      <RefreshCw size={10} className="animate-spin text-blue-400" />
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {scrapingTickers.has(h.ticker) && h.valuation_status == null
                      ? <RefreshCw size={10} className="animate-spin text-blue-400" />
                      : <StatusBadge status={h.valuation_status} />}
                  </td>

                  {/* 備注：技術分析文字 */}
                  <td className="px-4 py-3 max-w-[220px]">
                    {isCash ? null : (() => {
                      const note = buildTechNote(perHoldingTech[h.ticker]);
                      if (!note) return <span className="text-[var(--text-muted)] text-xs">—</span>;
                      const colorMap = {
                        red:    'text-[var(--accent-danger)]',
                        orange: 'text-[#fb923c]',
                        green:  'text-[var(--accent-green)]',
                        cyan:   'text-[var(--accent-cyan)]',
                        muted:  'text-[var(--text-muted)]',
                      };
                      return (
                        <span className={`text-[11px] leading-relaxed ${colorMap[note.tone]}`}>
                          {note.text}
                        </span>
                      );
                    })()}
                  </td>

                  {/* Vol ratio */}
                  <td className="px-3 py-3 text-center">
                    {(() => {
                      const tech = perHoldingTech[h.ticker];
                      const vr = tech?.volume_ratio;
                      if (isCash || vr == null) return <span className="text-[var(--text-muted)] text-xs">—</span>;
                      const cls = vr >= 3 ? 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.12)]'
                                : vr >= 2 ? 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.12)]'
                                : 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)]';
                      return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>{vr}×</span>;
                    })()}
                  </td>

                  {/* RSI-14 */}
                  <td className="px-3 py-3 text-center">
                    {(() => {
                      const tech = perHoldingTech[h.ticker];
                      const rsi = tech?.rsi_14;
                      if (isCash || rsi == null) return <span className="text-[var(--text-muted)] text-xs">—</span>;
                      const cls = rsi >= 70 ? 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.12)]'
                                : rsi <= 30 ? 'text-[var(--accent-green)] bg-[rgba(16,185,129,0.12)]'
                                : 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)]';
                      return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>{rsi}</span>;
                    })()}
                  </td>

                  {/* MA cross / MA20 deviation */}
                  <td className="px-3 py-3 text-center">
                    {(() => {
                      const tech = perHoldingTech[h.ticker];
                      if (isCash || !tech) return <span className="text-[var(--text-muted)] text-xs">—</span>;
                      const cross = tech.ma_cross;
                      const dev = tech.price_vs_ma20_pct;
                      if (cross === 'golden') return (
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded text-[var(--accent-green)] bg-[rgba(16,185,129,0.15)]">▲金叉</span>
                      );
                      if (cross === 'death') return (
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded text-[var(--accent-danger)] bg-[rgba(248,113,113,0.15)]">▼死叉</span>
                      );
                      if (dev == null) return <span className="text-[var(--text-muted)] text-xs">—</span>;
                      const cls = dev >= 15  ? 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.12)]'
                                : dev <= -10 ? 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.10)]'
                                : dev >= 0   ? 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)]'
                                :              'text-[var(--text-secondary)] bg-[rgba(255,255,255,0.04)]';
                      return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cls}`} title="Distance from MA20">{dev > 0 ? '+' : ''}{dev}%</span>;
                    })()}
                  </td>

                  <td className="px-4 py-3">
                    <button
                      onClick={() => deleteHolding(h.ticker, h.account)}
                      className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all"
                      title="Remove from portfolio"
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              );
            })}

            {/* Add row */}
            {adding ? (
              <tr className="border-t-2 border-[var(--accent-purple)] bg-[rgba(139,92,246,0.04)]">
                <td className="px-4 py-2">
                  <select
                    value={addVals.account}
                    onChange={e => setAddVals(v => ({ ...v, account: e.target.value }))}
                    className="w-full px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  >
                    <option>Firstrade</option>
                    <option>eToro</option>
                  </select>
                </td>
                <td className="px-4 py-2">
                  <input
                    autoFocus
                    type="text" placeholder="TICKER"
                    value={addVals.ticker}
                    onChange={e => setAddVals(v => ({ ...v, ticker: e.target.value.toUpperCase() }))}
                    className="w-20 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-xs outline-none font-bold uppercase"
                  />
                </td>
                <td className="px-4 py-2 text-xs text-[var(--text-muted)]">—</td>
                <td className="px-4 py-2">
                  <input
                    type="number" step="any" placeholder="Shares"
                    value={addVals.shares}
                    onChange={e => setAddVals(v => ({ ...v, shares: e.target.value }))}
                    className="w-20 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="number" step="any" placeholder="Avg cost"
                    value={addVals.avg_cost}
                    onChange={e => setAddVals(v => ({ ...v, avg_cost: e.target.value }))}
                    className="w-24 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td colSpan={8} className="px-4 py-2">
                  {addError && <span className="text-[10px] text-[var(--accent-danger)]">{addError}</span>}
                </td>
                <td className="px-4 py-2">
                  <input
                    type="text" placeholder="Notes (optional)"
                    value={addVals.notes}
                    onChange={e => setAddVals(v => ({ ...v, notes: e.target.value }))}
                    className="w-32 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={saveAdd}
                      disabled={addSaving}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 transition-all disabled:opacity-50"
                    >
                      <Check size={12} />
                      {addSaving ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      onClick={() => { setAdding(false); setAddVals(EMPTY_ADD); setAddError(''); }}
                      className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
                      title="Cancel"
                    >
                      <X size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ) : (
              <tr className="border-t border-[var(--glass-border)]">
                <td colSpan={15} className="px-4 py-2">
                  <button
                    onClick={() => { setAdding(true); setAddVals({ ...EMPTY_ADD, portfolio: selected }); }}
                    className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent-purple)] transition-colors py-1"
                  >
                    <Plus size={13} /> Add ticker
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EditableCell({ value, display, isEditing, editVal, onStart, onChange, onSave, onCancel, width = 'w-20' }) {
  if (isEditing) {
    return (
      <input
        autoFocus
        type="number"
        step="any"
        value={editVal}
        onChange={e => onChange(e.target.value)}
        onBlur={onSave}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.target.blur(); }
          if (e.key === 'Escape') { onCancel(); }
        }}
        className={`${width} px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-xs outline-none text-[var(--text-primary)]`}
      />
    );
  }
  return (
    <span
      onClick={onStart}
      title="Click to edit"
      className="cursor-text px-1 py-0.5 rounded hover:bg-[rgba(139,92,246,0.12)] hover:text-[var(--text-primary)] transition-colors text-[var(--text-secondary)] text-xs"
    >
      {display}
    </span>
  );
}

function SummaryCard({ icon, label, value, subValue, subValueColor, subValue2, subValue2Color, color }) {
  const colorMap = {
    cyan:   'from-[var(--accent-cyan)] to-[#0891b2]',
    purple: 'from-[var(--accent-purple)] to-[#7c3aed]',
    green:  'from-[var(--accent-green)] to-[#059669]',
    red:    'from-[var(--accent-danger)] to-[#dc2626]',
    yellow: 'from-[var(--accent-yellow)] to-[#d97706]',
    orange: 'from-[#f97316] to-[#ea580c]',
  };
  const textColorMap = {
    cyan:   'text-[var(--accent-cyan)]',
    purple: 'text-[var(--accent-purple)]',
    green:  'text-[var(--accent-green)]',
    red:    'text-[var(--accent-danger)]',
    yellow: 'text-[var(--accent-yellow)]',
    orange: 'text-[#f97316]',
  };
  return (
    <div className="card p-4 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${colorMap[color]} flex items-center justify-center shrink-0 shadow-md`}>
        <span className="text-white">{icon}</span>
      </div>
      <div>
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">{label}</p>
        <p className={`text-lg font-extrabold ${textColorMap[color]}`}>{value}</p>
        {subValue && (
          <p className={`text-[10px] mt-0.5 ${subValueColor ? textColorMap[subValueColor] : 'text-[var(--text-muted)]'}`}>
            {subValue}
          </p>
        )}
        {subValue2 && (
          <p className={`text-[10px] mt-0.5 ${subValue2Color ? textColorMap[subValue2Color] : 'text-[var(--text-muted)]'}`}>
            {subValue2}
          </p>
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value, sub, tone = 'neutral' }) {
  // Only directional stats get colour. Cash % and position count are facts, not
  // gains, so they stay neutral — otherwise the palette stops meaning anything.
  const toneMap = {
    neutral: 'text-[var(--text-primary)]',
    green: 'text-[var(--accent-green)]',
    red: 'text-[var(--accent-danger)]',
  };
  return (
    <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-3 py-2.5">
      <p className="text-[11px] font-medium text-[var(--text-secondary)] truncate" title={label}>{label}</p>
      <p className={`mt-1 text-lg font-bold font-mono tabular-nums leading-none ${toneMap[tone] || toneMap.neutral}`}>
        {value}
      </p>
      <p className="mt-1 text-[11px] font-mono tabular-nums text-[var(--text-muted)]">{sub}</p>
    </div>
  );
}

function ToneBadge({ text, tone }) {
  const cls = tone === 'green'
    ? 'bg-[rgba(16,185,129,0.14)] text-[var(--accent-green)]'
    : tone === 'red'
      ? 'bg-[rgba(248,113,113,0.14)] text-[var(--accent-danger)]'
      : tone === 'orange'
        ? 'bg-[rgba(249,115,22,0.14)] text-[#fb923c]'
        : 'bg-[rgba(6,182,212,0.14)] text-[var(--accent-cyan)]';
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${cls}`}>{text}</span>;
}

function FlowRow({ label, tone, items }) {
  return (
    <div className="flex items-start gap-2">
      <ToneBadge text={label} tone={tone} />
      <div className="flex-1 flex flex-wrap gap-1">
        {items.length === 0 ? (
          <span className="text-xs text-[var(--text-muted)]">—</span>
        ) : (
          items.slice(0, 6).map((t) => (
            <span key={t} className="text-[11px] px-2 py-0.5 rounded-md bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
              {t}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function FlowDeltaRow({ label, tone, items }) {
  return (
    <div className="flex items-start gap-2">
      <ToneBadge text={label} tone={tone} />
      <div className="flex-1 flex flex-wrap gap-1">
        {items.length === 0 ? (
          <span className="text-xs text-[var(--text-muted)]">—</span>
        ) : (
          items.slice(0, 6).map((x) => (
            <span key={x.ticker} className="text-[11px] px-2 py-0.5 rounded-md bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
              {x.ticker} {x.share_diff > 0 ? '+' : ''}{x.share_diff}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function WeightRow({ label, tone, items }) {
  return (
    <div className="flex items-start gap-2">
      <ToneBadge text={label} tone={tone} />
      <div className="flex-1 flex flex-wrap gap-1">
        {items.length === 0 ? (
          <span className="text-xs text-[var(--text-muted)]">—</span>
        ) : (
          items.slice(0, 6).map((x) => (
            <span key={x.ticker} className="text-[11px] px-2 py-0.5 rounded-md bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
              {x.ticker} {x.weight_diff_pct > 0 ? '+' : ''}{fmt(x.weight_diff_pct, 2)} pts
            </span>
          ))
        )}
      </div>
    </div>
  );
}

// All eight categories the API returns, so the tally reconciles with the signal
// count in the section header. The old panel showed five and silently dropped 15
// of the 22 signals it claimed to summarise.
const RADAR_CATS = [
  { key: 'breakouts',       label: '突破',   dir: 'bull'    },
  { key: 'breakdowns',      label: '跌破',   dir: 'bear'    },
  { key: 'ma_crosses',      label: '均線',   dir: 'neutral' },
  { key: 'rsi_extreme',     label: 'RSI',    dir: 'watch'   },
  { key: 'volume_spikes',   label: '爆量',   dir: 'watch'   },
  { key: 'near_support',    label: '近支撐', dir: 'bull'    },
  { key: 'near_resistance', label: '近壓力', dir: 'watch'   },
  { key: 'large_moves',     label: '大漲跌', dir: 'neutral' },
];

// Shared column track so the header labels and every row line up exactly.
const RADAR_COLS = 'md:grid-cols-[4.25rem_5.5rem_4.75rem_3.75rem_4.5rem_minmax(0,1fr)]';

function RiskAlerts({ flags }) {
  // Severity first: a real warning should not sit below fifteen routine notes.
  const RANK = { high: 0, bear: 1, medium: 2, info: 3, bull: 4 };
  const ordered = useMemo(
    () => [...flags].sort((a, b) => (RANK[a.level] ?? 9) - (RANK[b.level] ?? 9)),
    [flags], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const urgent = ordered.filter(f => f.level === 'high' || f.level === 'bear');
  const rest = ordered.filter(f => !(f.level === 'high' || f.level === 'bear'));
  const [showAll, setShowAll] = useState(false);
  const dirOf = (lvl) => (lvl === 'high' || lvl === 'bear' ? 'bear' : lvl === 'medium' ? 'watch' : lvl === 'bull' ? 'bull' : 'neutral');

  // A coloured rule instead of a full bordered box per row: 15 outlined pills
  // stacked full-width was more frame than content.
  const Row = (flag, i) => {
    const d = DIR[dirOf(flag.level)];
    return (
      <div key={i} className="flex items-start gap-2 py-1">
        <span className={`mt-[5px] h-3 w-[3px] shrink-0 rounded-full ${d.bar}`} />
        <span className={`text-xs leading-relaxed ${d.text}`}>{flag.message}</span>
      </div>
    );
  };

  return (
    <div className="border-t border-[var(--glass-border)] pt-4 space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-bold text-[var(--text-primary)]">風險警示 · Risk Alerts</p>
        <span className="font-mono tabular-nums text-[11px] text-[var(--text-muted)]">
          {urgent.length} 緊急 / {flags.length} 則
        </span>
      </div>

      {urgent.length > 0 && (
        <div className={`rounded-xl border px-3 py-2 ${DIR.bear.ring} ${DIR.bear.wash}`}>
          {urgent.map(Row)}
        </div>
      )}

      {rest.length > 0 && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
            {(showAll ? rest : rest.slice(0, 6)).map(Row)}
          </div>
          {rest.length > 6 && (
            <button
              type="button"
              onClick={() => setShowAll(v => !v)}
              className="text-[11px] font-semibold text-[var(--accent-cyan)] hover:underline"
            >
              {showAll ? '收合' : `顯示其餘 ${rest.length - 6} 則`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function AbnormalStocksPanel({ abnormal }) {
  const [activeCat, setActiveCat] = useState(null);
  const categories = abnormal?.categories;

  // ticker → which categories it fired, so the tally can filter the list below
  // instead of just restating it.
  const catsByTicker = useMemo(() => {
    const map = {};
    RADAR_CATS.forEach(c => ((categories?.[c.key]) || []).forEach(x => {
      if (!map[x.ticker]) map[x.ticker] = new Set();
      map[x.ticker].add(c.key);
    }));
    return map;
  }, [categories]);

  // Largest portfolio impact first. The API order buries a 6.6% position that
  // dropped 4.8% underneath a 1.1% position that ticked up.
  const rows = useMemo(() => {
    const all = abnormal?.summary || [];
    const list = activeCat ? all.filter(s => catsByTicker[s.ticker]?.has(activeCat)) : all;
    return [...list].sort((a, b) => Math.abs(impactBp(b) ?? 0) - Math.abs(impactBp(a) ?? 0));
  }, [abnormal, activeCat, catsByTicker]);

  return (
    <div className="space-y-3">
      {/* Signal tally, doubling as a filter */}
      <div className="flex flex-wrap items-center gap-1.5">
        {RADAR_CATS.map(cat => {
          const n = ((categories?.[cat.key]) || []).length;
          const on = activeCat === cat.key;
          const d = DIR[cat.dir];
          return (
            <button
              key={cat.key}
              type="button"
              disabled={n === 0}
              aria-pressed={on}
              onClick={() => setActiveCat(on ? null : cat.key)}
              className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-colors
                ${n === 0
                  ? 'border-[var(--glass-border)] text-[var(--text-muted)] opacity-55 cursor-default'
                  : on
                    ? `${d.ring} ${d.text} bg-[var(--bg-tertiary)]`
                    : 'border-[var(--glass-border)] text-[var(--text-secondary)] hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]'}`}
            >
              <span>{cat.label}</span>
              <span className={`font-mono tabular-nums ${n > 0 && !on ? d.text : ''}`}>{n}</span>
            </button>
          );
        })}
        {activeCat && (
          <button
            type="button"
            onClick={() => setActiveCat(null)}
            className="text-[11px] font-medium text-[var(--accent-cyan)] hover:underline px-1"
          >
            顯示全部
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-xs text-[var(--text-muted)]">
          此分類沒有持倉中的個股觸發訊號。
        </p>
      ) : (
        <div>
          <div className={`hidden md:grid ${RADAR_COLS} gap-x-3 px-2 pb-1.5 text-[10px] font-semibold text-[var(--text-muted)] border-b border-[var(--glass-border)]`}>
            <span>代號</span>
            <span className="text-right">股價</span>
            <span className="text-right">單日</span>
            <span className="text-right">持倉</span>
            <span className="text-right" title="持倉比重 × 單日漲跌，對整體投組的影響（基點）">影響 bp</span>
            <span>訊號</span>
          </div>
          <div className="divide-y divide-[rgba(148,163,184,0.22)]">
            {rows.map(item => <AbnormalStockRow key={item.ticker} item={item} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function AbnormalStockRow({ item }) {
  const bp = impactBp(item);
  const chgDir = item.day_change_pct == null ? 'neutral' : item.day_change_pct >= 0 ? 'bull' : 'bear';

  const Ticker = (
    <Link href={`/stock/${item.ticker}`}>
      <span className="font-mono text-[13px] font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">
        {item.ticker}
      </span>
    </Link>
  );
  const Price = (
    <span className="font-mono tabular-nums text-[12px] text-[var(--text-primary)]">
      {item.price != null ? `$${fmt(item.price)}` : '—'}
    </span>
  );
  const Chg = (
    <span className={`font-mono tabular-nums text-[12px] font-semibold ${DIR[chgDir].text}`}>
      {fmtPct(item.day_change_pct)}
    </span>
  );
  const Weight = (
    <span className="font-mono tabular-nums text-[12px] text-[var(--text-secondary)]">
      {item.weight_pct != null ? `${fmt(item.weight_pct, 1)}%` : '—'}
    </span>
  );
  const Impact = (
    <span className={`font-mono tabular-nums text-[12px] font-semibold ${DIR[bp == null ? 'neutral' : bp >= 0 ? 'bull' : 'bear'].text}`}>
      {fmtBp(bp)}
    </span>
  );

  return (
    <div className="px-2 py-2 hover:bg-[var(--bg-secondary)] transition-colors">
      {/* Desktop: aligned columns so prices and percentages read down the page */}
      <div className={`hidden md:grid ${RADAR_COLS} gap-x-3 items-center`}>
        {Ticker}
        <div className="text-right">{Price}</div>
        <div className="text-right">{Chg}</div>
        <div className="text-right">{Weight}</div>
        <div className="text-right">{Impact}</div>
        <SignalChips signals={item.signals} />
      </div>

      {/* < 768px: identity line, then signals */}
      <div className="md:hidden space-y-1.5">
        <div className="flex items-baseline gap-2 flex-wrap">
          {Ticker}
          {Price}
          {Chg}
          <span className="ml-auto flex items-baseline gap-2">
            <span className="text-[10px] text-[var(--text-muted)]">持倉</span>
            {Weight}
            <span className="text-[10px] text-[var(--text-muted)]">影響</span>
            {Impact}
          </span>
        </div>
        <SignalChips signals={item.signals} max={3} />
      </div>
    </div>
  );
}

function classifyInsight(ins) {
  const rsi = ins.rsi ?? 50;
  const dayChg = ins.day_change_pct ?? 0;
  const hasDeath = ins.signals.some(s => s.tag.includes('死亡交叉'));
  const isCritical =
    Math.abs(dayChg) >= 5 ||
    (ins.direction === 'bear' && ins.weight_pct >= 5) ||
    rsi >= 85 ||
    hasDeath;
  if (isCritical) return 'critical';
  if (ins.direction === 'bull') return 'opportunity';
  return 'watch';
}

function StockInsightsGrouped({ insights }) {
  const groups = { critical: [], watch: [], opportunity: [] };
  insights.forEach(ins => groups[classifyInsight(ins)].push(ins));

  const sections = [
    { key: 'critical',    label: '重點關注', sub: '需優先處理',     dir: 'bear'  },
    { key: 'watch',       label: '留意觀察', sub: '訊號偏空或混合', dir: 'watch' },
    { key: 'opportunity', label: '潛在機會', sub: '技術面偏多',     dir: 'bull'  },
  ];

  return (
    <div className="space-y-5">
      {sections.map(sec => {
        const items = groups[sec.key];
        if (items.length === 0) return null;
        const d = DIR[sec.dir];
        // Biggest position first — a 7% holding outranks a 1% holding.
        const ordered = [...items].sort((a, b) => (b.weight_pct ?? 0) - (a.weight_pct ?? 0));
        return (
          <div key={sec.key}>
            <div className="flex items-baseline gap-2 mb-2.5 pb-1.5 border-b border-[var(--glass-border)]">
              <span className={`self-center h-3.5 w-1 rounded-full ${d.bar}`} />
              <span className={`text-[13px] font-bold ${d.text}`}>{sec.label}</span>
              <span className="text-[11px] text-[var(--text-muted)]">{sec.sub}</span>
              <span className="ml-auto font-mono tabular-nums text-[11px] font-semibold text-[var(--text-secondary)]">
                {ordered.length} 檔
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
              {ordered.map(ins => (
                <StockInsightCard key={ins.ticker} ins={ins} priority={sec.key} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StockInsightCard({ ins, priority }) {
  const dirMap = {
    bull:    { label: '偏多', dir: 'bull' },
    bear:    { label: '偏空', dir: 'bear' },
    neutral: { label: '中性', dir: 'neutral' },
  };
  const priorityDir = { critical: 'bear', watch: 'watch', opportunity: 'bull' };
  const d = dirMap[ins.direction] || dirMap.neutral;
  const dc = DIR[d.dir];
  const p = DIR[priorityDir[priority] || 'watch'];
  const chgDir = ins.day_change_pct == null ? 'neutral' : ins.day_change_pct >= 0 ? 'bull' : 'bear';
  const bp = impactBp(ins);

  return (
    <div className={`rounded-xl border flex overflow-hidden ${p.ring} ${p.wash}`}>
      {/* Left rule repeats the group's direction so a card stays readable out of context. */}
      <div className={`w-1 shrink-0 ${p.bar}`} />
      <div className="flex-1 p-3.5 space-y-2.5 min-w-0">
        <div className="flex items-baseline justify-between gap-2 flex-wrap">
          <div className="flex items-baseline gap-2">
            <Link href={`/stock/${ins.ticker}`}>
              <span className="font-mono text-[15px] font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{ins.ticker}</span>
            </Link>
            <span className="font-mono tabular-nums text-[12px] text-[var(--text-primary)]">
              {ins.price != null ? `$${fmt(ins.price)}` : '—'}
            </span>
            <span className={`font-mono tabular-nums text-[13px] font-bold ${DIR[chgDir].text}`}>
              {fmtPct(ins.day_change_pct)}
            </span>
          </div>
          <span className={`shrink-0 text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${dc.text} ${dc.chip}`}>
            {d.label}
          </span>
        </div>

        {/* Position context: how much of the portfolio this is, and what it did to it today. */}
        {ins.weight_pct > 0 && (
          <div className="flex items-baseline gap-3 text-[11px] text-[var(--text-muted)]">
            <span>
              持倉 <span className="font-mono tabular-nums text-[var(--text-secondary)]">{fmt(ins.weight_pct, 1)}%</span>
            </span>
            {bp != null && (
              <span>
                今日影響{' '}
                <span className={`font-mono tabular-nums font-semibold ${DIR[bp >= 0 ? 'bull' : 'bear'].text}`}>{fmtBp(bp)} bp</span>
              </span>
            )}
          </div>
        )}

        <SignalChips signals={(ins.signals || []).map(s => ({ label: s.tag, tone: s.tone }))} max={4} />

        {ins.suggestion && <SuggestionList text={ins.suggestion} />}
      </div>
    </div>
  );
}

function TechSignalsByTicker({ techSignals }) {
  const tickerMap = {};
  const add = (list, label, tone, detail) => {
    (list || []).forEach(x => {
      if (!tickerMap[x.ticker]) tickerMap[x.ticker] = [];
      tickerMap[x.ticker].push({ label, tone, detail: detail ? detail(x) : null });
    });
  };
  add(techSignals.golden_cross,    '↑ 黃金交叉', 'cyan');
  add(techSignals.death_cross,     '↓ 死亡交叉', 'red');
  add(techSignals.breakouts,        '↑ 突破壓力', 'cyan',   x => `$${x.price}`);
  add(techSignals.breakdowns,       '↓ 跌破支撐', 'red',    x => `$${x.price}`);
  add(techSignals.rsi_oversold,    '↑ RSI 超賣', 'green',  x => `RSI ${x.rsi}`);
  add(techSignals.rsi_overbought,  '↓ RSI 超買', 'orange', x => `RSI ${x.rsi}`);
  add(techSignals.near_support,    '↑ 近支撐',   'green',  x => `$${x.price} ↓${x.dist_pct}%`);
  add(techSignals.near_resistance, '↓ 近壓力',   'orange', x => `$${x.price} ↑${x.dist_pct}%`);
  add(techSignals.volume_anomalies,'量能異常',   'yellow', x => `${x.volume_ratio}×`);

  const toneMap = {
    cyan:   { text: 'text-[var(--accent-cyan)]',   bg: 'bg-[rgba(6,182,212,0.12)]',   border: 'border-[rgba(6,182,212,0.20)]' },
    green:  { text: 'text-[var(--accent-green)]',  bg: 'bg-[rgba(16,185,129,0.12)]',  border: 'border-[rgba(16,185,129,0.20)]' },
    orange: { text: 'text-[var(--accent-yellow)]', bg: 'bg-[rgba(249,115,22,0.12)]',  border: 'border-[rgba(249,115,22,0.20)]' },
    red:    { text: 'text-[var(--accent-danger)]', bg: 'bg-[rgba(248,113,113,0.12)]', border: 'border-[rgba(248,113,113,0.20)]' },
    yellow: { text: 'text-[var(--accent-yellow)]', bg: 'bg-[rgba(245,158,11,0.12)]',  border: 'border-[rgba(245,158,11,0.20)]' },
  };

  const tickers = Object.keys(tickerMap).sort();
  return (
    <div className="flex flex-wrap gap-2">
      {tickers.map(ticker => (
        <div key={ticker} className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-3 py-2.5 min-w-[130px]">
          <p className="text-xs font-bold text-[var(--accent-cyan)] mb-2">
            <Link href={`/stock/${ticker}`}><span className="hover:underline cursor-pointer">{ticker}</span></Link>
          </p>
          <div className="flex flex-wrap gap-1">
            {tickerMap[ticker].map((sig, i) => {
              const t = toneMap[sig.tone] || toneMap.yellow;
              return (
                <span key={i} className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${t.text} ${t.bg}`}>
                  {sig.label}{sig.detail ? ` ${sig.detail}` : ''}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function TechSignalGroup({ label, tone, items }) {
  // tone semantics:
  //   cyan   → 多方突破 bullish breakout (golden cross)
  //   green  → 反彈機會 rebound opportunity (oversold)
  //   orange → 超買警告 overbought warning
  //   red    → 空方訊號 bearish signal (death cross)
  //   yellow → 量能異常 volume anomaly
  const toneMap = {
    cyan:   { text: 'text-[var(--accent-cyan)]',    bg: 'bg-[rgba(6,182,212,0.10)]',   border: 'border-[rgba(6,182,212,0.20)]' },
    green:  { text: 'text-[var(--accent-green)]',   bg: 'bg-[rgba(16,185,129,0.10)]',  border: 'border-[rgba(16,185,129,0.20)]' },
    orange: { text: 'text-[var(--accent-yellow)]',  bg: 'bg-[rgba(249,115,22,0.10)]',  border: 'border-[rgba(249,115,22,0.20)]' },
    red:    { text: 'text-[var(--accent-danger)]',  bg: 'bg-[rgba(248,113,113,0.10)]', border: 'border-[rgba(248,113,113,0.20)]' },
    yellow: { text: 'text-[var(--accent-yellow)]',  bg: 'bg-[rgba(245,158,11,0.10)]',  border: 'border-[rgba(245,158,11,0.20)]' },
  };
  const t = toneMap[tone] || toneMap.yellow;
  return (
    <div className={`rounded-xl p-3 space-y-1.5 border ${t.border} ${t.bg}`}>
      <p className={`text-[10px] font-semibold uppercase tracking-wide ${t.text}`}>{label}</p>
      <div className="flex flex-wrap gap-1">
        {items.map((item, i) => (
          <span key={i} className={`text-[11px] font-semibold px-1.5 py-0.5 rounded ${t.text} ${t.bg}`}>{item}</span>
        ))}
      </div>
    </div>
  );
}
