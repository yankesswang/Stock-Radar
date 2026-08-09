import { useState, useEffect } from 'react';
import axios from 'axios';
import {
    LineChart, Line, AreaChart, Area, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid, ReferenceLine, Legend,
} from 'recharts';
import { TrendingUp, TrendingDown, Minus, Split, ArrowRight, ChevronDown } from 'lucide-react';
import { CHART_GRID, CHART_AXIS, chartTooltipStyle, chartTooltipLabelStyle } from './chartTheme';

const UP = 'var(--accent-success)';
const DOWN = 'var(--accent-danger)';
const FLAT = 'var(--text-muted)';

const dirColor = (d) => (d === 'up' ? UP : d === 'down' ? DOWN : FLAT);

const VERDICT_STYLE = {
    up:      { color: UP,   bg: 'rgba(5,150,105,0.10)',  border: 'rgba(5,150,105,0.28)',  Icon: TrendingUp },
    down:    { color: DOWN, bg: 'rgba(220,38,38,0.10)',  border: 'rgba(220,38,38,0.28)',  Icon: TrendingDown },
    mixed:   { color: 'var(--accent-amber)', bg: 'rgba(217,119,6,0.10)', border: 'rgba(217,119,6,0.28)', Icon: Split },
    flat:    { color: FLAT, bg: 'rgba(148,163,184,0.10)', border: 'var(--glass-border)',  Icon: Minus },
    unknown: { color: FLAT, bg: 'rgba(148,163,184,0.10)', border: 'var(--glass-border)',  Icon: Minus },
};

const fmtPct = (v, unit = '%') =>
    v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v}${unit}`;

// Snapshots are irregularly spaced (daily for months, then a 5-month gap), so the
// x-axis has to be a real time scale — a categorical axis would make a one-day
// revision look the same width as half a year of silence.
const withTs = (rows) => rows.map(r => ({ ...r, ts: Date.parse(`${r.date}T00:00:00Z`) }));
const tsMonth = (ts) => new Date(ts).toISOString().slice(2, 7);
const tsDay = (ts) => new Date(ts).toISOString().slice(0, 10);
const TIME_AXIS = { type: 'number', scale: 'time', domain: ['dataMin', 'dataMax'] };

// ── Verdict tile: one signal (EPS / target / ratings / actions) ────────────────
function SignalTile({ reason }) {
    const color = dirColor(reason.direction);
    return (
        <div className="p-4 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
            <div className="flex items-start justify-between gap-2 mb-2">
                <span className="text-xs text-[var(--text-muted)] font-medium leading-snug">{reason.label}</span>
                {reason.weight === 'primary' && (
                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-[rgba(0,82,255,0.08)] text-[var(--accent-cyan)] font-semibold">
                        主訊號
                    </span>
                )}
            </div>
            <div className="text-2xl font-bold font-mono tabular-nums leading-none mb-2" style={{ color }}>
                {reason.change_pct === null || reason.change_pct === undefined
                    ? (reason.direction === 'up' ? '偏多' : reason.direction === 'down' ? '偏空' : '中性')
                    : fmtPct(reason.change_pct, reason.unit === 'pt' ? 'pt' : '%')}
            </div>
            <div className="text-[11px] text-[var(--text-secondary)] leading-relaxed">{reason.detail}</div>
        </div>
    );
}

// ── Consensus EPS step chart (dense series, fiscal-year rolls marked) ──────────
function ConsensusChart({ trend }) {
    const points = withTs(trend?.points ?? []);
    if (points.length < 2) return null;
    const hasNext = points.some(p => p.next_year_eps !== null);

    return (
        <div>
            <div className="flex items-baseline justify-between mb-3">
                <h4 className="text-[13px] font-semibold text-[var(--text-secondary)]">本年度 EPS 共識走勢</h4>
                {trend.momentum?.since_segment_start !== undefined && trend.momentum?.since_segment_start !== null && (
                    <span className="text-[11px] font-mono" style={{ color: dirColor(trend.momentum.since_segment_start > 0 ? 'up' : trend.momentum.since_segment_start < 0 ? 'down' : 'flat') }}>
                        {fmtPct(trend.momentum.since_segment_start)}<span className="ml-2 text-[var(--text-muted)]">自 {trend.momentum.segment_start_date}</span>
                    </span>
                )}
            </div>
            <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={points} margin={{ top: 5, right: 8, bottom: 0, left: -8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                        <XAxis dataKey="ts" {...TIME_AXIS} stroke="var(--text-muted)" fontSize={10} tickLine={false}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }}
                            tickFormatter={tsMonth} minTickGap={28} />
                        <YAxis stroke="var(--text-muted)" fontSize={10} tickLine={false} width={48}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }}
                            domain={['auto', 'auto']} tickFormatter={(v) => `$${v}`} />
                        <Tooltip contentStyle={chartTooltipStyle} labelStyle={chartTooltipLabelStyle}
                            labelFormatter={tsDay}
                            formatter={(v, name) => [v === null ? '—' : `$${v}`, name]} />
                        {trend.breaks?.map((d, i) => (
                            <ReferenceLine key={d} x={Date.parse(`${d}T00:00:00Z`)} stroke="var(--accent-amber)" strokeDasharray="2 4"
                                label={{
                                    value: '不連續', fontSize: 9, fill: 'var(--accent-amber)',
                                    // Alternate sides so adjacent breaks don't overprint each other.
                                    position: i % 2 ? 'insideBottomLeft' : 'insideTopLeft',
                                }} />
                        ))}
                        <Line type="stepAfter" dataKey="current_year_eps" name="本年度 EPS" dot={false}
                            stroke="var(--accent-cyan)" strokeWidth={2} connectNulls={false}
                            activeDot={{ r: 4, fill: 'var(--accent-cyan)' }} />
                        {hasNext && (
                            <Line type="stepAfter" dataKey="next_year_eps" name="明年度 EPS" dot={false}
                                stroke="var(--accent-purple)" strokeWidth={2} strokeDasharray="4 4"
                                connectNulls={false} activeDot={{ r: 4, fill: 'var(--accent-purple)' }} />
                        )}
                        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconType="plainline" iconSize={14} />
                    </LineChart>
                </ResponsiveContainer>
            </div>
            {(trend.breaks?.length > 0 || trend.outliers_dropped > 0) && (
                <p className="mt-1 text-[10px] text-[var(--text-muted)] leading-relaxed">
                    {trend.breaks?.length > 0 &&
                        '虛線處單日跳動逾 15%（會計年度換算或資料源更換），前後數值不可直接比較；漲跌幅一律只從最後一條虛線之後起算。'}
                    {trend.outliers_dropped > 0 && ` 已剔除 ${trend.outliers_dropped} 筆抓取異常值。`}
                </p>
            )}
        </div>
    );
}

// ── Price target consensus over forecast snapshots ────────────────────────────
function TargetChart({ priceTarget }) {
    const series = withTs(priceTarget?.series ?? []);
    if (series.length < 2) return null;
    const dir = priceTarget.change_pct > 2 ? 'up' : priceTarget.change_pct < -2 ? 'down' : 'flat';
    const latest = priceTarget.latest;

    return (
        <div>
            <div className="flex items-baseline justify-between mb-3">
                <h4 className="text-[13px] font-semibold text-[var(--text-secondary)]">平均目標價修正</h4>
                <span className="text-[11px] font-mono" style={{ color: dirColor(dir) }}>
                    {fmtPct(priceTarget.change_pct)}<span className="ml-2 text-[var(--text-muted)]">${series[0].avg} → ${series[series.length - 1].avg}</span>
                </span>
            </div>
            {latest?.low != null && latest?.high != null && (
                <p className="-mt-2 mb-2 text-[10px] text-[var(--text-muted)] font-mono">
                    最新區間 ${latest.low} – ${latest.high}
                </p>
            )}
            <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={series} margin={{ top: 5, right: 8, bottom: 0, left: -8 }}>
                        <defs>
                            <linearGradient id="ptFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor="var(--accent-cyan)" stopOpacity={0.22} />
                                <stop offset="100%" stopColor="var(--accent-cyan)" stopOpacity={0.01} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                        <XAxis dataKey="ts" {...TIME_AXIS} stroke="var(--text-muted)" fontSize={10} tickLine={false}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }}
                            tickFormatter={tsMonth} minTickGap={28} />
                        <YAxis stroke="var(--text-muted)" fontSize={10} tickLine={false} width={48}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }}
                            domain={['auto', 'auto']} tickFormatter={(v) => `$${v}`} />
                        <Tooltip contentStyle={chartTooltipStyle} labelStyle={chartTooltipLabelStyle}
                            labelFormatter={tsDay}
                            formatter={(v, _n, p) => [
                                `$${v}（區間 $${p.payload.low ?? '—'} – $${p.payload.high ?? '—'}）`,
                                '平均目標價',
                            ]} />
                        <Area type="monotone" dataKey="avg" name="平均目標價" stroke="var(--accent-cyan)"
                            strokeWidth={2} fill="url(#ptFill)"
                            dot={{ r: 3, fill: 'var(--accent-cyan)', strokeWidth: 0 }} />
                    </AreaChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}

// ── Share of bullish analysts over the merged rating window ───────────────────
function RatingsDrift({ ratings }) {
    if (!ratings || ratings.rows.length < 2) return null;
    const dir = ratings.direction;

    return (
        <div>
            <div className="flex items-baseline justify-between mb-3">
                <h4 className="text-[13px] font-semibold text-[var(--text-secondary)]">看多分析師佔比</h4>
                <span className="text-[11px] font-mono" style={{ color: dirColor(dir) }}>
                    {fmtPct(ratings.bull_pct_change, 'pt')}<span className="ml-2 text-[var(--text-muted)]">{ratings.bull_pct_first}% → {ratings.bull_pct_latest}%</span>
                </span>
            </div>
            <div className="h-[150px]">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={ratings.rows} margin={{ top: 5, right: 8, bottom: 0, left: -8 }}>
                        <defs>
                            <linearGradient id="bullFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={dirColor(dir)} stopOpacity={0.22} />
                                <stop offset="100%" stopColor={dirColor(dir)} stopOpacity={0.01} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                        <XAxis dataKey="period" stroke="var(--text-muted)" fontSize={10} tickLine={false}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }} />
                        <YAxis stroke="var(--text-muted)" fontSize={10} tickLine={false} width={40}
                            axisLine={{ stroke: CHART_AXIS }} tick={{ fill: 'var(--text-muted)' }}
                            domain={['dataMin - 5', 'dataMax + 5']} tickFormatter={(v) => `${Math.round(v)}%`} />
                        <Tooltip contentStyle={chartTooltipStyle} labelStyle={chartTooltipLabelStyle}
                            formatter={(v, _n, p) => [`${v}%（${p.payload.total} 位分析師）`, '看多佔比']} />
                        <Area type="monotone" dataKey="bull_pct" stroke={dirColor(dir)} strokeWidth={2}
                            fill="url(#bullFill)" dot={{ r: 2.5, fill: dirColor(dir), strokeWidth: 0 }} />
                    </AreaChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}

// ── Individual analyst actions ────────────────────────────────────────────────
function ActionList({ actions, counts }) {
    const [open, setOpen] = useState(false);
    if (!actions?.length) return null;
    const shown = open ? actions : actions.slice(0, 6);

    return (
        <div>
            <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
                <h4 className="text-[13px] font-semibold text-[var(--text-secondary)]">近期分析師動作</h4>
                {counts?.total > 0 && (
                    <div className="flex items-center gap-2.5 text-[11px] font-mono">
                        <span className="text-[var(--text-muted)]">近 {counts.window_days} 天</span>
                        <span style={{ color: UP }}>↑目標價 {counts.raised}</span>
                        <span style={{ color: DOWN }}>↓目標價 {counts.lowered}</span>
                        <span style={{ color: UP }}>升評 {counts.upgrades}</span>
                        <span style={{ color: DOWN }}>降評 {counts.downgrades}</span>
                    </div>
                )}
            </div>
            <div className="divide-y divide-[var(--glass-border)] border-y border-[var(--glass-border)]">
                {shown.map((a, i) => {
                    const color = dirColor(a.direction);
                    const low = a.action.toLowerCase();
                    const actionZh = low.includes('upgrade') ? '升評'
                        : low.includes('downgrade') ? '降評'
                        : low.includes('initiat') ? '首次評等'
                        : low.includes('reiterat') ? '重申'
                        : low.includes('maintain') ? '維持' : a.action;
                    return (
                        <div key={`${a.date}-${a.firm}-${i}`} className="flex items-center gap-3 py-2.5 text-[12px]">
                            <span className="w-[68px] shrink-0 font-mono text-[var(--text-muted)]">{a.date.slice(5)}</span>
                            <span className="flex-1 min-w-0 truncate text-[var(--text-primary)] font-medium">{a.firm}</span>
                            <span className="w-[52px] shrink-0 text-[11px]" style={{ color }}>{actionZh}</span>
                            <span className="w-[52px] shrink-0 text-[11px] text-[var(--text-secondary)]">{a.rating}</span>
                            <span className="w-[128px] shrink-0 text-right font-mono tabular-nums" style={{ color }}>
                                {a.target_from !== null && a.target_to !== null ? (
                                    <span className="inline-flex items-center gap-1 justify-end">
                                        <span className="text-[var(--text-muted)] line-through">${a.target_from}</span>
                                        <ArrowRight size={10} />
                                        <span className="font-semibold">${a.target_to}</span>
                                    </span>
                                ) : a.target_to !== null ? (
                                    <span className="text-[var(--text-secondary)]">${a.target_to}</span>
                                ) : (
                                    <span className="text-[var(--text-muted)]">—</span>
                                )}
                            </span>
                        </div>
                    );
                })}
            </div>
            {actions.length > 6 && (
                <button onClick={() => setOpen(!open)}
                    className="mt-2 inline-flex items-center gap-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--accent-cyan)] transition-colors">
                    <ChevronDown size={12} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
                    {open ? '收合' : `顯示全部 ${actions.length} 筆`}
                </button>
            )}
        </div>
    );
}

// ── Panel ─────────────────────────────────────────────────────────────────────
export default function AnalystRevisionPanel({ ticker }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    // Mounted with key={ticker} by the parent, so a ticker switch remounts this
    // component and `loading` starts true again — no need to reset it here.
    useEffect(() => {
        if (!ticker) return;
        let alive = true;
        axios.get(`/api/valuations/${ticker}/revisions`)
            .then(res => { if (alive) { setData(res.data); setLoading(false); } })
            .catch(() => { if (alive) { setData(null); setLoading(false); } });
        return () => { alive = false; };
    }, [ticker]);

    if (loading) {
        return (
            <div className="card !p-6 flex justify-center py-16">
                <div className="w-5 h-5 border-2 border-[var(--accent-cyan)] border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }
    if (!data) return null;

    const v = data.verdict;
    const style = VERDICT_STYLE[v.direction] ?? VERDICT_STYLE.unknown;
    const { Icon } = style;
    const epsFy = (data.eps_by_fiscal_year ?? []).filter(f => f.points.length >= 2);

    return (
        <div className="card !p-6 space-y-7">
            {/* Header + verdict */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)]">分析師預期修正</h3>
                    <p className="text-[11px] text-[var(--text-muted)] mt-0.5">
                        {data.as_of ? `資料截至 ${data.as_of}` : '無快照資料'}
                        {data.snapshot_count > 0 && ` · ${data.snapshot_count} 份分析師快照`}
                    </p>
                </div>
                <div className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-bold"
                    style={{ color: style.color, background: style.bg, border: `1px solid ${style.border}` }}>
                    <Icon size={16} /> {v.headline}
                </div>
            </div>

            {v.reasons.length === 0 ? (
                <p className="text-[13px] text-[var(--text-muted)] py-6 text-center">{v.summary}</p>
            ) : (
                <>
                    {/* Signals */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                        {v.reasons.map(r => <SignalTile key={r.label} reason={r} />)}
                    </div>

                    {/* FY-aligned consensus — the cleanest read on revisions */}
                    {epsFy.length > 0 && (
                        <div>
                            <h4 className="text-[13px] font-semibold text-[var(--text-secondary)] mb-3">
                                各會計年度 EPS 共識修正
                            </h4>
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                                {epsFy.map(f => (
                                    <div key={f.fiscal_year}
                                        className="p-3.5 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                                        <div className="flex items-center justify-between mb-1.5">
                                            <span className="text-xs font-semibold text-[var(--text-secondary)]">FY{f.fiscal_year}</span>
                                            <span className="text-sm font-bold font-mono tabular-nums"
                                                style={{ color: dirColor(f.direction) }}>
                                                {fmtPct(f.change_pct)}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-1.5 font-mono text-[13px]">
                                            <span className="text-[var(--text-muted)]">${f.first}</span>
                                            <ArrowRight size={11} className="text-[var(--text-muted)]" />
                                            <span className="font-semibold text-[var(--text-primary)]">${f.latest}</span>
                                        </div>
                                        <div className="text-[10px] text-[var(--text-muted)] mt-1">
                                            {f.first_date} → {f.latest_date} · {f.points.length} 次觀測
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Charts */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <ConsensusChart trend={data.consensus_trend} />
                        <TargetChart priceTarget={data.price_target} />
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <RatingsDrift ratings={data.ratings} />
                        <ActionList actions={data.actions} counts={data.action_counts} />
                    </div>
                </>
            )}
        </div>
    );
}
