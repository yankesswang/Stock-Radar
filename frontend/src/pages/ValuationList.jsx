import { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { Link } from 'wouter';
import {
    ChevronLeft, ChevronRight, ArrowUp, ArrowDown, ChevronsUpDown,
    SlidersHorizontal, X, ChevronDown, Check, Search, RotateCcw, CalendarDays,
    RefreshCw, Clock,
} from 'lucide-react';

export default function ValuationList() {
    const [data, setData] = useState([]);
    const [pag, setPag] = useState({});
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [sortBy, setSortBy] = useState('industry');
    const [sortDir, setSortDir] = useState('asc');
    const [livePrices, setLivePrices] = useState({});

    // Filter state
    const [search, setSearch] = useState('');
    const [industries, setIndustries] = useState([]);
    const [status, setStatus] = useState(null);
    const [priceMin, setPriceMin] = useState('');
    const [priceMax, setPriceMax] = useState('');
    const [diffMin, setDiffMin] = useState('');
    const [diffMax, setDiffMax] = useState('');
    const [peMin, setPeMin] = useState('');
    const [peMax, setPeMax] = useState('');
    const [epsGrowthMin, setEpsGrowthMin] = useState('');
    const [epsGrowthMax, setEpsGrowthMax] = useState('');
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [industryList, setIndustryList] = useState([]);

    const [earningsDates,  setEarningsDates]  = useState({});
    const [schedStatus,    setSchedStatus]    = useState(null);
    const [schedRunning,   setSchedRunning]   = useState(false);
    const debounceRef = useRef(null);

    useEffect(() => {
        axios.get('/api/industries')
            .then(res => setIndustryList(res.data.map(i => i.name).sort()))
            .catch(err => console.error('Failed to fetch industries:', err));
    }, []);

    useEffect(() => {
        axios.get('/api/earnings/calendar')
            .then(res => {
                const map = {};
                for (const ev of (res.data.events || [])) map[ev.ticker] = ev.date;
                setEarningsDates(map);
            })
            .catch(() => {});
    }, []);

    // Scheduler status (poll every 5s while running)
    useEffect(() => {
        let interval;
        const fetchStatus = () => {
            axios.get('/api/scheduler/status')
                .then(res => { setSchedStatus(res.data); setSchedRunning(res.data.status === 'running'); })
                .catch(() => {});
        };
        fetchStatus();
        interval = setInterval(fetchStatus, 5000);
        return () => clearInterval(interval);
    }, []);

    const triggerRefresh = () => {
        if (schedRunning) return;
        setSchedRunning(true);
        axios.post('/api/scheduler/run').catch(() => setSchedRunning(false));
    };

    const hasActiveFilters = () => {
        return search || industries.length > 0 || status ||
            priceMin || priceMax || diffMin || diffMax ||
            peMin || peMax || epsGrowthMin || epsGrowthMax;
    };

    const activeFilterCount = () => {
        let count = 0;
        if (search) count++;
        if (industries.length > 0) count++;
        if (status) count++;
        if (priceMin || priceMax) count++;
        if (diffMin || diffMax) count++;
        if (peMin || peMax) count++;
        if (epsGrowthMin || epsGrowthMax) count++;
        return count;
    };

    const clearAllFilters = () => {
        setSearch('');
        setIndustries([]);
        setStatus(null);
        setPriceMin('');
        setPriceMax('');
        setDiffMin('');
        setDiffMax('');
        setPeMin('');
        setPeMax('');
        setEpsGrowthMin('');
        setEpsGrowthMax('');
        setPage(1);
    };

    const buildScreenerFilters = () => {
        const filters = [];
        const addRange = (field, min, max) => {
            if (min !== '') filters.push({ field, op: 'gte', value: Number(min) });
            if (max !== '') filters.push({ field, op: 'lte', value: Number(max) });
        };
        addRange('current_price', priceMin, priceMax);
        addRange('valuation_diff_pct', diffMin, diffMax);
        addRange('pe_median_5y', peMin, peMax);
        addRange('eps_growth_5y', epsGrowthMin, epsGrowthMax);
        return filters;
    };

    const hasScreenerFilters = () => {
        return industries.length > 0 || status ||
            priceMin || priceMax || diffMin || diffMax ||
            peMin || peMax || epsGrowthMin || epsGrowthMax;
    };

    const fetchValuations = useCallback(async () => {
        setLoading(true);
        try {
            let resData, resPag;

            if (hasScreenerFilters()) {
                const body = {
                    filters: buildScreenerFilters(),
                    industries: industries.length > 0 ? industries : undefined,
                    valuation_status: status || undefined,
                    search: search || undefined,
                    sort_by: sortBy,
                    sort_dir: sortDir,
                    page,
                    per_page: 20,
                };
                const res = await axios.post('/api/screener', body);
                resData = res.data.data;
                resPag = res.data.pagination;
            } else {
                const res = await axios.get('/api/valuations', {
                    params: { page, per_page: 20, sort_by: sortBy, sort_dir: sortDir, search: search || undefined }
                });
                resData = res.data.data;
                resPag = res.data.pagination;
            }

            setData(resData);
            setPag(resPag);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [page, sortBy, sortDir, search, industries, status,
        priceMin, priceMax, diffMin, diffMax, peMin, peMax, epsGrowthMin, epsGrowthMax]);

    useEffect(() => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            fetchValuations();
        }, 300);
        return () => clearTimeout(debounceRef.current);
    }, [fetchValuations]);

    // Fetch live prices for all stocks on the current page
    useEffect(() => {
        if (data.length > 0) {
            const fetchLivePrices = async () => {
                const prices = {};
                await Promise.all(
                    data.map(async (stock) => {
                        try {
                            const res = await axios.get(`/api/stocks/${stock.ticker}/price`);
                            prices[stock.ticker] = res.data;
                        } catch (err) {
                            // Keep null if fetch fails
                            prices[stock.ticker] = null;
                        }
                    })
                );
                setLivePrices(prices);
            };
            fetchLivePrices();
        }
    }, [data]);

    const prevFiltersRef = useRef('');
    useEffect(() => {
        const filterKey = JSON.stringify({
            search, industries, status,
            priceMin, priceMax, diffMin, diffMax, peMin, peMax, epsGrowthMin, epsGrowthMax
        });
        if (prevFiltersRef.current && prevFiltersRef.current !== filterKey) {
            setPage(1);
        }
        prevFiltersRef.current = filterKey;
    }, [search, industries, status, priceMin, priceMax, diffMin, diffMax, peMin, peMax, epsGrowthMin, epsGrowthMax]);

    const handleSort = (field) => {
        if (sortBy === field) {
            setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(field);
            setSortDir('asc');
        }
    };

    const filterCount = activeFilterCount();

    return (
        <div className="container py-10 animate-fade-in">
            {/* Page header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 mb-8">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight mb-1">Valuations</h2>
                    <p className="text-[15px] text-[var(--text-muted)]">
                        {pag.total || '...'} stocks across {pag.total_pages || '...'} pages
                    </p>
                </div>

                {/* Data freshness widget */}
                {schedStatus && (
                    <div className="flex items-center gap-3 px-4 py-2.5 rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)] shrink-0">
                        <div className="flex items-center gap-2">
                            <Clock size={13} className="text-[var(--text-muted)]" />
                            <div className="text-xs">
                                {schedStatus.status === 'running' ? (
                                    <span className="text-[var(--accent-yellow)]">
                                        Refreshing… {schedStatus.done}/{schedStatus.total}
                                        {schedStatus.current && <span className="opacity-60 ml-1">({schedStatus.current})</span>}
                                    </span>
                                ) : schedStatus.last_run ? (
                                    <span className="text-[var(--text-muted)]">
                                        Updated {daysSince(schedStatus.last_run)}
                                        {schedStatus.next_run && (
                                            <span className="ml-1 opacity-60">· next {formatNextRun(schedStatus.next_run)}</span>
                                        )}
                                    </span>
                                ) : (
                                    <span className="text-[var(--text-muted)]">Never refreshed</span>
                                )}
                            </div>
                        </div>
                        <button
                            onClick={triggerRefresh}
                            disabled={schedRunning}
                            title="Refresh all data now"
                            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-[rgba(139,92,246,0.12)] text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.2)] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                        >
                            <RefreshCw size={11} className={schedRunning ? 'animate-spin' : ''} />
                            {schedRunning ? 'Running…' : 'Refresh Now'}
                        </button>
                    </div>
                )}
            </div>

            {/* Screener toolbar */}
            <div className="screener-toolbar">
                {/* Search bar - always visible */}
                <div className="screener-search-wrap">
                    <Search size={18} className="text-[var(--text-muted)]" />
                    <input
                        type="text"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder="Search by ticker or company name..."
                        className="screener-search"
                    />
                    {search && (
                        <button onClick={() => setSearch('')} className="screener-search-clear">
                            <X size={16} />
                        </button>
                    )}
                </div>

                <div className="flex items-center gap-3">
                    {/* Filter toggle */}
                    <button
                        onClick={() => setFiltersOpen(!filtersOpen)}
                        className={`screener-filter-btn ${filtersOpen || filterCount > 0 ? 'active' : ''}`}
                    >
                        <SlidersHorizontal size={17} />
                        <span>Screener</span>
                        {filterCount > 0 && (
                            <span className="screener-filter-badge">{filterCount}</span>
                        )}
                    </button>

                    {/* Reset */}
                    {filterCount > 0 && (
                        <button onClick={clearAllFilters} className="screener-reset-btn">
                            <RotateCcw size={15} />
                            <span>Reset</span>
                        </button>
                    )}
                </div>
            </div>

            {/* Expanded screener panel */}
            {filtersOpen && (
                <div className="screener-panel animate-fade-in">
                    {/* Row 1: Status + Industry */}
                    <div className="screener-row">
                        <div className="screener-field" style={{ flex: '0 0 280px' }}>
                            <label className="screener-label">Valuation Status</label>
                            <div className="screener-toggle-group">
                                {[
                                    { label: 'All', value: null },
                                    { label: 'Undervalued', value: 'undervalued', color: 'var(--accent-success)' },
                                    { label: 'Overvalued', value: 'overvalued', color: 'var(--accent-danger)' },
                                ].map(opt => (
                                    <button
                                        key={opt.label}
                                        onClick={() => setStatus(opt.value)}
                                        className={`screener-toggle-item ${status === opt.value ? 'active' : ''}`}
                                        style={status === opt.value && opt.color ? {
                                            borderColor: opt.color,
                                            color: opt.color,
                                            background: `color-mix(in srgb, ${opt.color} 8%, transparent)`,
                                        } : undefined}
                                    >
                                        {opt.color && (
                                            <span
                                                className="inline-block w-2 h-2 rounded-full mr-1.5 shrink-0"
                                                style={{ background: opt.color }}
                                            />
                                        )}
                                        {opt.label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="screener-field" style={{ flex: '1 1 320px' }}>
                            <label className="screener-label">Industry</label>
                            <MultiSelect
                                options={industryList}
                                selected={industries}
                                onChange={setIndustries}
                                placeholder="All industries"
                            />
                        </div>
                    </div>

                    {/* Row 2: Numeric ranges */}
                    <div className="screener-divider" />
                    <div className="screener-row">
                        <RangeInput label="Current Price" prefix="$" min={priceMin} max={priceMax} setMin={setPriceMin} setMax={setPriceMax} />
                        <RangeInput label="Valuation Diff" suffix="%" min={diffMin} max={diffMax} setMin={setDiffMin} setMax={setDiffMax} />
                        <RangeInput label="PE Median 5Y" min={peMin} max={peMax} setMin={setPeMin} setMax={setPeMax} />
                        <RangeInput label="EPS Growth 5Y" suffix="%" min={epsGrowthMin} max={epsGrowthMax} setMin={setEpsGrowthMin} setMax={setEpsGrowthMax} />
                    </div>
                </div>
            )}

            {/* Active filter tags */}
            {filterCount > 0 && !filtersOpen && (
                <div className="flex flex-wrap gap-2 mb-4 animate-fade-in">
                    {search && <FilterTag label={`Search: "${search}"`} onRemove={() => setSearch('')} />}
                    {industries.length > 0 && <FilterTag label={`${industries.length} ${industries.length === 1 ? 'Industry' : 'Industries'}`} onRemove={() => setIndustries([])} />}
                    {status && <FilterTag label={status === 'undervalued' ? 'Undervalued' : 'Overvalued'} onRemove={() => setStatus(null)} />}
                    {(priceMin || priceMax) && <FilterTag label={`Price: ${priceMin || '...'}–${priceMax || '...'}`} onRemove={() => { setPriceMin(''); setPriceMax(''); }} />}
                    {(diffMin || diffMax) && <FilterTag label={`Diff: ${diffMin || '...'}–${diffMax || '...'}%`} onRemove={() => { setDiffMin(''); setDiffMax(''); }} />}
                    {(peMin || peMax) && <FilterTag label={`PE: ${peMin || '...'}–${peMax || '...'}`} onRemove={() => { setPeMin(''); setPeMax(''); }} />}
                    {(epsGrowthMin || epsGrowthMax) && <FilterTag label={`EPS Gr: ${epsGrowthMin || '...'}–${epsGrowthMax || '...'}%`} onRemove={() => { setEpsGrowthMin(''); setEpsGrowthMax(''); }} />}
                </div>
            )}

            {/* Data table */}
            <div className="card overflow-hidden !p-0 !hover:transform-none" style={{ transform: 'none' }}>
                <div className="overflow-x-auto">
                    <table className="w-full">
                        <thead>
                            <tr className="border-b border-[var(--glass-border)] bg-[rgba(255,255,255,0.02)]">
                                <Th label="Ticker" field="ticker" onSort={handleSort} active={sortBy} dir={sortDir} />
                                <Th label="Company" field="company_name" onSort={handleSort} active={sortBy} dir={sortDir} />
                                <Th label="Industry" field="industry" onSort={handleSort} active={sortBy} dir={sortDir} />
                                <Th label="Price" field="current_price" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Fair Value" field="pe_median_price" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Diff %" field="valuation_diff_pct" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Status" field="valuation_status" onSort={() => {}} align="center" />
                            </tr>
                        </thead>
                        <tbody>
                            {loading ? (
                                <tr>
                                    <td colSpan="7" className="py-20 text-center text-[var(--text-muted)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
                                            <span className="text-sm">Loading valuations...</span>
                                        </div>
                                    </td>
                                </tr>
                            ) : data.length === 0 ? (
                                <tr>
                                    <td colSpan="7" className="py-20 text-center text-[var(--text-muted)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <SlidersHorizontal size={28} className="opacity-30" />
                                            <span className="text-base">No results match your filters</span>
                                            {hasActiveFilters() && (
                                                <button
                                                    onClick={clearAllFilters}
                                                    className="text-sm text-[var(--accent-purple)] hover:underline underline-offset-2 mt-1"
                                                >
                                                    Clear all filters
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ) : (
                                data.map((v) => {
                                    const livePrice = livePrices[v.ticker]?.price;
                                    const currentPrice = livePrice ?? v.current_price;
                                    const fairValue = v.pe_median_price;

                                    // Recalculate diff: (fairValue - currentPrice) / fairValue * 100
                                    // Positive = undervalued, Negative = overvalued
                                    const liveDiff = fairValue && currentPrice
                                        ? ((fairValue - currentPrice) / fairValue * 100).toFixed(1)
                                        : v.valuation_diff_pct;
                                    const isUndervalued = parseFloat(liveDiff) >= 0;

                                    return (
                                    <tr
                                        key={v.ticker}
                                        className="border-b border-[var(--glass-border)] hover:bg-[rgba(139,92,246,0.03)] transition-colors duration-150"
                                    >
                                        <td className="px-6 py-4">
                                            <Link
                                                href={`/stock/${v.ticker}`}
                                                className="font-mono text-[15px] font-bold text-[var(--accent-cyan)] hover:underline underline-offset-2"
                                            >
                                                {v.ticker}
                                            </Link>
                                            {earningsDates[v.ticker] && (
                                                <div className="flex items-center gap-1 mt-0.5">
                                                    <CalendarDays size={9} className="text-[var(--text-muted)]" />
                                                    <span className="text-[10px] text-[var(--text-muted)]">
                                                        {new Date(earningsDates[v.ticker] + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                                    </span>
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 text-[var(--text-secondary)] text-[14px] truncate max-w-[240px]">
                                            {v.company_name}
                                        </td>
                                        <td className="px-6 py-4 text-[var(--text-muted)] text-[13px]">
                                            {v.industry}
                                        </td>
                                        <td className="px-6 py-4 text-right font-mono text-[15px] tabular-nums">
                                            <div className="flex flex-col items-end">
                                                <span>${currentPrice?.toFixed(2)}</span>
                                                {livePrice && livePrices[v.ticker]?.change !== null && (
                                                    <span className={`text-[11px] ${
                                                        livePrices[v.ticker]?.change >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                                    }`}>
                                                        {livePrices[v.ticker]?.change >= 0 ? '+' : ''}{livePrices[v.ticker]?.change_percent?.toFixed(1)}%
                                                    </span>
                                                )}
                                            </div>
                                        </td>
                                        <td className="px-6 py-4 text-right font-mono text-[15px] tabular-nums text-[var(--text-secondary)]">
                                            ${fairValue?.toFixed(2)}
                                        </td>
                                        <td className={`px-6 py-4 text-right font-mono text-[15px] font-semibold tabular-nums ${
                                            isUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                        }`}>
                                            {isUndervalued ? '+' : ''}{liveDiff}%
                                        </td>
                                        <td className="px-6 py-4 text-center">
                                            <StatusBadge status={isUndervalued ? 'undervalued' : 'overvalued'} />
                                        </td>
                                    </tr>
                                );
                                })
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Pagination */}
                <div className="px-6 py-5 flex items-center justify-between border-t border-[var(--glass-border)] bg-[rgba(255,255,255,0.01)]">
                    <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="btn btn-ghost disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2 text-[14px]"
                    >
                        <ChevronLeft size={16} /> Prev
                    </button>
                    <div className="flex items-center gap-1.5">
                        {Array.from({ length: Math.min(pag.total_pages || 0, 7) }, (_, i) => {
                            const pageNum = i + 1;
                            return (
                                <button
                                    key={pageNum}
                                    onClick={() => setPage(pageNum)}
                                    className={`w-9 h-9 rounded-lg text-[13px] font-medium transition-colors ${
                                        page === pageNum
                                            ? 'bg-[var(--accent-purple)] text-white'
                                            : 'text-[var(--text-muted)] hover:bg-[var(--glass-bg)] hover:text-[var(--text-primary)]'
                                    }`}
                                >
                                    {pageNum}
                                </button>
                            );
                        })}
                        {(pag.total_pages || 0) > 7 && (
                            <span className="text-[var(--text-muted)] text-sm px-1">...</span>
                        )}
                    </div>
                    <button
                        onClick={() => setPage(p => Math.min(pag.total_pages, p + 1))}
                        disabled={page === pag.total_pages}
                        className="btn btn-ghost disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2 text-[14px]"
                    >
                        Next <ChevronRight size={16} />
                    </button>
                </div>
            </div>
        </div>
    );
}

/* ─── Date helpers ─── */
function daysSince(iso) {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    if (diff === 0) return 'today';
    if (diff === 1) return '1 day ago';
    return `${diff} days ago`;
}

function formatNextRun(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/* ─── Filter Tag (shown when panel is collapsed) ─── */
function FilterTag({ label, onRemove }) {
    return (
        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium bg-[rgba(139,92,246,0.1)] text-[var(--accent-purple)] border border-[rgba(139,92,246,0.2)]">
            {label}
            <button onClick={onRemove} className="hover:text-white transition-colors">
                <X size={13} />
            </button>
        </span>
    );
}

/* ─── Range Input ─── */
function RangeInput({ label, prefix, suffix, min, max, setMin, setMax }) {
    return (
        <div className="screener-field">
            <label className="screener-label">{label}</label>
            <div className="flex items-center gap-2">
                <div className="screener-input-wrap">
                    {prefix && <span className="screener-input-affix">{prefix}</span>}
                    <input
                        type="number"
                        value={min}
                        onChange={e => setMin(e.target.value)}
                        placeholder="Min"
                        className="screener-input"
                    />
                    {suffix && <span className="screener-input-affix">{suffix}</span>}
                </div>
                <span className="text-[var(--text-muted)] text-sm font-medium shrink-0">&ndash;</span>
                <div className="screener-input-wrap">
                    {prefix && <span className="screener-input-affix">{prefix}</span>}
                    <input
                        type="number"
                        value={max}
                        onChange={e => setMax(e.target.value)}
                        placeholder="Max"
                        className="screener-input"
                    />
                    {suffix && <span className="screener-input-affix">{suffix}</span>}
                </div>
            </div>
        </div>
    );
}

/* ─── Multi-Select Dropdown ─── */
function MultiSelect({ options, selected, onChange, placeholder }) {
    const [open, setOpen] = useState(false);
    const [filterText, setFilterText] = useState('');
    const ref = useRef(null);
    const inputRef = useRef(null);

    useEffect(() => {
        const handleClick = (e) => {
            if (ref.current && !ref.current.contains(e.target)) {
                setOpen(false);
                setFilterText('');
            }
        };
        document.addEventListener('mousedown', handleClick);
        return () => document.removeEventListener('mousedown', handleClick);
    }, []);

    useEffect(() => {
        if (open && inputRef.current) inputRef.current.focus();
    }, [open]);

    const toggle = (val) => {
        onChange(
            selected.includes(val)
                ? selected.filter(s => s !== val)
                : [...selected, val]
        );
    };

    const filtered = filterText
        ? options.filter(o => o.toLowerCase().includes(filterText.toLowerCase()))
        : options;

    return (
        <div className="relative" ref={ref}>
            <button
                type="button"
                onClick={() => setOpen(!open)}
                className="screener-select-trigger"
            >
                <span className={selected.length ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'}>
                    {selected.length
                        ? `${selected.length} ${selected.length === 1 ? 'industry' : 'industries'} selected`
                        : placeholder}
                </span>
                <ChevronDown
                    size={16}
                    className={`transition-transform duration-200 shrink-0 text-[var(--text-muted)] ${open ? 'rotate-180' : ''}`}
                />
            </button>
            {open && (
                <div className="screener-dropdown">
                    {/* Search within dropdown */}
                    <div className="px-3 pt-3 pb-2">
                        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--bg-primary)] border border-[var(--glass-border)]">
                            <Search size={14} className="text-[var(--text-muted)] shrink-0" />
                            <input
                                ref={inputRef}
                                type="text"
                                value={filterText}
                                onChange={e => setFilterText(e.target.value)}
                                placeholder="Filter industries..."
                                className="bg-transparent border-none outline-none text-[14px] text-[var(--text-primary)] w-full placeholder:text-[var(--text-muted)]"
                            />
                        </div>
                    </div>
                    {/* Options */}
                    <div className="max-h-60 overflow-y-auto px-1.5 pb-2">
                        {filtered.length === 0 ? (
                            <div className="px-3 py-4 text-sm text-[var(--text-muted)] text-center">No matches</div>
                        ) : (
                            filtered.map(opt => (
                                <button
                                    key={opt}
                                    type="button"
                                    onClick={() => toggle(opt)}
                                    className="screener-dropdown-item"
                                >
                                    <span className={`screener-checkbox ${selected.includes(opt) ? 'checked' : ''}`}>
                                        {selected.includes(opt) && <Check size={12} className="text-white" />}
                                    </span>
                                    <span className="truncate">{opt}</span>
                                </button>
                            ))
                        )}
                    </div>
                    {/* Footer with count */}
                    {selected.length > 0 && (
                        <div className="px-3 py-2.5 border-t border-[var(--glass-border)] flex items-center justify-between">
                            <span className="text-[13px] text-[var(--text-muted)]">
                                {selected.length} selected
                            </span>
                            <button
                                onClick={() => { onChange([]); setFilterText(''); }}
                                className="text-[13px] text-[var(--accent-purple)] hover:text-[var(--text-primary)] transition-colors"
                            >
                                Clear
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

/* ─── Table Header ─── */
function Th({ label, field, onSort, active, dir, align = 'left' }) {
    const isActive = active === field;
    return (
        <th
            onClick={() => onSort(field)}
            className={`px-6 py-4 font-semibold text-[12px] uppercase tracking-wider cursor-pointer select-none transition-colors hover:text-[var(--text-primary)] whitespace-nowrap ${
                isActive ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'
            }`}
        >
            <div className={`flex items-center gap-1.5 ${
                align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : 'justify-start'
            }`}>
                {label}
                <span className="opacity-60">
                    {isActive ? (
                        dir === 'asc' ? <ArrowUp size={13} /> : <ArrowDown size={13} />
                    ) : (
                        <ChevronsUpDown size={13} />
                    )}
                </span>
            </div>
        </th>
    );
}

/* ─── Status Badge ─── */
function StatusBadge({ status }) {
    if (!status) return <span className="text-[var(--text-muted)]">-</span>;
    const cls = status === 'undervalued' ? 'badge-success' : 'badge-danger';
    return <span className={`badge ${cls}`}>{status.toUpperCase()}</span>;
}
