import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  RefreshCw, Target, ChevronDown, ChevronUp, Briefcase, Eye,
} from 'lucide-react';

// ── Helpers ───────────────────────────────────────────────────────────────────

function pctColor(v) {
  if (v == null) return 'var(--text-muted)';
  return v >= 0 ? '#34d399' : '#f87171';
}

// 當前動作燈號（對齊 mimi 操作建議總表）
const LIGHTS = {
  '🟢': { label: '佈局/持有', color: '#34d399', bg: 'rgba(52,211,153,0.12)',  border: 'rgba(52,211,153,0.30)' },
  '🟡': { label: '轉保守',    color: '#fbbf24', bg: 'rgba(251,191,36,0.12)',  border: 'rgba(251,191,36,0.30)' },
  '🟠': { label: '減碼',      color: '#fb923c', bg: 'rgba(251,146,60,0.12)',  border: 'rgba(251,146,60,0.30)' },
  '🔴': { label: '出場/避開', color: '#f87171', bg: 'rgba(248,113,113,0.12)', border: 'rgba(248,113,113,0.30)' },
  '⚪': { label: '觀察',      color: '#94a3b8', bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.30)' },
};

function returnColor(s) {
  if (!s) return 'var(--text-muted)';
  return s.trim().startsWith('-') ? '#f87171' : '#34d399';
}

// 從 direction 取動作動詞（"佈局——…" / "長線減碼、…" → 佈局 / 長線減碼）
function actionVerb(direction) {
  if (!direction) return '';
  return direction.split(/——|—|，|、|（|\(/)[0].trim().slice(0, 10);
}

function ActionBadge({ light, direction }) {
  const meta = LIGHTS[light];
  const verb = actionVerb(direction);
  if (!light && !verb) return null;
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border"
      style={{
        background: meta?.bg ?? 'var(--card-bg)',
        color: meta?.color ?? 'var(--text-secondary)',
        borderColor: meta?.border ?? 'var(--border)',
      }}
    >
      {light} {verb || meta?.label}
    </span>
  );
}

function StatusBadge({ item }) {
  if (item.held)
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-500/15 text-blue-400 border border-blue-500/25">
        <Briefcase size={10} /> 已持有
      </span>
    );
  if (item.near_entry)
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-400/15 text-amber-400 border border-amber-400/25">
        <Target size={10} /> 接近進場
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-white/5 text-[var(--text-muted)] border border-white/10">
      <Eye size={10} /> 觀察中
    </span>
  );
}

function WatchlistRow({ item }) {
  const [expanded, setExpanded] = useState(false);
  const chg = item.change_pct;

  return (
    <div
      className="rounded-xl border transition-all"
      style={{
        background: item.near_entry ? 'rgba(251,191,36,0.04)' : item.held ? 'rgba(59,130,246,0.04)' : 'var(--card-bg)',
        borderColor: item.near_entry ? 'rgba(251,191,36,0.2)' : item.held ? 'rgba(59,130,246,0.2)' : 'var(--border)',
      }}
    >
      {/* Main row */}
      <button
        className="w-full text-left px-4 py-3 flex items-center gap-3"
        onClick={() => setExpanded(e => !e)}
      >
        {/* Ticker + meta */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Link
              href={`/stock/${item.ticker}`}
              onClick={e => e.stopPropagation()}
              className="font-bold text-sm text-[var(--text-primary)] hover:text-blue-400 underline-offset-2 hover:underline transition-colors"
            >
              {item.ticker}
            </Link>
            <ActionBadge light={item.action_light} direction={item.direction} />
            <StatusBadge item={item} />
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {item.company_name && (
              <span className="text-xs text-[var(--text-secondary)] truncate">{item.company_name}</span>
            )}
            {item.rec_date && (
              <span className="text-[10px] text-[var(--text-muted)] shrink-0">
                首推 {item.rec_date}
                {item.date_updated ? ` · 更新 ${item.date_updated}` : ''}
              </span>
            )}
          </div>
          {item.direction && (
            <div className="text-[11px] mt-0.5 truncate text-[var(--text-secondary)]">{item.direction}</div>
          )}
        </div>

        {/* Price + 自首推報酬率 */}
        <div className="text-right shrink-0">
          {item.price != null ? (
            <>
              <div className="font-mono font-bold text-sm text-[var(--text-primary)]">
                ${item.price.toFixed(2)}
              </div>
              {chg != null && (
                <div className="text-xs font-medium" style={{ color: pctColor(chg) }}>
                  {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-[var(--text-muted)]">—</div>
          )}
          {item.return_pct && (
            <div
              className="mt-0.5 text-[11px] font-bold font-mono"
              style={{ color: returnColor(item.return_pct) }}
              title="首推至今報酬率（yfinance）"
            >
              自首推 {item.return_pct}
            </div>
          )}
        </div>

        {/* Expand toggle */}
        <div className="text-[var(--text-muted)] shrink-0 ml-1">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-white/5 pt-3 space-y-2 text-xs">
          {/* 首推 → 現 + 報酬率 */}
          {(item.price_rec != null || item.return_pct) && (
            <div className="flex items-center gap-1.5 flex-wrap pb-1 text-[11px]">
              {item.price_rec != null && (
                <span className="text-[var(--text-muted)]">
                  首推 <span className="font-mono text-[var(--text-secondary)]">${item.price_rec.toFixed(2)}</span>
                </span>
              )}
              {item.price != null && (
                <span className="text-[var(--text-muted)]">
                  → 現 <span className="font-mono text-[var(--text-secondary)]">${item.price.toFixed(2)}</span>
                </span>
              )}
              {item.return_pct && (
                <span className="font-bold font-mono" style={{ color: returnColor(item.return_pct) }}>
                  {item.return_pct}
                </span>
              )}
            </div>
          )}
          {item.target_price && <DetailRow label="目標價" value={item.target_price} accent="green" />}
          {item.time_frame && <DetailRow label="時間框架" value={item.time_frame} accent="blue" />}
          {item.catalyst && <DetailRow label="催化劑" value={item.catalyst} />}
          <DetailRow label="進場區" value={item.entry_zone || '—'} highlight={item.near_entry} />
          <DetailRow label="停損參考" value={item.stop_loss || '—'} />
          {item.position_size && <DetailRow label="建議倉位" value={item.position_size} />}

          {/* 觀點演變（不同時期論點 × 差異對比） */}
          {item.history?.length > 1 && (
            <div className="mt-2">
              <span className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wide">
                觀點演變（{item.history.length} 期）
              </span>
              <div className="mt-1.5 space-y-2 border-l border-white/10 pl-3 ml-1">
                {item.history.map((h, idx) => (
                  <div key={idx} className="relative">
                    <span className="absolute -left-[15px] top-1.5 w-1.5 h-1.5 rounded-full bg-white/40" />
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[10px] font-mono text-[var(--text-muted)]">{h.date}</span>
                      {h.price && (
                        <span className="text-[10px] font-mono text-[var(--text-secondary)]">{h.price}</span>
                      )}
                      {h.action && <span className="text-[10px] font-medium">{h.action}</span>}
                    </div>
                    {h.change && h.change !== '— 起點' && (
                      <div className="text-[10px] text-[var(--text-secondary)] leading-snug mt-0.5">
                        ↳ {h.change}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {item.logic && (
            <div className="rounded-lg px-3 py-2 mt-1"
              style={{ background: 'rgba(59,130,246,0.06)', borderLeft: '2px solid rgba(59,130,246,0.35)' }}>
              <span className="text-[10px] font-semibold text-[var(--accent-cyan-text)] uppercase tracking-wide">操作邏輯</span>
              <div className="text-[var(--text-primary)] mt-0.5 leading-relaxed whitespace-pre-wrap text-xs">{item.logic}</div>
            </div>
          )}
          {item.risk && (
            <div className="rounded-lg px-3 py-2"
              style={{ background: 'rgba(220,38,38,0.05)', borderLeft: '2px solid rgba(220,38,38,0.35)' }}>
              <span className="text-[10px] font-semibold text-[var(--accent-danger-text)] uppercase tracking-wide">核心風險</span>
              <div className="text-[var(--text-primary)] mt-0.5 leading-relaxed whitespace-pre-wrap text-xs">{item.risk}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DetailRow({ label, value, highlight, accent }) {
  const accentColor = accent === 'green' ? '#059669' : accent === 'blue' ? '#2563eb' : undefined;
  return (
    <div className="flex gap-2">
      <span className="text-[var(--text-muted)] shrink-0 w-16">{label}</span>
      <span
        className="text-[var(--text-primary)] leading-relaxed"
        style={highlight ? { color: '#b45309', fontWeight: 600 } : accentColor ? { color: accentColor, fontWeight: 600 } : undefined}
      >
        {value}
      </span>
    </div>
  );
}

function SectorGroup({ sector, items }) {
  const [collapsed, setCollapsed] = useState(false);
  const nearCount = items.filter(i => i.near_entry && !i.held).length;
  const heldCount = items.filter(i => i.held).length;

  return (
    <div className="mb-5">
      {/* Sector header */}
      <button
        className="w-full flex items-center gap-2 mb-2 group"
        onClick={() => setCollapsed(c => !c)}
      >
        <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
          {sector}
        </span>
        <span className="text-[10px] text-[var(--text-secondary)]">({items.length})</span>
        {nearCount > 0 && (
          <span className="text-[10px] bg-amber-400/15 text-amber-400 px-1.5 py-px rounded-full">
            ⚠️ {nearCount}
          </span>
        )}
        {heldCount > 0 && (
          <span className="text-[10px] bg-blue-500/15 text-blue-400 px-1.5 py-px rounded-full">
            持 {heldCount}
          </span>
        )}
        <div className="flex-1 h-px bg-white/5 ml-1" />
        <span className="text-[var(--text-muted)] opacity-40 group-hover:opacity-70 transition-opacity">
          {collapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
        </span>
      </button>

      {!collapsed && (
        <div className="space-y-2">
          {items.map(item => <WatchlistRow key={item.ticker} item={item} />)}
        </div>
      )}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

const FILTERS = [
  { key: 'all',    label: '全部' },
  { key: 'near',   label: '⚠️ 接近進場' },
  { key: 'held',   label: '📊 已持有' },
  { key: 'reduce', label: '🔻 減碼/出場' },
  { key: 'win',    label: '📈 賺' },
  { key: 'lose',   label: '📉 賠' },
];

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MimiWatchlist() {
  const [items, setItems]         = useState([]);
  const [prices, setPrices]       = useState({});
  const [loading, setLoading]     = useState(true);
  const [priceLoading, setPriceLoading] = useState(false);
  const [syncing, setSyncing]     = useState(false);
  const [filter, setFilter]       = useState('all');
  const [search, setSearch]       = useState('');
  const [lastSync, setLastSync]   = useState(null);

  const loadPrices = async () => {
    setPriceLoading(true);
    try {
      const { data } = await axios.get('/api/mimi/watchlist/prices');
      setPrices(data);
    } catch (e) {
      console.error(e);
    } finally {
      setPriceLoading(false);
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get('/api/mimi/watchlist');
      setItems(data.items ?? []);
      const first = data.items?.[0];
      if (first?.last_synced) setLastSync(first.last_synced);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
    // Fetch prices + trigger background scrape on default load
    loadPrices();
    axios.post('/api/scrape/mimi-all').catch(() => {});
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await axios.post('/api/mimi/sync');
      await load();
    } catch (e) {
      console.error(e);
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Merge live prices into items
  const enriched = useMemo(() =>
    items.map(i => ({ ...i, ...(prices[i.ticker] ?? {}) })),
  [items, prices]);

  const filtered = useMemo(() => {
    let list = enriched;
    if (filter === 'near')        list = list.filter(i => i.near_entry && !i.held);
    else if (filter === 'held')   list = list.filter(i => i.held);
    else if (filter === 'reduce') list = list.filter(i => ['🟠', '🔴'].includes(i.action_light));
    else if (filter === 'win')    list = list.filter(i => (i.return_pct || '').trim().startsWith('+'));
    else if (filter === 'lose')   list = list.filter(i => (i.return_pct || '').trim().startsWith('-'));
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter(i =>
        i.ticker.includes(q) ||
        (i.company_name ?? '').toUpperCase().includes(q) ||
        (i.tags ?? '').toUpperCase().includes(q) ||
        (i.sector ?? '').toUpperCase().includes(q)
      );
    }
    return list;
  }, [enriched, filter, search]);

  // Group by sector, preserving doc order
  const grouped = useMemo(() => {
    const map = new Map();
    for (const item of filtered) {
      const s = item.sector || '其他';
      if (!map.has(s)) map.set(s, []);
      map.get(s).push(item);
    }
    return [...map.entries()]; // [sector, items[]]
  }, [filtered]);

  const nearCount = enriched.filter(i => i.near_entry && !i.held).length;
  const heldCount = enriched.filter(i => i.held).length;
  const winCount  = enriched.filter(i => (i.return_pct || '').trim().startsWith('+')).length;
  const loseCount = enriched.filter(i => (i.return_pct || '').trim().startsWith('-')).length;

  return (
    <div className="min-h-screen p-4 md:p-6 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">Mimi 觀察名單</h1>
          {lastSync && (
            <p className="text-xs text-[var(--text-muted)] mt-0.5">
              最後同步：{new Date(lastSync).toLocaleString('zh-TW')}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {priceLoading && (
            <span className="text-[10px] text-[var(--text-muted)] flex items-center gap-1">
              <RefreshCw size={10} className="animate-spin" /> 報價載入中
            </span>
          )}
          <button
            onClick={sync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--text-secondary)] transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            {syncing ? '同步中…' : '同步 Mimi Doc'}
          </button>
        </div>
      </div>

      {/* Stats row — clickable filters */}
      <div className="grid grid-cols-4 gap-2 mb-5">
        <StatCard label="全部標的" value={items.length}
          active={filter === 'all'} onClick={() => setFilter('all')} />
        <StatCard label="已持有" value={heldCount} accent="blue"
          active={filter === 'held'} onClick={() => setFilter(filter === 'held' ? 'all' : 'held')} />
        <StatCard label="📈 賺" value={winCount} accent="green"
          active={filter === 'win'} onClick={() => setFilter(filter === 'win' ? 'all' : 'win')} />
        <StatCard label="📉 賠" value={loseCount} accent="red"
          active={filter === 'lose'} onClick={() => setFilter(filter === 'lose' ? 'all' : 'lose')} />
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="搜尋股票代號、公司、族群…"
        value={search}
        onChange={e => setSearch(e.target.value)}
        className="w-full mb-3 px-3 py-2 rounded-lg text-sm bg-white/5 border border-white/10 text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-white/20"
      />

      {/* Filter tabs */}
      <div className="flex gap-2 mb-5 flex-wrap">
        {FILTERS.map(f => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              filter === f.key
                ? 'bg-white/15 border-white/20 text-[var(--text-primary)]'
                : 'bg-transparent border-white/10 text-[var(--text-muted)] hover:border-white/20'
            }`}
          >
            {f.label}
            {f.key === 'near' && nearCount > 0 && (
              <span className="ml-1 bg-amber-400/20 text-amber-400 rounded-full px-1.5 py-px text-[10px]">
                {nearCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* List grouped by sector */}
      {loading ? (
        <div className="text-center py-16 text-[var(--text-muted)] text-sm">載入中…</div>
      ) : grouped.length === 0 ? (
        <div className="text-center py-16 text-[var(--text-muted)] text-sm">無符合條件的標的</div>
      ) : (
        grouped.map(([sector, sItems]) => (
          <SectorGroup key={sector} sector={sector} items={sItems} />
        ))
      )}
    </div>
  );
}

function StatCard({ label, value, accent, active, onClick }) {
  const colors = {
    amber: { bg: 'rgba(251,191,36,0.08)', border: 'rgba(251,191,36,0.2)', text: '#fbbf24', ring: '#fbbf24' },
    blue:  { bg: 'rgba(59,130,246,0.08)', border: 'rgba(59,130,246,0.2)',  text: '#60a5fa', ring: '#60a5fa' },
    green: { bg: 'rgba(52,211,153,0.08)', border: 'rgba(52,211,153,0.2)',  text: '#34d399', ring: '#34d399' },
    red:   { bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.2)', text: '#f87171', ring: '#f87171' },
  };
  const c = accent ? colors[accent] : null;
  const ring = c?.ring ?? 'var(--text-primary)';
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-xl p-3 border text-center transition-all hover:opacity-90 cursor-pointer"
      style={{
        background: c?.bg ?? 'var(--card-bg)',
        borderColor: active ? ring : (c?.border ?? 'var(--border)'),
        boxShadow: active ? `0 0 0 2px ${ring}` : 'none',
      }}
    >
      <div className="text-lg font-bold" style={{ color: c?.text ?? 'var(--text-primary)' }}>
        {value}
      </div>
      <div className="text-[10px] text-[var(--text-muted)] mt-0.5">{label}</div>
    </button>
  );
}
