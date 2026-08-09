import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, LabelList,
} from 'recharts';
import { ArrowLeft, ChevronDown, BarChart3 } from 'lucide-react';
import { CHART_GRID, CHART_AXIS } from '../components/chartTheme';

const COLORS = [
    'var(--accent-cyan)',
    'var(--accent-purple)',
    'var(--accent-success)',
    'var(--accent-amber)',
    'var(--accent-danger)',
    '#60a5fa',
    '#a78bfa',
    '#f472b6',
    '#2dd4bf',
    '#fb923c',
];

// Detect the display type for a metric: 'pct' | 'dollar' | 'ratio'
function getMetricType(metricName) {
    const name = metricName.toLowerCase();
    const isPct = name.includes('growth') || name.includes('yield') || name.includes('margin')
        || name.includes('return') || name.includes('payout') || name.includes('roe')
        || name.includes('roa') || name.includes('roic') || name.includes('roce')
        || name.includes('dilution');
    if (isPct) return 'pct';

    const isDollar = name.includes('market cap') || name.includes('enterprise value')
        || name.includes('last close') || name.includes('price');
    if (isDollar) return 'dollar';

    return 'ratio';
}

// Human-readable unit label
const TYPE_LABELS = { pct: 'Percentage (%)', dollar: 'USD ($)', ratio: 'Ratio (x)' };

// Format ratio values intelligently based on metric name
function formatRatioValue(val, metricName) {
    if (val == null) return '—';
    const type = getMetricType(metricName);

    if (type === 'pct') return `${val.toFixed(2)}%`;

    if (type === 'dollar') {
        const abs = Math.abs(val);
        if (abs >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
        if (abs >= 1e3) return `$${(val / 1e3).toFixed(2)}K`;
        return `$${val.toFixed(2)}`;
    }

    return `${val.toFixed(2)}x`;
}

// Compact label for rendering on top of bars
function formatBarLabel(val, metricName) {
    if (val == null) return '';
    const type = getMetricType(metricName);

    if (type === 'pct') return `${val.toFixed(1)}%`;

    if (type === 'dollar') {
        const abs = Math.abs(val);
        if (abs >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
        if (abs >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
        return `$${val.toFixed(1)}`;
    }

    return `${val.toFixed(2)}x`;
}

export default function StockRatios() {
    const [, params] = useRoute('/stock/:ticker/ratios');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [openSections, setOpenSections] = useState({});
    const [selectedMetric, setSelectedMetric] = useState(null);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/ratios`)
            .then(res => {
                setData(res.data);
                const open = {};
                res.data.sections.forEach(s => { open[s.name] = true; });
                setOpenSections(open);
                const first = res.data.sections[0]?.metrics[0];
                if (first) setSelectedMetric({ section: res.data.sections[0].name, metric: first.name });
            })
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load ratios data');
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

    const toggleSection = (name) => setOpenSections(prev => ({ ...prev, [name]: !prev[name] }));

    // Build chart data for the selected metric
    let chartData = [];
    if (selectedMetric && data) {
        const sec = data.sections.find(s => s.name === selectedMetric.section);
        const met = sec?.metrics.find(m => m.name === selectedMetric.metric);
        if (met) {
            chartData = data.periods
                .map((p, i) => ({ period: p, value: met.values[i] }))
                .filter(d => d.value != null)
                .reverse();
        }
    }

    const selectedType = selectedMetric ? getMetricType(selectedMetric.metric) : 'ratio';

    const totalMetrics = data.sections.reduce((sum, s) => sum + s.metrics.length, 0);

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            {/* Navigation */}
            <Link
                href={`/stock/${ticker}`}
                className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            >
                <ArrowLeft size={14} /> Back to {ticker}
            </Link>

            {/* Header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <div className="flex items-center gap-3 mb-2">
                        <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
                        <span className="badge badge-neutral">Financial Ratios</span>
                    </div>
                    <p className="text-[15px] text-[var(--text-muted)]">
                        {totalMetrics} ratios &middot; {data.periods.length} fiscal periods &middot; Data as of {data.date}
                    </p>
                </div>
            </div>

            {/* Chart */}
            {chartData.length > 0 && selectedMetric && (
                <div className="metrics-chart-card">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2.5">
                            <BarChart3 size={16} className="text-[var(--text-muted)] opacity-60" />
                            <h3 className="text-[15px] font-semibold text-[var(--text-secondary)]">
                                {selectedMetric.metric}
                            </h3>
                            <span className={`px-2 py-0.5 rounded-md text-[11px] font-bold uppercase tracking-wider ${
                                selectedType === 'pct'
                                    ? 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)] border border-[rgba(52,211,153,0.2)]'
                                    : selectedType === 'dollar'
                                        ? 'bg-[rgba(0,212,255,0.1)] text-[var(--accent-cyan)] border border-[rgba(0,212,255,0.2)]'
                                        : 'bg-[rgba(251,191,36,0.1)] text-[var(--accent-amber)] border border-[rgba(251,191,36,0.2)]'
                            }`}>
                                {TYPE_LABELS[selectedType]}
                            </span>
                        </div>
                        <span className="text-[13px] text-[var(--text-muted)]">
                            {selectedMetric.section}
                        </span>
                    </div>
                    <div className="h-[300px]">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} margin={{ top: 28, right: 10, bottom: 5, left: 10 }}>
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
                                    tickFormatter={v => {
                                        if (selectedType === 'pct') return `${v}%`;
                                        if (selectedType === 'dollar') {
                                            if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
                                            if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
                                            return `$${v}`;
                                        }
                                        return `${v}x`;
                                    }}
                                />
                                <Tooltip
                                    contentStyle={{
                                        backgroundColor: 'var(--bg-secondary)',
                                        border: '1px solid var(--glass-border)',
                                        borderRadius: '10px',
                                        fontSize: '13px',
                                        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                                    }}
                                    labelStyle={{ color: 'var(--text-muted)', marginBottom: 4 }}
                                    formatter={(val) => [formatRatioValue(val, selectedMetric.metric), selectedMetric.metric]}
                                />
                                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                                    {chartData.map((entry, i) => (
                                        <Cell
                                            key={i}
                                            fill={entry.value < 0 ? 'var(--accent-danger)' : 'var(--accent-cyan)'}
                                            fillOpacity={0.75}
                                        />
                                    ))}
                                    <LabelList
                                        dataKey="value"
                                        position="top"
                                        formatter={(val) => formatBarLabel(val, selectedMetric.metric)}
                                        style={{
                                            fill: 'var(--text-secondary)',
                                            fontSize: 11,
                                            fontWeight: 600,
                                            fontFamily: "'SF Mono', 'Fira Code', monospace",
                                        }}
                                    />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

            {/* Section tables */}
            {data.sections.map((sec, secIdx) => (
                <div key={sec.name} className="metrics-section">
                    {/* Section header */}
                    <button
                        onClick={() => toggleSection(sec.name)}
                        className="metrics-section-header"
                    >
                        <div className="flex items-center gap-3">
                            <span
                                className="w-3 h-3 rounded-sm shrink-0"
                                style={{ background: COLORS[secIdx % COLORS.length] }}
                            />
                            <span className="text-[15px] font-semibold">{sec.name}</span>
                            <span className="text-[13px] text-[var(--text-muted)] font-normal">
                                {sec.metrics.length} ratios
                            </span>
                        </div>
                        <ChevronDown
                            size={16}
                            className={`text-[var(--text-muted)] transition-transform duration-200 ${openSections[sec.name] ? 'rotate-180' : ''}`}
                        />
                    </button>

                    {/* Table */}
                    {openSections[sec.name] && (
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">Metric</th>
                                        {data.periods.map(p => (
                                            <th key={p} className="metrics-th">{p}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {sec.metrics.map((met) => {
                                        const isActive = selectedMetric?.section === sec.name && selectedMetric?.metric === met.name;
                                        const name = met.name.toLowerCase();
                                        const isGrowthOrYield = name.includes('growth') || name.includes('yield')
                                            || name.includes('return') || name.includes('dilution');

                                        return (
                                            <tr
                                                key={met.name}
                                                onClick={() => setSelectedMetric({ section: sec.name, metric: met.name })}
                                                className={`metrics-tr ${isActive ? 'active' : ''}`}
                                            >
                                                <td className="metrics-td-sticky">
                                                    <span className="text-[14px] font-medium text-[var(--text-primary)]">
                                                        {met.name}
                                                    </span>
                                                </td>
                                                {data.periods.map((p, pIdx) => {
                                                    const val = met.values[pIdx];

                                                    let textColor = 'text-[var(--text-secondary)]';
                                                    if (isGrowthOrYield && val != null) {
                                                        textColor = val >= 0
                                                            ? 'text-[var(--accent-success)]'
                                                            : 'text-[var(--accent-danger)]';
                                                    }

                                                    return (
                                                        <td key={p} className={`metrics-td ${textColor}`}>
                                                            {formatRatioValue(val, met.name)}
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}
