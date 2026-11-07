import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from '../lib/apiClient';
import {
  ChevronLeft, ChevronRight, RefreshCw, CalendarDays,
  TrendingUp, DollarSign, Clock,
} from 'lucide-react';
import ClusterTabs from '../components/ClusterTabs';
import { EARNINGS_TABS } from '../components/clusterTabsDef';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function fmt(n, dec = 2) {
  if (n == null) return null;
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtRev(n) {
  if (n == null) return null;
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${fmt(n, 0)}`;
}

// Colour pool for ticker chips — cycles through accent colours
const CHIP_COLORS = [
  'bg-[rgba(139,92,246,0.18)] text-[var(--accent-purple)] border-[rgba(139,92,246,0.3)]',
  'bg-[rgba(6,182,212,0.15)] text-[var(--accent-cyan)] border-[rgba(6,182,212,0.3)]',
  'bg-[rgba(16,185,129,0.15)] text-[var(--accent-green)] border-[rgba(16,185,129,0.3)]',
  'bg-[rgba(245,158,11,0.15)] text-[var(--accent-yellow)] border-[rgba(245,158,11,0.3)]',
  'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)] border-[rgba(248,113,113,0.3)]',
  'bg-[rgba(99,102,241,0.15)] text-[#818cf8] border-[rgba(99,102,241,0.3)]',
];

// Deterministically assign colour by ticker so it stays consistent
function chipColor(ticker) {
  let h = 0;
  for (let i = 0; i < ticker.length; i++) h = (h * 31 + ticker.charCodeAt(i)) >>> 0;
  return CHIP_COLORS[h % CHIP_COLORS.length];
}

export default function EarningsCalendar() {
  const [events,   setEvents]   = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState(null);
  const [selected, setSelected] = useState(null);   // { date, events[] }
  const [viewDate, setViewDate] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });

  const load = (bustCache = false) => {
    setLoading(true);
    setError(null);
    const req = bustCache
      ? axios.delete('/api/earnings/calendar/cache').then(() => axios.get('/api/earnings/calendar'))
      : axios.get('/api/earnings/calendar');
    req
      .then(r => setEvents((r.data || r).events || []))
      .catch(e => setError(e?.response?.data?.detail || 'Failed to load earnings'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // Build a map: "YYYY-MM-DD" → event[]
  const byDate = useMemo(() => {
    const m = {};
    for (const ev of events) {
      (m[ev.date] ||= []).push(ev);
    }
    return m;
  }, [events]);

  // Calendar grid for current viewDate
  const { year, month } = viewDate;
  const firstDay = new Date(year, month, 1).getDay();  // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const todayStr = new Date().toISOString().slice(0, 10);

  const cells = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    cells.push({ day: d, iso, evs: byDate[iso] || [] });
  }

  const prevMonth = () => setViewDate(({ year, month }) =>
    month === 0 ? { year: year - 1, month: 11 } : { year, month: month - 1 });
  const nextMonth = () => setViewDate(({ year, month }) =>
    month === 11 ? { year: year + 1, month: 0 } : { year, month: month + 1 });

  // Upcoming events in next 14 days (for sidebar)
  const upcoming = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const limit = new Date(today); limit.setDate(limit.getDate() + 14);
    return events
      .filter(e => e.status !== 'reported')
      .filter(e => { const d = new Date(e.date + 'T00:00:00'); return d >= today && d <= limit; })
      .slice(0, 20);
  }, [events]);

  // Monthly stats
  const monthEvs = useMemo(() => {
    const prefix = `${year}-${String(month + 1).padStart(2, '0')}-`;
    return events.filter(e => e.date.startsWith(prefix));
  }, [events, year, month]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-3">
        <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
        <p className="text-xs text-[var(--text-muted)]">Fetching earnings calendar…</p>
        <p className="text-[10px] text-[var(--text-muted)] opacity-60">First load may take 10–20 s</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-20 text-center space-y-3">
        <p className="text-[var(--accent-danger)]">{error}</p>
        <button onClick={() => load()} className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* 財報三個切面（行事曆／結論／論點）—— 導覽列只留一個「財報」入口 */}
      <ClusterTabs items={EARNINGS_TABS} active="/earnings" />

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg">
            <CalendarDays size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Earnings Calendar</h1>
            <p className="text-xs text-[var(--text-muted)]">
              {events.length} upcoming events · powered by yfinance
            </p>
          </div>
        </div>
        <button
          onClick={() => load(true)}
          className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_280px] gap-6">

        {/* ── Calendar ───────────────────────────────────────────────────── */}
        <div className="space-y-4">
          {/* Month nav */}
          <div className="card p-4">
            <div className="flex items-center justify-between mb-4">
              <button
                onClick={prevMonth}
                className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
              >
                <ChevronLeft size={18} />
              </button>
              <div className="text-center">
                <p className="text-base font-bold">{MONTHS[month]} {year}</p>
                <p className="text-[10px] text-[var(--text-muted)]">{monthEvs.length} earnings this month</p>
              </div>
              <button
                onClick={nextMonth}
                className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
              >
                <ChevronRight size={18} />
              </button>
            </div>

            {/* Weekday headers */}
            <div className="grid grid-cols-7 mb-1">
              {WEEKDAYS.map(w => (
                <div key={w} className="text-center text-[10px] font-semibold text-[var(--text-muted)] py-1">
                  {w}
                </div>
              ))}
            </div>

            {/* Day cells */}
            <div className="grid grid-cols-7 gap-1">
              {cells.map((cell, i) => {
                if (!cell) return <div key={`empty-${i}`} />;
                const isToday   = cell.iso === todayStr;
                const isSelected = selected?.date === cell.iso;
                const hasEvs    = cell.evs.length > 0;

                return (
                  <button
                    key={cell.iso}
                    onClick={() => setSelected(isSelected ? null : { date: cell.iso, events: cell.evs })}
                    disabled={!hasEvs}
                    className={`
                      relative min-h-[72px] p-1.5 rounded-lg text-left flex flex-col transition-all
                      ${isSelected
                        ? 'bg-[rgba(139,92,246,0.2)] ring-1 ring-[var(--accent-purple)]'
                        : hasEvs
                          ? 'hover:bg-[rgba(255,255,255,0.04)] cursor-pointer'
                          : 'cursor-default opacity-60'
                      }
                      ${isToday ? 'ring-1 ring-[var(--accent-cyan)]' : ''}
                    `}
                  >
                    <span className={`text-xs font-semibold mb-1 ${
                      isToday ? 'text-[var(--accent-cyan)]' : 'text-[var(--text-secondary)]'
                    }`}>
                      {cell.day}
                    </span>
                    <div className="flex flex-col gap-0.5 overflow-hidden">
                      {cell.evs.slice(0, 3).map(ev => (
                        <span
                          key={ev.ticker}
                          className={`text-[9px] font-bold px-1 py-0.5 rounded border truncate ${chipColor(ev.ticker)}`}
                        >
                          {ev.ticker}
                        </span>
                      ))}
                      {cell.evs.length > 3 && (
                        <span className="text-[9px] text-[var(--text-muted)] pl-0.5">
                          +{cell.evs.length - 3} more
                        </span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ── Selected-day detail panel ─────────────────────────────── */}
          {selected && selected.events.length > 0 && (
            <div className="card p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold">
                  {new Date(selected.date + 'T00:00:00').toLocaleDateString('en-US', {
                    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
                  })}
                </h3>
                <span className="text-xs text-[var(--text-muted)]">{selected.events.length} report{selected.events.length > 1 ? 's' : ''}</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {selected.events.map(ev => (
                  <EventCard key={ev.ticker} ev={ev} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Sidebar: upcoming 14 days ──────────────────────────────────── */}
        <div className="space-y-3">
          <div className="card p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Clock size={13} className="text-[var(--text-muted)]" />
              <h3 className="text-xs font-bold text-[var(--text-secondary)] uppercase tracking-wider">Next 14 Days</h3>
            </div>

            {upcoming.length === 0 ? (
              <p className="text-xs text-[var(--text-muted)]">No earnings in the next 14 days.</p>
            ) : (
              <div className="space-y-1">
                {groupByDate(upcoming).map(({ date, items }) => (
                  <div key={date}>
                    <p className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider px-1 mt-2 mb-1">
                      {new Date(date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                    </p>
                    {items.map(ev => (
                      <Link key={ev.ticker} href={`/stock/${ev.ticker}`}>
                        <div className="flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-[rgba(255,255,255,0.04)] transition-all cursor-pointer group">
                          <div>
                            <p className={`text-xs font-bold group-hover:underline ${chipColor(ev.ticker).split(' ')[1]}`}>
                              {ev.ticker}
                            </p>
                            <p className="text-[10px] text-[var(--text-muted)] truncate max-w-[140px]">
                              {ev.company_name}
                            </p>
                          </div>
                          {ev.eps_estimate != null && (
                            <span className="text-[10px] text-[var(--text-muted)]">
                              EPS {ev.eps_estimate > 0 ? '+' : ''}{fmt(ev.eps_estimate)}
                            </span>
                          )}
                        </div>
                      </Link>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Legend */}
          <div className="card p-4 space-y-2">
            <h3 className="text-xs font-bold text-[var(--text-secondary)] uppercase tracking-wider">Legend</h3>
            <div className="space-y-1.5 text-[10px] text-[var(--text-muted)]">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded ring-1 ring-[var(--accent-cyan)] shrink-0" />
                Today
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded ring-1 ring-[var(--accent-purple)] bg-[rgba(139,92,246,0.2)] shrink-0" />
                Selected day
              </div>
              <div className="flex items-center gap-2">
                <span className="w-10 h-3 rounded bg-[rgba(139,92,246,0.18)] border border-[rgba(139,92,246,0.3)] shrink-0" />
                Earnings event
              </div>
            </div>
            <p className="text-[10px] text-[var(--text-muted)] pt-1 leading-relaxed">
              Click any day with events to see details. Dates show the earliest expected reporting window from yfinance.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── EventCard ─────────────────────────────────────────────────────────────── */
function EventCard({ ev }) {
  const color = chipColor(ev.ticker);
  const chipCls = color.split(' ').slice(0, 2).join(' ');

  return (
    <div className="rounded-xl border border-[var(--glass-border)] p-3 space-y-2 bg-[rgba(255,255,255,0.02)] hover:bg-[rgba(255,255,255,0.04)] transition-all">
      <div className="flex items-start justify-between gap-2">
        <div>
          <Link href={`/stock/${ev.ticker}`}>
            <span className={`text-sm font-bold hover:underline cursor-pointer ${chipCls}`}>
              {ev.ticker}
            </span>
          </Link>
          <p className="text-[10px] text-[var(--text-muted)] mt-0.5 leading-tight">
            {ev.company_name}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {ev.status === 'reported' && (
            <span className="text-[9px] text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] px-1.5 py-0.5 rounded">
              reported
            </span>
          )}
          {(ev.time_of_day === 'BMO' || ev.time_of_day === 'AMC') && (
            <span className="text-[9px] text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] px-1.5 py-0.5 rounded">
              {ev.time_of_day === 'BMO' ? 'pre-market' : 'after-close'}
            </span>
          )}
          {ev.date_source === 'calendar_estimate' && (
            <span className="text-[9px] text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] px-1.5 py-0.5 rounded">
              unconfirmed
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {ev.eps_estimate != null && (
          <Stat
            icon={<TrendingUp size={10} />}
            label="EPS Est."
            value={`${ev.eps_estimate >= 0 ? '+' : ''}$${fmt(ev.eps_estimate)}`}
            sub={ev.eps_low != null && ev.eps_high != null
              ? `$${fmt(ev.eps_low)} – $${fmt(ev.eps_high)}`
              : null}
          />
        )}
        {ev.rev_estimate != null && (
          <Stat
            icon={<DollarSign size={10} />}
            label="Rev Est."
            value={fmtRev(ev.rev_estimate)}
            sub={ev.rev_low != null && ev.rev_high != null
              ? `${fmtRev(ev.rev_low)} – ${fmtRev(ev.rev_high)}`
              : null}
          />
        )}
      </div>
    </div>
  );
}

function Stat({ icon, label, value, sub }) {
  return (
    <div className="bg-[rgba(255,255,255,0.03)] rounded-lg p-2">
      <div className="flex items-center gap-1 text-[var(--text-muted)] mb-0.5">
        {icon}
        <span className="text-[9px] uppercase tracking-wider">{label}</span>
      </div>
      <p className="text-xs font-bold">{value}</p>
      {sub && <p className="text-[9px] text-[var(--text-muted)] mt-0.5">{sub}</p>}
    </div>
  );
}

/* ── helpers ───────────────────────────────────────────────────────────────── */
function groupByDate(evs) {
  const map = {};
  for (const ev of evs) {
    (map[ev.date] ||= []).push(ev);
  }
  return Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, items]) => ({ date, items }));
}
