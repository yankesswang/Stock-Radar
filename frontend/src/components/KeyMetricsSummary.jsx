import { useState, useEffect } from 'react';
import axios from '../lib/apiClient';

const GROUP_COLORS = {
    cyan:    { border: 'rgba(0,212,255,0.18)',  bg: 'rgba(0,212,255,0.05)',  accent: 'var(--accent-cyan)',    tab: 'rgba(0,212,255,0.12)' },
    purple:  { border: 'rgba(139,92,246,0.18)', bg: 'rgba(139,92,246,0.05)', accent: 'var(--accent-purple)',  tab: 'rgba(139,92,246,0.12)' },
    success: { border: 'rgba(52,211,153,0.18)', bg: 'rgba(52,211,153,0.05)', accent: 'var(--accent-success)', tab: 'rgba(52,211,153,0.12)' },
    amber:   { border: 'rgba(251,191,36,0.18)', bg: 'rgba(251,191,36,0.05)', accent: 'var(--accent-amber)',   tab: 'rgba(251,191,36,0.12)' },
    danger:  { border: 'rgba(248,113,113,0.18)',bg: 'rgba(248,113,113,0.05)',accent: 'var(--accent-danger)',  tab: 'rgba(248,113,113,0.12)' },
};

/** Round all decimal numbers in a display string to 2 decimal places */
function fmt2(display) {
    if (!display || display === '—') return '—';
    return display.replace(/-?\d+\.\d+/g, n => parseFloat(n).toFixed(2));
}

/** Decide text colour for a metric value */
function valueColor(metric) {
    const { label, value } = metric;
    if (value == null || typeof value === 'string') return '';
    const l = label.toLowerCase();
    const positiveGreen = l.includes('growth') || l.includes('margin') || l.includes('return')
        || l.includes('roe') || l.includes('roa') || l.includes('roic')
        || l.includes('upside') || l.includes('52w') || l.includes('yield');
    if (positiveGreen) {
        return value > 0 ? 'text-[var(--accent-success)]' : value < 0 ? 'text-[var(--accent-danger)]' : '';
    }
    return '';
}

function Tile({ metric }) {
    const [tip, setTip] = useState(false);
    const col = valueColor(metric);

    return (
        <div className="relative flex flex-col justify-between p-3 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)] hover:border-[var(--glass-border-hover)] transition-colors min-h-[72px]">
            <div className="flex items-start justify-between gap-1">
                <span className="text-[11px] text-[var(--text-muted)] leading-tight">{metric.label}</span>
                {metric.hint && (
                    <button
                        className="shrink-0 text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
                        onClick={() => setTip(t => !t)}
                        aria-label="info"
                    >
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
                            <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm0 3a1 1 0 1 1 0 2 1 1 0 0 1 0-2Zm-1 3h2v4H7V7Z" />
                        </svg>
                    </button>
                )}
            </div>
            {tip && metric.hint && (
                <div className="absolute z-20 top-full left-0 right-0 mt-1 px-2 py-1.5 rounded-lg text-[11px] bg-[var(--bg-primary)] border border-[var(--glass-border)] text-[var(--text-secondary)] shadow-xl">
                    {metric.hint}
                </div>
            )}
            <div className={`text-[15px] font-bold font-mono tabular-nums mt-1 ${col}`}>
                {fmt2(metric.display)}
            </div>
            {metric.sub && (
                <div className="text-[11px] text-[var(--text-muted)] font-mono tabular-nums mt-0.5">
                    {metric.sub}
                </div>
            )}
        </div>
    );
}

export default function KeyMetricsSummary({ ticker }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState(0);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/summary`)
            .then(res => { setData(res.data); setLoading(false); })
            .catch(() => setLoading(false));
    }, [ticker]);

    if (loading) {
        return (
            <div className="card !p-6">
                <div className="h-5 w-40 bg-[var(--bg-tertiary)] rounded animate-pulse mb-4" />
                <div className="grid grid-cols-4 gap-3">
                    {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="h-[72px] rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />
                    ))}
                </div>
            </div>
        );
    }

    if (!data?.groups?.length) return null;

    const groups = data.groups;
    const group = groups[activeTab];
    const colors = GROUP_COLORS[group.color] ?? GROUP_COLORS.cyan;

    return (
        <div className="card !p-0 overflow-hidden">
            {/* Tab bar */}
            <div className="flex items-center gap-1 px-4 pt-4 pb-0 flex-wrap">
                <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider mr-2 shrink-0">
                    Key Metrics
                </span>
                {groups.map((g, i) => {
                    return (
                        <button
                            key={g.name}
                            onClick={() => setActiveTab(i)}
                            className={`px-3 py-1.5 rounded-t-lg text-[12px] font-semibold transition-colors border-b-2 ${
                                i === activeTab
                                    ? 'border-[var(--accent-color)] text-white'
                                    : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
                            }`}
                            style={i === activeTab ? {
                                background: colors.tab,
                                borderColor: colors.accent,
                                color: colors.accent,
                            } : {}}
                        >
                            {g.name}
                        </button>
                    );
                })}
            </div>

            {/* Divider */}
            <div className="h-px mx-4 bg-[var(--glass-border)]" />

            {/* Metrics grid */}
            <div className="p-4">
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                    {group.metrics.map(m => (
                        <Tile key={m.label} metric={m} />
                    ))}
                </div>
            </div>
        </div>
    );
}
