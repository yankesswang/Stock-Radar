import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import { ArrowLeft, ChevronDown, TrendingUp, TrendingDown, Minus } from 'lucide-react';

// Section icons as simple colored squares - map sections to accent colors
const SECTION_COLORS = {
    'Total Valuation':          'var(--accent-cyan)',
    'Important Dates':          'var(--text-muted)',
    'Share Statistics':         'var(--accent-purple)',
    'Valuation Ratios':         'var(--accent-cyan)',
    'Enterprise Valuation':     '#60a5fa',
    'Financial Position':       'var(--accent-amber)',
    'Financial Efficiency':     'var(--accent-success)',
    'Taxes':                    'var(--text-muted)',
    'Stock Price Statistics':   'var(--accent-purple)',
    'Short Selling Information':'var(--accent-danger)',
    'Income Statement':         'var(--accent-cyan)',
    'Balance Sheet':            '#a78bfa',
    'Cash Flow':                'var(--accent-success)',
    'Margins':                  'var(--accent-amber)',
    'Dividends & Yields':      '#f472b6',
    'Analyst Forecast':         'var(--accent-cyan)',
    'Fair Value':               'var(--accent-success)',
    'Stock Splits':             'var(--text-muted)',
    'Scores':                   'var(--accent-amber)',
};

function formatDisplay(val, display) {
    // Use the original display string from the scraper when available
    if (display && display !== '—') return display;
    if (val == null) return '—';
    return String(val);
}

function getValueColor(label, val) {
    if (val == null || typeof val === 'string') return '';

    const l = label.toLowerCase();

    // Growth / change / upside / yield fields - green if positive, red if negative
    if (l.includes('growth') || l.includes('change') || l.includes('upside')
        || l.includes('yield') || l.includes('return') || l.includes('difference')) {
        return val > 0 ? 'stat-val-positive' : val < 0 ? 'stat-val-negative' : '';
    }

    return '';
}

export default function StockStatistics() {
    const [, params] = useRoute('/stock/:ticker/statistics');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [openSections, setOpenSections] = useState({});

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/statistics`)
            .then(res => {
                setData(res.data);
                const open = {};
                res.data.sections.forEach(s => { open[s.name] = true; });
                setOpenSections(open);
            })
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load statistics');
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

    const totalItems = data.sections.reduce((sum, s) => sum + s.items.length, 0);

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
            <div className="pb-6 border-b border-[var(--glass-border)]">
                <div className="flex items-center gap-3 mb-2">
                    <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
                    <span className="badge badge-neutral">Key Statistics</span>
                </div>
                <p className="text-[15px] text-[var(--text-muted)]">
                    {totalItems} data points across {data.sections.length} sections &middot; Data as of {data.date}
                </p>
            </div>

            {/* Sections grid - 2 columns on large screens */}
            <div className="stat-grid">
                {data.sections.map((section) => {
                    const color = SECTION_COLORS[section.name] || 'var(--text-muted)';
                    const isOpen = openSections[section.name];

                    return (
                        <div key={section.name} className="stat-section">
                            {/* Section header */}
                            <button
                                onClick={() => toggleSection(section.name)}
                                className="stat-section-header"
                            >
                                <div className="flex items-center gap-3">
                                    <span
                                        className="w-3 h-3 rounded-sm shrink-0"
                                        style={{ background: color }}
                                    />
                                    <span className="text-[15px] font-semibold">{section.name}</span>
                                    <span className="text-[13px] text-[var(--text-muted)] font-normal">
                                        {section.items.length}
                                    </span>
                                </div>
                                <ChevronDown
                                    size={16}
                                    className={`text-[var(--text-muted)] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
                                />
                            </button>

                            {/* Items */}
                            {isOpen && (
                                <div className="stat-items">
                                    {section.items.map((item) => {
                                        const valColor = getValueColor(item.label, item.value);
                                        return (
                                            <div key={item.label} className="stat-row">
                                                <span className="stat-label">{item.label}</span>
                                                <span className={`stat-value ${valColor}`}>
                                                    {formatDisplay(item.value, item.display)}
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
