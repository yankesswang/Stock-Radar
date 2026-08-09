import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
    Legend, Cell,
} from 'recharts';
import {
    ArrowLeft, TrendingUp, TrendingDown, Target, Users, ChevronDown,
    ArrowUpRight, ArrowDownRight, Minus,
} from 'lucide-react';
import { CHART_GRID, CHART_AXIS } from '../components/chartTheme';

function fmt(val, type) {
    if (val == null) return '\u2014';
    if (type === 'pct') return `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    if (type === 'dollar') return `$${val.toFixed(2)}`;
    if (type === 'revenue') {
        const abs = Math.abs(val);
        if (abs >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
        if (abs >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
        if (abs >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
        return `$${val.toFixed(2)}`;
    }
    if (type === 'eps') return `$${val.toFixed(2)}`;
    return String(val);
}

const RATING_COLORS = {
    strong_buy: 'var(--accent-success)',
    buy: '#60d6a2',
    hold: 'var(--accent-amber)',
    sell: '#fb923c',
    strong_sell: 'var(--accent-danger)',
};

const RATING_LABELS = {
    strong_buy: 'Strong Buy',
    buy: 'Buy',
    hold: 'Hold',
    sell: 'Sell',
    strong_sell: 'Strong Sell',
};

export default function StockForecast() {
    const [, params] = useRoute('/stock/:ticker/forecast');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [recsOpen, setRecsOpen] = useState(false);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/forecast`)
            .then(res => setData(res.data))
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load forecast data');
            })
            .finally(() => setLoading(false));
    }, [ticker]);

    if (loading) {
        return (
            <div className="container py-20 flex justify-center">
                <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="container py-20 text-center">
                <p className="text-[var(--text-muted)] text-base">{error}</p>
                <Link href={`/stock/${ticker}`} className="text-sm text-[var(--accent-purple)] hover:underline mt-4 inline-block">
                    Back to {ticker}
                </Link>
            </div>
        );
    }

    const pt = data.price_target;
    const ratings = data.analyst_ratings;
    const recs = data.analyst_recommendations;
    const af = data.annual_forecast;
    const ranges = data.forecast_ranges;

    // Build ratings chart data
    const ratingsChartData = ratings ? ratings.periods.map((p, i) => ({
        period: p,
        strong_buy: ratings.strong_buy?.[i] || 0,
        buy: ratings.buy?.[i] || 0,
        hold: ratings.hold?.[i] || 0,
        sell: ratings.sell?.[i] || 0,
        strong_sell: ratings.strong_sell?.[i] || 0,
    })) : [];

    // Latest ratings totals
    const latestIdx = ratingsChartData.length - 1;
    const latest = latestIdx >= 0 ? ratingsChartData[latestIdx] : null;
    const totalAnalysts = latest
        ? latest.strong_buy + latest.buy + latest.hold + latest.sell + latest.strong_sell
        : 0;

    return (
        <div className="container py-10 space-y-8 animate-fade-in">
            {/* Navigation */}
            <Link
                href={`/stock/${ticker}`}
                className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            >
                <ArrowLeft size={14} /> Back to {ticker}
            </Link>

            {/* Header */}
            <div className="pb-6 border-b border-[var(--glass-border)]">
                <div className="flex items-center gap-3 mb-2">
                    <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
                    <span className="badge badge-neutral">Analyst Forecast</span>
                </div>
                <p className="text-[15px] text-[var(--text-muted)]">
                    {totalAnalysts} analysts covering &middot; Data as of {data.date}
                </p>
            </div>

            {/* Price Target Cards */}
            {pt && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <Target size={16} className="text-[var(--accent-cyan)]" /> Price Target Consensus
                    </h3>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        <PriceTargetCard label="Low" price={pt.low} change={pt.low_change_pct} />
                        <PriceTargetCard label="Average" price={pt.average} change={pt.average_change_pct} highlight />
                        <PriceTargetCard label="Median" price={pt.median} change={pt.median_change_pct} />
                        <PriceTargetCard label="High" price={pt.high} change={pt.high_change_pct} />
                    </div>
                </div>
            )}

            {/* Analyst Ratings Chart + Current Breakdown side by side */}
            {ratings && ratingsChartData.length > 0 && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <Users size={16} className="text-[var(--accent-purple)]" /> Analyst Ratings
                    </h3>
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                        {/* Chart */}
                        <div className="lg:col-span-2 metrics-chart-card">
                            <div className="h-[280px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={ratingsChartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                                        <XAxis
                                            dataKey="period"
                                            stroke="var(--text-muted)"
                                            fontSize={11}
                                            tick={{ fill: 'var(--text-muted)' }}
                                            axisLine={{ stroke: CHART_AXIS }}
                                            tickLine={false}
                                        />
                                        <YAxis
                                            stroke="var(--text-muted)"
                                            fontSize={11}
                                            tick={{ fill: 'var(--text-muted)' }}
                                            axisLine={{ stroke: CHART_AXIS }}
                                            tickLine={false}
                                        />
                                        <Tooltip
                                            contentStyle={{
                                                backgroundColor: 'var(--bg-secondary)',
                                                border: '1px solid var(--glass-border)',
                                                borderRadius: '10px',
                                                fontSize: '12px',
                                                boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                                            }}
                                            itemStyle={{ fontSize: '12px' }}
                                            labelStyle={{ color: 'var(--text-muted)', marginBottom: '4px' }}
                                        />
                                        <Legend
                                            wrapperStyle={{ fontSize: '11px', paddingTop: '12px' }}
                                            iconType="circle"
                                            iconSize={8}
                                        />
                                        {Object.entries(RATING_COLORS).map(([key, color]) => (
                                            <Bar
                                                key={key}
                                                dataKey={key}
                                                stackId="ratings"
                                                fill={color}
                                                name={RATING_LABELS[key]}
                                                radius={key === 'strong_sell' || key === 'sell' && !ratings.strong_sell?.some(v => v > 0) ? [0, 0, 0, 0] : [0, 0, 0, 0]}
                                            />
                                        ))}
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        {/* Current breakdown */}
                        {latest && (
                            <div className="card !p-5 flex flex-col justify-center" style={{ transform: 'none' }}>
                                <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-4 font-semibold">
                                    Current Ratings
                                </div>
                                <div className="space-y-3">
                                    {Object.entries(RATING_COLORS).map(([key, color]) => {
                                        const count = latest[key] || 0;
                                        const pct = totalAnalysts > 0 ? (count / totalAnalysts * 100) : 0;
                                        if (count === 0 && (key === 'strong_sell')) return null;
                                        return (
                                            <div key={key}>
                                                <div className="flex items-center justify-between mb-1">
                                                    <span className="text-[13px] font-medium" style={{ color }}>
                                                        {RATING_LABELS[key]}
                                                    </span>
                                                    <span className="text-[13px] font-mono font-semibold text-[var(--text-primary)]">
                                                        {count}
                                                    </span>
                                                </div>
                                                <div className="h-2 rounded-full bg-[rgba(255,255,255,0.04)] overflow-hidden">
                                                    <div
                                                        className="h-full rounded-full transition-all duration-500"
                                                        style={{ width: `${pct}%`, background: color, opacity: 0.8 }}
                                                    />
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                                <div className="mt-4 pt-3 border-t border-[var(--glass-border)] text-center">
                                    <span className="text-2xl font-extrabold font-mono">{totalAnalysts}</span>
                                    <span className="text-[13px] text-[var(--text-muted)] ml-2">total analysts</span>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Annual Forecast Timeline */}
            {af && af.periods && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <TrendingUp size={16} className="text-[var(--accent-success)]" /> Annual Forecast Timeline
                    </h3>
                    <div className="metrics-section">
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">Metric</th>
                                        {af.periods.map(p => (
                                            <th key={p} className="metrics-th">{p.replace('FY ', '')}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    <ForecastRow label="Revenue" values={af.revenue} format="revenue" periods={af.periods} />
                                    <ForecastRow label="Revenue Growth" values={af.revenue_growth} format="pct" periods={af.periods} isGrowth />
                                    <ForecastRow label="EPS" values={af.eps} format="eps" periods={af.periods} />
                                    <ForecastRow label="EPS Growth" values={af.eps_growth} format="pct" periods={af.periods} isGrowth />
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}

            {/* EPS & Revenue Ranges */}
            {ranges && Object.keys(ranges).length > 0 && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <Target size={16} className="text-[var(--accent-amber)]" /> Forecast Ranges (High / Avg / Low)
                    </h3>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {[
                            { key: 'eps', label: 'EPS Estimates', format: 'eps', color: 'var(--accent-cyan)' },
                            { key: 'eps_growth', label: 'EPS Growth', format: 'pct', color: 'var(--accent-success)' },
                            { key: 'revenue', label: 'Revenue Estimates', format: 'revenue', color: 'var(--accent-purple)' },
                            { key: 'revenue_growth', label: 'Revenue Growth', format: 'pct', color: 'var(--accent-amber)' },
                        ].map(({ key, label, format, color }) => {
                            const r = ranges[key];
                            if (!r) return null;
                            return (
                                <RangeCard key={key} label={label} range={r} format={format} accentColor={color} />
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Analyst Recommendations Table */}
            {recs && recs.length > 0 && (
                <div>
                    <button
                        onClick={() => setRecsOpen(!recsOpen)}
                        className="metrics-section-header w-full"
                        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: '16px' }}
                    >
                        <div className="flex items-center gap-3">
                            <span className="w-3 h-3 rounded-sm shrink-0" style={{ background: 'var(--accent-cyan)' }} />
                            <span className="text-[15px] font-semibold">Recent Analyst Recommendations</span>
                            <span className="text-[13px] text-[var(--text-muted)] font-normal">{recs.length}</span>
                        </div>
                        <ChevronDown
                            size={16}
                            className={`text-[var(--text-muted)] transition-transform duration-200 ${recsOpen ? 'rotate-180' : ''}`}
                        />
                    </button>

                    {recsOpen && (
                        <div className="metrics-section mt-2">
                            <div className="overflow-x-auto styled-scrollbar">
                                <table className="metrics-table">
                                    <thead>
                                        <tr>
                                            <th className="metrics-th-sticky" style={{ minWidth: '140px' }}>Analyst</th>
                                            <th className="metrics-th" style={{ textAlign: 'left' }}>Firm</th>
                                            <th className="metrics-th" style={{ textAlign: 'left' }}>Rating</th>
                                            <th className="metrics-th" style={{ textAlign: 'left' }}>Action</th>
                                            <th className="metrics-th">Price Target</th>
                                            <th className="metrics-th">Upside</th>
                                            <th className="metrics-th">Date</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {recs.map((rec, i) => (
                                            <tr key={i} className="metrics-tr">
                                                <td className="metrics-td-sticky">
                                                    <span className="text-[14px] font-medium text-[var(--text-primary)]">
                                                        {rec.analyst}
                                                    </span>
                                                </td>
                                                <td className="metrics-td text-[var(--text-secondary)] !text-left !font-sans">
                                                    {rec.firm}
                                                </td>
                                                <td className="metrics-td !text-left !font-sans">
                                                    <RatingBadge rating={rec.rating} />
                                                </td>
                                                <td className="metrics-td text-[var(--text-secondary)] !text-left !font-sans">
                                                    {rec.action}
                                                </td>
                                                <td className="metrics-td font-semibold">{rec.price_target}</td>
                                                <td className={`metrics-td font-semibold ${
                                                    rec.upside?.startsWith('+') ? 'text-[var(--accent-success)]' :
                                                    rec.upside?.startsWith('-') ? 'text-[var(--accent-danger)]' : ''
                                                }`}>
                                                    {rec.upside}
                                                </td>
                                                <td className="metrics-td text-[var(--text-muted)]">{rec.date}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

/* ── Price Target Card ── */
function PriceTargetCard({ label, price, change, highlight }) {
    const isPositive = change != null && change >= 0;
    return (
        <div className={`card group ${highlight ? '!border-[rgba(139,92,246,0.25)]' : ''}`} style={{ transform: 'none' }}>
            <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-semibold">{label}</div>
            <div className="text-2xl font-extrabold font-mono tabular-nums mb-1">
                {price != null ? `$${price.toFixed(2)}` : '\u2014'}
            </div>
            {change != null && (
                <div className={`flex items-center gap-1 text-sm font-semibold ${
                    isPositive ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                }`}>
                    {isPositive ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                    {isPositive ? '+' : ''}{change.toFixed(2)}%
                </div>
            )}
        </div>
    );
}

/* ── Forecast Table Row ── */
function ForecastRow({ label, values, format, isGrowth }) {
    if (!values) return null;
    return (
        <tr className="metrics-tr">
            <td className="metrics-td-sticky">
                <span className="text-[14px] font-medium text-[var(--text-primary)]">{label}</span>
            </td>
            {values.map((val, i) => {
                let textColor = 'text-[var(--text-secondary)]';
                if (isGrowth && val != null) {
                    textColor = val >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]';
                }
                return (
                    <td key={i} className={`metrics-td ${textColor}`}>
                        {fmt(val, format)}
                    </td>
                );
            })}
        </tr>
    );
}

/* ── Range Card (High/Avg/Low) ── */
function RangeCard({ label, range, format, accentColor }) {
    const years = range.years || [];
    const high = range.high || [];
    const avg = range.avg || [];
    const low = range.low || [];

    // Filter out years where all values are null
    const validYears = years.filter((_, i) => high[i] != null || avg[i] != null || low[i] != null);

    if (validYears.length === 0) return null;

    return (
        <div className="card !p-5" style={{ transform: 'none' }}>
            <div className="flex items-center gap-2 mb-4">
                <span className="w-2.5 h-2.5 rounded-sm" style={{ background: accentColor }} />
                <span className="text-[14px] font-semibold text-[var(--text-primary)]">{label}</span>
            </div>
            <table className="w-full text-sm">
                <thead>
                    <tr className="border-b border-[var(--glass-border)]">
                        <th className="text-left text-[11px] text-[var(--text-muted)] uppercase tracking-wider font-semibold pb-2 pr-4">Year</th>
                        <th className="text-right text-[11px] text-[var(--text-muted)] uppercase tracking-wider font-semibold pb-2 px-3">High</th>
                        <th className="text-right text-[11px] text-[var(--text-muted)] uppercase tracking-wider font-semibold pb-2 px-3">Average</th>
                        <th className="text-right text-[11px] text-[var(--text-muted)] uppercase tracking-wider font-semibold pb-2 pl-3">Low</th>
                    </tr>
                </thead>
                <tbody>
                    {validYears.map((year) => {
                        const idx = years.indexOf(year);
                        return (
                            <tr key={year} className="border-b border-[rgba(255,255,255,0.03)]">
                                <td className="py-2.5 pr-4 font-semibold text-[var(--text-primary)]">{year}</td>
                                <td className="py-2.5 px-3 text-right font-mono tabular-nums text-[var(--text-secondary)]">
                                    {fmt(high[idx], format)}
                                </td>
                                <td className="py-2.5 px-3 text-right font-mono tabular-nums font-semibold" style={{ color: accentColor }}>
                                    {fmt(avg[idx], format)}
                                </td>
                                <td className="py-2.5 pl-3 text-right font-mono tabular-nums text-[var(--text-secondary)]">
                                    {fmt(low[idx], format)}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

/* ── Rating Badge ── */
function RatingBadge({ rating }) {
    if (!rating) return <span className="text-[var(--text-muted)]">{'\u2014'}</span>;

    const r = rating.toLowerCase();
    let cls = 'badge-neutral';
    if (r.includes('strong buy')) cls = 'badge-success';
    else if (r.includes('buy')) cls = 'badge-success';
    else if (r.includes('hold')) cls = 'badge-neutral';
    else if (r.includes('sell')) cls = 'badge-danger';

    // Handle upgrades/downgrades with arrow
    if (rating.includes('\u2192')) {
        const [from, to] = rating.split('\u2192');
        return (
            <span className="flex items-center gap-1 text-[13px]">
                <span className="text-[var(--text-muted)]">{from.trim()}</span>
                <span className="text-[var(--text-muted)]">&rarr;</span>
                <span className={`badge ${cls} !py-0.5 !px-2`}>{to.trim()}</span>
            </span>
        );
    }

    return <span className={`badge ${cls}`}>{rating}</span>;
}
