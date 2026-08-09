import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts';
import {
    ArrowLeft, TrendingUp, TrendingDown, DollarSign, AlertTriangle,
} from 'lucide-react';
import SensitivityMatrix from '../components/SensitivityMatrix';
import { CHART_GRID, CHART_AXIS } from '../components/chartTheme';

function fmt(val, type) {
    if (val == null) return '\u2014';
    if (type === 'pct') return `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    if (type === 'dollar') return `$${val.toFixed(2)}`;
    if (type === 'big') {
        const abs = Math.abs(val);
        if (abs >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
        if (abs >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
        if (abs >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
        return `$${val.toFixed(0)}`;
    }
    return String(val);
}

const BRIDGE_COLORS = ['var(--accent-purple)', 'var(--accent-cyan)', 'var(--accent-amber)', 'var(--accent-success)'];

const SCENARIO_META = {
    optimistic:  { label: 'Optimistic',  color: 'var(--accent-success)', bg: 'rgba(52,211,153,0.08)',  border: 'rgba(52,211,153,0.2)' },
    moderate:    { label: 'Moderate',     color: 'var(--accent-cyan)',    bg: 'rgba(0,212,255,0.08)',   border: 'rgba(0,212,255,0.2)' },
    pessimistic: { label: 'Pessimistic',  color: 'var(--accent-danger)',  bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.2)' },
};

export default function StockDCF() {
    const [, params] = useRoute('/stock/:ticker/dcf');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [activeScenario, setActiveScenario] = useState('moderate');

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/dcf`)
            .then(res => setData(res.data))
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load DCF data');
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

    if (!data || !data.scenarios) return null;

    const sc = data.scenarios[activeScenario];
    if (!sc) return null;

    const { assumptions, summary, projections, value_bridge, sensitivity, warnings } = sc;
    const isUndervalued = sc.verdict === 'Undervalued';

    const bridgeData = (value_bridge || []).map((item, i) => ({
        name: item.label,
        value: item.value,
        fill: BRIDGE_COLORS[i % BRIDGE_COLORS.length],
    }));

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
                    <span className="badge badge-neutral">DCF Valuation</span>
                </div>
                <p className="text-[15px] text-[var(--text-muted)]">
                    Data as of {data.data_date} &middot; Computed {data.computed_date}
                </p>
            </div>

            {/* ── 3-Scenario Overview Cards ── */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {['pessimistic', 'moderate', 'optimistic'].map((key) => {
                    const s = data.scenarios[key];
                    const meta = SCENARIO_META[key];
                    const active = activeScenario === key;
                    const up = s.upside_pct;
                    return (
                        <button
                            key={key}
                            onClick={() => setActiveScenario(key)}
                            className="card !p-5 text-left transition-all"
                            style={{
                                transform: 'none',
                                borderColor: active ? meta.border : undefined,
                                background: active ? meta.bg : undefined,
                                outline: active ? `2px solid ${meta.color}` : 'none',
                                outlineOffset: '-1px',
                            }}
                        >
                            <div className="flex items-center justify-between mb-3">
                                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: meta.color }}>
                                    {meta.label}
                                </span>
                                {s.verdict && (
                                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                                        s.verdict === 'Undervalued'
                                            ? 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)]'
                                            : 'bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)]'
                                    }`}>
                                        {s.verdict}
                                    </span>
                                )}
                            </div>
                            <div className="text-2xl font-extrabold font-mono tabular-nums mb-1">
                                {fmt(s.intrinsic_value, 'dollar')}
                            </div>
                            <div className="flex items-center gap-3 text-sm">
                                {up != null && (
                                    <span className={`font-semibold font-mono ${
                                        up >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                    }`}>
                                        {up > 0 ? '+' : ''}{up}%
                                    </span>
                                )}
                                <span className="text-[var(--text-muted)] text-xs">
                                    WACC {s.assumptions.effective_wacc}% &middot; TG {s.assumptions.terminal_growth}%
                                </span>
                            </div>
                        </button>
                    );
                })}
            </div>

            {/* Current Price reference */}
            <div className="flex items-center gap-4 px-1">
                <span className="text-sm text-[var(--text-muted)]">Current Price:</span>
                <span className="text-lg font-extrabold font-mono">{fmt(data.current_price, 'dollar')}</span>
            </div>

            {/* ── Scenario Detail Tabs ── */}
            <div className="flex items-center gap-1 p-1 rounded-xl bg-[var(--bg-secondary)] border border-[var(--glass-border)] w-fit">
                {['pessimistic', 'moderate', 'optimistic'].map((key) => {
                    const meta = SCENARIO_META[key];
                    const active = activeScenario === key;
                    return (
                        <button
                            key={key}
                            onClick={() => setActiveScenario(key)}
                            className="px-4 py-1.5 rounded-lg text-[13px] font-semibold transition-all"
                            style={{
                                background: active ? meta.bg : 'transparent',
                                color: active ? meta.color : 'var(--text-muted)',
                                border: active ? `1px solid ${meta.border}` : '1px solid transparent',
                            }}
                        >
                            {meta.label}
                        </button>
                    );
                })}
            </div>

            {/* Verdict Card */}
            {sc.verdict && (
                <div className={`card !p-5 flex flex-col sm:flex-row items-start sm:items-center gap-4 ${
                    isUndervalued
                        ? '!border-[rgba(52,211,153,0.25)]'
                        : '!border-[rgba(248,113,113,0.25)]'
                }`} style={{ transform: 'none' }}>
                    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-semibold ${
                        isUndervalued
                            ? 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)] border border-[rgba(52,211,153,0.15)]'
                            : 'bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)] border border-[rgba(248,113,113,0.15)]'
                    }`}>
                        {isUndervalued ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                        {sc.verdict}
                    </div>
                    <div className="flex items-center gap-6">
                        <div>
                            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">Upside</div>
                            <div className={`text-lg font-bold font-mono ${
                                isUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                            }`}>
                                {sc.upside_pct > 0 ? '+' : ''}{sc.upside_pct}%
                            </div>
                        </div>
                        <div>
                            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">Margin of Safety</div>
                            <div className={`text-lg font-bold font-mono ${
                                isUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                            }`}>
                                {sc.margin_of_safety > 0 ? '+' : ''}{sc.margin_of_safety}%
                            </div>
                        </div>
                        {summary && (
                            <div>
                                <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">Terminal Value %</div>
                                <div className="text-lg font-bold font-mono text-[var(--text-secondary)]">
                                    {summary.terminal_value_pct}%
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Value Bridge Chart */}
            {bridgeData.length > 0 && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <DollarSign size={16} className="text-[var(--accent-purple)]" /> Value Bridge
                    </h3>
                    <div className="card !p-6" style={{ transform: 'none' }}>
                        <div className="h-[280px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={bridgeData} margin={{ top: 5, right: 20, bottom: 5, left: 20 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                                    <XAxis
                                        dataKey="name"
                                        stroke="var(--text-muted)"
                                        fontSize={12}
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
                                        tickFormatter={(v) => fmt(v, 'big')}
                                    />
                                    <Tooltip
                                        contentStyle={{
                                            backgroundColor: 'var(--bg-secondary)',
                                            border: '1px solid var(--glass-border)',
                                            borderRadius: '10px',
                                            fontSize: '12px',
                                            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                                        }}
                                        formatter={(val) => [fmt(val, 'big'), 'Value']}
                                        labelStyle={{ color: 'var(--text-muted)', marginBottom: '4px' }}
                                    />
                                    <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                                        {bridgeData.map((entry, i) => (
                                            <Cell key={i} fill={entry.fill} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                </div>
            )}

            {/* Projection Table */}
            {projections && projections.length > 0 && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4 flex items-center gap-2">
                        <TrendingUp size={16} className="text-[var(--accent-success)]" /> 5-Year FCF Projection
                        <span className="text-xs font-normal text-[var(--text-muted)]">({SCENARIO_META[activeScenario].label})</span>
                    </h3>
                    <div className="metrics-section">
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">Year</th>
                                        <th className="metrics-th">Revenue</th>
                                        <th className="metrics-th">Growth</th>
                                        <th className="metrics-th">FCF Margin</th>
                                        <th className="metrics-th">FCF</th>
                                        <th className="metrics-th">Discount Factor</th>
                                        <th className="metrics-th">Present Value</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {projections.map((p) => (
                                        <tr key={p.year} className="metrics-tr">
                                            <td className="metrics-td-sticky">
                                                <span className="text-[14px] font-semibold text-[var(--text-primary)]">
                                                    Year {p.year}
                                                </span>
                                            </td>
                                            <td className="metrics-td">{fmt(p.revenue, 'big')}</td>
                                            <td className={`metrics-td font-semibold ${
                                                p.growth_rate >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                            }`}>
                                                {p.growth_rate > 0 ? '+' : ''}{p.growth_rate.toFixed(2)}%
                                            </td>
                                            <td className="metrics-td">{p.fcf_margin.toFixed(2)}%</td>
                                            <td className="metrics-td">{fmt(p.fcf, 'big')}</td>
                                            <td className="metrics-td text-[var(--text-muted)]">{p.discount_factor.toFixed(4)}</td>
                                            <td className="metrics-td font-semibold">{fmt(p.present_value, 'big')}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}

            {/* Assumptions Card */}
            {assumptions && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4">
                        Key Assumptions
                        <span className="text-xs font-normal text-[var(--text-muted)] ml-2">({SCENARIO_META[activeScenario].label})</span>
                    </h3>
                    <div className="card !p-5" style={{ transform: 'none' }}>
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-8 gap-y-4">
                            <AssumptionItem label="WACC" value={`${assumptions.effective_wacc}%`} />
                            <AssumptionItem label="WACC Source" value={assumptions.wacc_source} />
                            <AssumptionItem label="Terminal Growth" value={`${assumptions.terminal_growth}%`} />
                            <AssumptionItem label="Risk-Free Rate" value={`${assumptions.risk_free_rate}%`} />
                            <AssumptionItem label="Equity Risk Premium" value={`${assumptions.equity_risk_premium}%`} />
                            <AssumptionItem label="Beta" value={assumptions.beta != null ? assumptions.beta.toFixed(2) : '\u2014'} />
                            <AssumptionItem label="Effective Tax Rate" value={assumptions.tax_rate != null ? `${assumptions.tax_rate}%` : '\u2014'} />
                            <AssumptionItem label="FCF Margin" value={assumptions.fcf_margin != null ? `${assumptions.fcf_margin}%` : '\u2014'} />
                            <AssumptionItem label="Yr 1 Revenue Growth" value={`${assumptions.growth_yr1}%`} />
                            <AssumptionItem label="Yr 2 Revenue Growth" value={`${assumptions.growth_yr2}%`} />
                            <AssumptionItem label="Projection Years" value={assumptions.projection_years} />
                        </div>
                    </div>
                </div>
            )}

            {/* Sensitivity Matrix (moderate only) */}
            {sensitivity && (
                <div>
                    <h3 className="text-base font-semibold text-[var(--text-secondary)] mb-4">Sensitivity Analysis (WACC vs Terminal Growth)</h3>
                    <SensitivityMatrix matrix={sensitivity} currentPrice={data.current_price} />
                </div>
            )}

            {/* Warnings */}
            {warnings && warnings.length > 0 && (
                <div className="card !p-5 !border-[rgba(251,191,36,0.2)]" style={{ transform: 'none' }}>
                    <div className="flex items-center gap-2 mb-3">
                        <AlertTriangle size={16} className="text-[var(--accent-amber)]" />
                        <span className="text-sm font-semibold text-[var(--accent-amber)]">Calculation Notes</span>
                    </div>
                    <ul className="space-y-1.5">
                        {warnings.map((w, i) => (
                            <li key={i} className="text-[13px] text-[var(--text-muted)] flex items-start gap-2">
                                <span className="text-[var(--accent-amber)] mt-0.5 shrink-0">&bull;</span>
                                {w}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

function AssumptionItem({ label, value }) {
    return (
        <div>
            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">{label}</div>
            <div className="text-[14px] font-semibold font-mono text-[var(--text-primary)]">{value}</div>
        </div>
    );
}
