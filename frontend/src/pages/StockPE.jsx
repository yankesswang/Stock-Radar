import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
    ReferenceLine, Cell, CartesianGrid,
} from 'recharts';
import { ArrowLeft, TrendingUp, TrendingDown, Loader2, Info } from 'lucide-react';

// ── colour constants ──────────────────────────────────────────────────────────
const CYAN   = 'var(--accent-cyan)';
const GREEN  = 'var(--accent-success)';
const RED    = 'var(--accent-danger)';
const AMBER  = 'var(--accent-amber)';
const PURPLE = 'var(--accent-purple)';

// ── helpers ───────────────────────────────────────────────────────────────────
const fmt   = v => v != null ? +v.toFixed(2) : null;
const pctVs = (a, b) => (a && b) ? ((a / b - 1) * 100).toFixed(1) : null;
const sign  = n => n > 0 ? '+' : '';

function barColor(pe, median) {
    if (!median) return CYAN;
    const r = pe / median;
    if (r < 0.7) return '#34d399';
    if (r < 0.9) return '#6ee7b7';
    if (r < 1.1) return CYAN;
    if (r < 1.4) return '#fbbf24';
    return '#f87171';
}

function diffColor(pct) {
    return parseFloat(pct) > 0 ? RED : GREEN;
}

// ── custom bar tooltip ────────────────────────────────────────────────────────
function PETooltip({ active, payload, label, median, forwardPE }) {
    if (!active || !payload?.length) return null;
    const pe  = payload[0].value;
    const vsM = pctVs(pe, median);
    const vsF = pctVs(pe, forwardPE);
    return (
        <div className="bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-xl px-4 py-3 shadow-2xl text-sm min-w-[160px]">
            <p className="font-bold text-[var(--text-primary)] mb-2">{label}</p>
            <p className="text-[var(--accent-cyan)]">PE: <span className="font-mono font-bold">{pe}</span></p>
            {vsM && <p className="text-[var(--text-muted)] text-xs mt-1">vs Median: <span style={{ color: diffColor(vsM) }}>{sign(vsM)}{vsM}%</span></p>}
            {vsF && <p className="text-[var(--text-muted)] text-xs">vs Forward PE: <span style={{ color: diffColor(vsF) }}>{sign(vsF)}{vsF}%</span></p>}
        </div>
    );
}

// ── PE position meter ─────────────────────────────────────────────────────────
function PEMeter({ today, forwardPE, min, max, median, average }) {
    if (!min || !max) return null;
    const range = max - min;
    const pct   = v => v != null ? Math.max(2, Math.min(98, ((v - min) / range) * 100)) : null;

    const markers = [
        { value: median,    label: 'Median',     color: GREEN  },
        { value: average,   label: 'Average',    color: CYAN   },
        { value: today,     label: 'Trailing',   color: 'rgba(255,255,255,0.8)' },
        { value: forwardPE, label: 'Forward PE', color: AMBER  },
    ].filter(m => m.value != null);

    return (
        <div className="card !p-5">
            <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-4 uppercase tracking-wider">
                PE Position in Historical Range
            </h3>
            <div className="relative h-4 rounded-full bg-gradient-to-r from-[#34d399] via-[#fbbf24] to-[#f87171] mb-12">
                {markers.map(m => {
                    const left = pct(m.value);
                    return (
                        <div key={m.label} className="absolute top-1/2 -translate-y-1/2 flex flex-col items-center" style={{ left: `${left}%` }}>
                            <div className="w-3.5 h-3.5 rounded-full border-2 border-[var(--bg-primary)] shadow-lg" style={{ backgroundColor: m.color }} />
                            <div className="absolute top-6 text-center whitespace-nowrap">
                                <p className="text-[10px] font-bold" style={{ color: m.color }}>{m.label}</p>
                                <p className="text-[10px] font-mono text-[var(--text-muted)]">{m.value}</p>
                            </div>
                        </div>
                    );
                })}
            </div>
            <div className="flex justify-between text-xs text-[var(--text-muted)]">
                <span>Min {min}</span>
                <span>Max {max}</span>
            </div>
        </div>
    );
}

// ── forward PE comparison card ────────────────────────────────────────────────
function ForwardPECard({ forwardPE, trailingPE, median, nextEps, price }) {
    if (!forwardPE) return (
        <div className="card !p-5 border border-dashed border-[var(--glass-border)]">
            <p className="text-sm text-[var(--text-muted)]">Forward PE unavailable — no analyst EPS forecast in database.</p>
        </div>
    );

    const vsMedian   = pctVs(forwardPE, median);
    const vsTrailing = pctVs(forwardPE, trailingPE);
    const discount   = vsMedian != null ? -parseFloat(vsMedian) : null;   // positive = cheaper than median

    return (
        <div className="card !p-5 border border-[rgba(251,191,36,0.2)] bg-[rgba(251,191,36,0.03)]">
            <div className="flex items-start justify-between mb-4">
                <div>
                    <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider">Forward PE Analysis</h3>
                    <p className="text-xs text-[var(--text-muted)] mt-0.5">Calculated from analyst EPS forecast</p>
                </div>
                <div className={`flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold border ${
                    discount > 0
                        ? 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)] border-[rgba(52,211,153,0.2)]'
                        : 'bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)] border-[rgba(248,113,113,0.2)]'
                }`}>
                    {discount > 0 ? <TrendingDown size={11}/> : <TrendingUp size={11}/>}
                    {discount > 0 ? 'Cheaper' : 'Pricier'} than Median
                </div>
            </div>

            <div className="grid grid-cols-3 gap-4">
                {/* Forward PE */}
                <div className="text-center p-4 rounded-xl bg-[var(--bg-tertiary)]">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">Forward PE</p>
                    <p className="text-3xl font-extrabold font-mono tabular-nums" style={{ color: AMBER }}>{forwardPE}</p>
                    <p className="text-[10px] text-[var(--text-muted)] mt-1">Price / Next-Yr EPS</p>
                </div>

                {/* vs Median */}
                <div className="text-center p-4 rounded-xl bg-[var(--bg-tertiary)]">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">vs Median ({median})</p>
                    <p className="text-3xl font-extrabold font-mono tabular-nums" style={{ color: vsMedian != null ? diffColor(vsMedian) : 'var(--text-muted)' }}>
                        {vsMedian != null ? `${sign(vsMedian)}${vsMedian}%` : '—'}
                    </p>
                    <p className="text-[10px] text-[var(--text-muted)] mt-1">
                        {vsMedian != null ? (parseFloat(vsMedian) > 0 ? 'Above historical median' : 'Below historical median') : ''}
                    </p>
                </div>

                {/* vs Trailing PE */}
                <div className="text-center p-4 rounded-xl bg-[var(--bg-tertiary)]">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">vs Trailing PE ({trailingPE})</p>
                    <p className="text-3xl font-extrabold font-mono tabular-nums" style={{ color: vsTrailing != null ? diffColor(vsTrailing) : 'var(--text-muted)' }}>
                        {vsTrailing != null ? `${sign(vsTrailing)}${vsTrailing}%` : '—'}
                    </p>
                    <p className="text-[10px] text-[var(--text-muted)] mt-1">
                        {vsTrailing != null ? (parseFloat(vsTrailing) < 0 ? 'Earnings growing into price' : 'Earnings shrinking') : ''}
                    </p>
                </div>
            </div>

            {/* Calculation breakdown */}
            <div className="mt-4 pt-4 border-t border-[var(--glass-border)] flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-[var(--text-muted)]">
                <span><span className="text-[var(--text-secondary)]">Price:</span> <span className="font-mono">${price}</span></span>
                <span className="opacity-40">÷</span>
                <span><span className="text-[var(--text-secondary)]">Next-Yr EPS (analyst):</span> <span className="font-mono">${nextEps}</span></span>
                <span className="opacity-40">=</span>
                <span><span className="text-[var(--accent-amber)]">Forward PE:</span> <span className="font-mono font-bold">{forwardPE}</span></span>
            </div>
        </div>
    );
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function StockPE() {
    const [, params] = useRoute('/stock/:ticker/pe');
    const ticker = params?.ticker;

    const [peData,    setPeData]    = useState(null);
    const [stockData, setStockData] = useState(null);
    const [loading,   setLoading]   = useState(true);
    const [error,     setError]     = useState('');

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        Promise.all([
            axios.get(`/api/stocks/${ticker}/pe`),
            axios.get(`/api/stocks/${ticker}`).catch(() => ({ data: null })),
            axios.get(`/api/stocks/${ticker}/price`).catch(() => ({ data: null })),
        ]).then(([peRes, stockRes, priceRes]) => {
            setPeData(peRes.data);
            // Merge price into stockData so we always use the freshest price
            setStockData({ ...stockRes.data, livePrice: priceRes.data?.price });
            setLoading(false);
        }).catch(err => {
            setError(err.response?.data?.detail || 'Failed to load PE data.');
            setLoading(false);
        });
    }, [ticker]);

    if (loading) return (
        <div className="container py-20 flex justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-[var(--accent-purple)]" />
        </div>
    );

    if (error) return (
        <div className="container py-20 text-center space-y-2">
            <p className="text-[var(--text-muted)] text-sm">{error}</p>
            <p className="text-xs text-[var(--text-muted)] font-mono">
                python3 scripts/scrape_fullratio_pe.py --ticker {ticker}
            </p>
        </div>
    );

    if (!peData) return null;

    // ── derived values ────────────────────────────────────────────────────────
    const s       = peData.summary || {};
    const today   = s['Today'];
    const median  = s['Median'];
    const average = s['Average'];
    const min     = s['Minimum'];
    const max     = s['Maximum'];

    const lv       = stockData?.latest_valuation;
    const fc       = stockData?.latest_forecast;
    const nextEps  = fc?.next_year_eps ?? lv?.next_year_eps;
    const rawPrice = stockData?.livePrice ?? lv?.current_price ?? stockData?.latest_metric?.current_price;
    const price    = rawPrice ? fmt(rawPrice) : null;

    // Forward PE = current price / next-year analyst EPS
    const forwardPE = (price && nextEps && nextEps > 0) ? fmt(price / nextEps) : null;

    const vsMedian = pctVs(today, median);

    // Chart data: oldest → newest
    const chartData = [...(peData.records || [])].reverse();

    // ── stat rows ─────────────────────────────────────────────────────────────
    const topStats = [
        { label: 'Trailing PE', value: today,     color: today > median ? RED : GREEN,
          sub: vsMedian ? `${sign(vsMedian)}${vsMedian}% vs median` : null },
        { label: 'Forward PE',  value: forwardPE, color: AMBER,
          sub: forwardPE && median ? `${sign(pctVs(forwardPE, median))}${pctVs(forwardPE, median)}% vs median` : null },
        { label: 'Median',      value: median,    color: GREEN,  sub: 'Historical median' },
        { label: 'Average',     value: average,   color: CYAN,   sub: 'All-time average'  },
        { label: 'Min / Max',   value: `${min} / ${max}`, color: 'var(--text-secondary)', sub: 'Historical range' },
    ];

    const avgStats = [
        { label: '3-Year Avg',  value: s['3-year avg']  },
        { label: '5-Year Avg',  value: s['5-year avg']  },
        { label: '10-Year Avg', value: s['10-year avg'] },
        { label: '15-Year Avg', value: s['15-year avg'] },
    ].filter(a => a.value != null);

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            {/* Back */}
            <Link href={`/stock/${ticker}`}
                className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
                <ArrowLeft size={14} /> Back to {ticker}
            </Link>

            {/* Header */}
            <div className="flex items-end justify-between pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <h1 className="text-3xl font-extrabold tracking-tight">
                        <span className="font-mono text-[var(--accent-cyan)]">{ticker}</span>
                        <span className="text-[var(--text-secondary)] font-normal text-xl ml-3">PE Ratio History</span>
                    </h1>
                    <p className="text-xs text-[var(--text-muted)] mt-1">
                        Source: fullratio.com · {peData.date}
                        {price && <span className="ml-3">· Current price: <span className="font-mono">${price}</span></span>}
                    </p>
                </div>
                {vsMedian != null && (
                    <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold border ${
                        parseFloat(vsMedian) > 0
                            ? 'bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)] border-[rgba(248,113,113,0.2)]'
                            : 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)] border-[rgba(52,211,153,0.2)]'
                    }`}>
                        {parseFloat(vsMedian) > 0 ? <TrendingUp size={14}/> : <TrendingDown size={14}/>}
                        Trailing PE {sign(vsMedian)}{vsMedian}% vs Median
                    </div>
                )}
            </div>

            {/* Top stat cards */}
            <div className="grid grid-cols-5 gap-3">
                {topStats.map(({ label, value, color, sub }) => (
                    <div key={label} className="card !p-4 text-center">
                        <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">{label}</p>
                        <p className="text-2xl font-extrabold font-mono tabular-nums leading-none" style={{ color }}>
                            {value ?? '—'}
                        </p>
                        {sub && <p className="text-[10px] text-[var(--text-muted)] mt-1.5">{sub}</p>}
                    </div>
                ))}
            </div>

            {/* Forward PE breakdown card */}
            <ForwardPECard
                forwardPE={forwardPE}
                trailingPE={today}
                median={median}
                nextEps={nextEps ? fmt(nextEps) : null}
                price={price}
            />

            {/* Historical averages */}
            {avgStats.length > 0 && (
                <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${avgStats.length}, 1fr)` }}>
                    {avgStats.map(({ label, value }) => {
                        const diffT = pctVs(today,     value);
                        const diffF = pctVs(forwardPE, value);
                        return (
                            <div key={label} className="card !p-4">
                                <p className="text-xs text-[var(--text-muted)] mb-1">{label}</p>
                                <p className="text-xl font-bold font-mono mb-2">{value}</p>
                                <div className="space-y-1">
                                    {diffT && (
                                        <div className="flex items-center justify-between text-[11px]">
                                            <span className="text-[var(--text-muted)]">Trailing</span>
                                            <span className="font-semibold" style={{ color: diffColor(diffT) }}>{sign(diffT)}{diffT}%</span>
                                        </div>
                                    )}
                                    {diffF && (
                                        <div className="flex items-center justify-between text-[11px]">
                                            <span className="text-[var(--text-muted)]">Forward</span>
                                            <span className="font-semibold" style={{ color: diffColor(diffF) }}>{sign(diffF)}{diffF}%</span>
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* PE position meter */}
            <PEMeter
                today={today}
                forwardPE={forwardPE}
                min={min}
                max={max}
                median={median}
                average={average}
            />

            {/* Bar chart */}
            <div className="card !p-6">
                <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-6 uppercase tracking-wider">
                    Annual PE Ratio
                </h3>
                <div className="h-[340px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData} margin={{ top: 10, right: 80, bottom: 0, left: -10 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                            <XAxis dataKey="date" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={{ stroke: 'rgba(255,255,255,0.06)' }} tickLine={false} />
                            <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} domain={[0, 'auto']} />
                            <Tooltip content={<PETooltip median={median} forwardPE={forwardPE} />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />

                            {median && (
                                <ReferenceLine y={median} stroke={GREEN} strokeDasharray="6 3" strokeWidth={1.5}
                                    label={{ value: `Median ${median}`, position: 'right', fill: GREEN, fontSize: 11 }} />
                            )}
                            {average && (
                                <ReferenceLine y={average} stroke={CYAN} strokeDasharray="6 3" strokeWidth={1.5}
                                    label={{ value: `Avg ${average}`, position: 'right', fill: CYAN, fontSize: 11 }} />
                            )}
                            {today && (
                                <ReferenceLine y={today} stroke="rgba(255,255,255,0.45)" strokeDasharray="3 3" strokeWidth={1}
                                    label={{ value: `Trailing ${today}`, position: 'right', fill: 'rgba(255,255,255,0.5)', fontSize: 11 }} />
                            )}
                            {forwardPE && (
                                <ReferenceLine y={forwardPE} stroke={AMBER} strokeDasharray="4 3" strokeWidth={2}
                                    label={{ value: `Fwd PE ${forwardPE}`, position: 'right', fill: AMBER, fontSize: 11 }} />
                            )}

                            <Bar dataKey="pe" name="Annual PE" radius={[4, 4, 0, 0]} maxBarSize={48}>
                                {chartData.map(entry => (
                                    <Cell key={entry.date} fill={barColor(entry.pe, median)} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Legend */}
                <div className="flex flex-wrap items-center gap-5 mt-4 pt-4 border-t border-[var(--glass-border)] text-xs text-[var(--text-muted)]">
                    <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm inline-block bg-[#34d399]"/>Cheap (&lt;70% median)</span>
                    <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm inline-block" style={{backgroundColor:CYAN}}/>Fair (90–110%)</span>
                    <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm inline-block bg-[#fbbf24]"/>Elevated (110–140%)</span>
                    <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm inline-block bg-[#f87171]"/>Expensive (&gt;140%)</span>
                    <span className="flex items-center gap-1.5 ml-auto">
                        <span className="inline-block w-6 border-t-2 border-dashed" style={{borderColor:AMBER}}/>
                        Forward PE
                    </span>
                </div>
            </div>
        </div>
    );
}
