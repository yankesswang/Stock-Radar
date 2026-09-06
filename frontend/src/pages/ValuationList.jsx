import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import axios from 'axios';
import { Link } from 'wouter';
import {
    ArrowUp, ArrowDown, ChevronsUpDown,
    SlidersHorizontal, X, ChevronDown, Check, Search, RotateCcw, CalendarDays,
    RefreshCw, Clock, Plus, Loader2, CheckCircle2, XCircle, ChevronRight as ChevronRightIcon,
    Pencil, Tag, Layers,
} from 'lucide-react';

// every row is loaded in one shot — the table is grouped by section, not paged
const PER_PAGE = 500;
// tickers per batch-quote request; several smaller calls fill the table
// progressively instead of blocking on one long one
const PRICE_CHUNK = 80;

export default function ValuationList() {
    const [data, setData] = useState([]);
    const [pag, setPag] = useState({});
    const [loading, setLoading] = useState(true);
    const [sortBy, setSortBy] = useState('industry');
    const [sortDir, setSortDir] = useState('asc');
    const [livePrices, setLivePrices] = useState({});
    const [collapsed, setCollapsed] = useState(() => new Set());

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

    // Inline add-ticker state
    const [addOpen, setAddOpen] = useState(false);

    // Selected-category rename state
    const [editingName, setEditingName] = useState(false);
    const [nameInput, setNameInput] = useState('');
    const [renaming, setRenaming] = useState(false);
    const [renameError, setRenameError] = useState('');

    const selectedCategory = industries.length === 1 ? industries[0] : null;

    const startRename = () => {
        setNameInput(selectedCategory || '');
        setRenameError('');
        setEditingName(true);
    };

    const submitRename = async () => {
        const next = nameInput.trim();
        if (!selectedCategory) return;
        if (!next || next === selectedCategory) { setEditingName(false); return; }
        setRenaming(true);
        setRenameError('');
        try {
            await axios.patch('/api/industries/rename', { old_name: selectedCategory, new_name: next });
            // Refresh industry list, point the filter at the renamed category, reload
            const res = await axios.get('/api/industries');
            setIndustryList(res.data.map(i => i.name).sort());
            setIndustries([next]);
            setEditingName(false);
            fetchValuations();
        } catch (err) {
            setRenameError(err.response?.data?.detail || 'Rename failed.');
        } finally {
            setRenaming(false);
        }
    };

    useEffect(() => {
        axios.get('/api/industries')
            .then(res => setIndustryList(res.data.map(i => i.name).sort()))
            .catch(err => console.error('Failed to fetch industries:', err));
    }, []);

    useEffect(() => {
        axios.get('/api/earnings/calendar')
            .then(res => {
                const map = {};
                for (const ev of (res.data.events || [])) {
                    if (ev.status === 'reported') continue;   // 已公布的不是「下次財報日」
                    map[ev.ticker] = ev.date;
                }
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
                    page: 1,
                    per_page: PER_PAGE,
                };
                const res = await axios.post('/api/screener', body);
                resData = res.data.data;
                resPag = res.data.pagination;
            } else {
                const res = await axios.get('/api/valuations', {
                    params: { page: 1, per_page: PER_PAGE, sort_by: sortBy, sort_dir: sortDir, search: search || undefined }
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
    }, [sortBy, sortDir, search, industries, status,
        priceMin, priceMax, diffMin, diffMax, peMin, peMax, epsGrowthMin, epsGrowthMax]);

    useEffect(() => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            fetchValuations();
        }, 300);
        return () => clearTimeout(debounceRef.current);
    }, [fetchValuations]);

    // Batch-fetch live prices for every loaded row. Chunked so the table fills in
    // as the responses land rather than waiting on the whole watchlist.
    const [pricesLoading, setPricesLoading] = useState(false);
    useEffect(() => {
        if (!data.length) return;
        let alive = true;
        const tickers = data.map(s => s.ticker);
        const chunks = [];
        for (let i = 0; i < tickers.length; i += PRICE_CHUNK) {
            chunks.push(tickers.slice(i, i + PRICE_CHUNK));
        }
        setPricesLoading(true);
        (async () => {
            // Sequential on purpose: concurrent bulk downloads get throttled by
            // Yahoo and come back with holes, which would skew the section averages.
            for (const chunk of chunks) {
                if (!alive) return;
                try {
                    const res = await axios.get('/api/stocks/prices', { params: { tickers: chunk.join(',') } });
                    if (!alive) return;
                    setLivePrices(prev => ({ ...prev, ...(res.data.prices || {}) }));
                } catch {
                    /* leave those rows on their stored price */
                }
            }
            if (alive) setPricesLoading(false);
        })();
        return () => { alive = false; };
    }, [data]);

    const handleSort = (field) => {
        if (sortBy === field) {
            setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(field);
            setSortDir('asc');
        }
    };

    const filterCount = activeFilterCount();

    // ── Group rows into sections ────────────────────────────────────────────────
    // Rows keep whatever order the backend sorted them into; only the sections
    // themselves are ordered here (by name, flipped by the Section column header).
    const sections = useMemo(() => {
        const map = new Map();
        for (const row of data) {
            const name = row.industry || 'Uncategorized';
            if (!map.has(name)) map.set(name, []);
            map.get(name).push(row);
        }
        const list = [...map.entries()].map(([name, rows]) => {
            const moves = rows
                .map(r => livePrices[r.ticker]?.change_percent)
                .filter(v => v != null && !Number.isNaN(v));
            const avg = moves.length ? moves.reduce((s, v) => s + v, 0) / moves.length : null;
            return {
                name,
                rows,
                avgChange: avg,
                up: moves.filter(v => v > 0).length,
                down: moves.filter(v => v < 0).length,
                priced: moves.length,
            };
        });
        const dir = sortBy === 'industry' && sortDir === 'desc' ? -1 : 1;
        list.sort((a, b) => a.name.localeCompare(b.name) * dir);
        return list;
    }, [data, livePrices, sortBy, sortDir]);

    const allCollapsed = sections.length > 0 && sections.every(s => collapsed.has(s.name));
    const toggleSection = (name) => {
        setCollapsed(prev => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name); else next.add(name);
            return next;
        });
    };
    const toggleAll = () => {
        setCollapsed(allCollapsed ? new Set() : new Set(sections.map(s => s.name)));
    };

    return (
        <div className="container py-10 animate-fade-in">
            {/* Page header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 mb-8">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight mb-1">Valuations</h2>
                    <p className="text-[15px] text-[var(--text-muted)]">
                        {pag.total ?? '...'} stocks across {sections.length || '...'} sections
                        {pricesLoading && (
                            <span className="ml-2 inline-flex items-center gap-1 text-[13px]">
                                <Loader2 size={11} className="animate-spin" /> loading quotes…
                            </span>
                        )}
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
                    {/* Add ticker (prefills group with the selected industry) */}
                    <button
                        onClick={() => setAddOpen(true)}
                        className="screener-filter-btn"
                        title={industries.length === 1 ? `Add a ticker to ${industries[0]}` : 'Add a ticker'}
                    >
                        <Plus size={17} />
                        <span>Add Ticker</span>
                    </button>

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

            {/* Selected category banner (shown when exactly one industry is selected) */}
            {selectedCategory && (
                <div className="flex items-center gap-3 mb-4 px-4 py-3 rounded-xl border border-[var(--glass-border)] bg-[rgba(139,92,246,0.04)] animate-fade-in">
                    <Tag size={15} className="text-[var(--accent-purple)] shrink-0" />
                    {editingName ? (
                        <>
                            <input
                                type="text"
                                value={nameInput}
                                onChange={e => setNameInput(e.target.value)}
                                onKeyDown={e => {
                                    if (e.key === 'Enter') submitRename();
                                    if (e.key === 'Escape') setEditingName(false);
                                }}
                                maxLength={50}
                                disabled={renaming}
                                autoFocus
                                className="bg-[var(--bg-primary)] border border-[rgba(139,92,246,0.4)] rounded-lg px-3 py-1.5 text-[15px] font-semibold text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-purple)] disabled:opacity-50"
                            />
                            <button
                                onClick={submitRename}
                                disabled={renaming}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
                            >
                                {renaming ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                                Save
                            </button>
                            <button
                                onClick={() => setEditingName(false)}
                                disabled={renaming}
                                className="px-3 py-1.5 rounded-lg text-[13px] font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors disabled:opacity-50"
                            >
                                Cancel
                            </button>
                            {renameError && (
                                <span className="text-[13px] text-red-400 flex items-center gap-1">
                                    <XCircle size={13} /> {renameError}
                                </span>
                            )}
                        </>
                    ) : (
                        <>
                            <span className="text-[12px] uppercase tracking-wider text-[var(--text-muted)]">Category</span>
                            <span className="text-[15px] font-semibold text-[var(--text-primary)]">{selectedCategory}</span>
                            <button
                                onClick={startRename}
                                title="Rename this category"
                                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[13px] font-medium text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.12)] transition-colors"
                            >
                                <Pencil size={13} /> Rename
                            </button>
                            <span className="text-[13px] text-[var(--text-muted)] ml-auto">
                                {pag.total ?? '...'} stocks
                            </span>
                        </>
                    )}
                </div>
            )}

            {/* Section controls */}
            {!loading && sections.length > 0 && (
                <div className="flex flex-wrap items-center gap-3 mb-3">
                    <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
                        <Layers size={14} />
                        Grouped by section
                    </span>
                    <button
                        onClick={() => handleSort('industry')}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[13px] font-medium text-[var(--text-muted)] border border-[var(--glass-border)] hover:text-[var(--text-primary)] transition-colors"
                        title="Flip section order"
                    >
                        {sortBy === 'industry' && sortDir === 'desc' ? <ArrowDown size={12} /> : <ArrowUp size={12} />}
                        A–Z
                    </button>
                    <button
                        onClick={toggleAll}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[13px] font-medium text-[var(--text-muted)] border border-[var(--glass-border)] hover:text-[var(--text-primary)] transition-colors"
                    >
                        <ChevronsUpDown size={12} />
                        {allCollapsed ? 'Expand all' : 'Collapse all'}
                    </button>
                </div>
            )}

            {/* Data table */}
            <div className="card overflow-hidden !p-0 !hover:transform-none" style={{ transform: 'none' }}>
                <div className="overflow-x-auto">
                    <table className="w-full">
                        <thead>
                            <tr className="border-b border-[var(--glass-border)] bg-[rgba(255,255,255,0.02)]">
                                <Th label="Ticker" field="ticker" onSort={handleSort} active={sortBy} dir={sortDir} />
                                <Th label="Price" field="current_price" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Today" field="today" onSort={() => {}} align="right" />
                                <Th label="Fair Value" field="pe_median_price" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Diff %" field="valuation_diff_pct" onSort={handleSort} active={sortBy} dir={sortDir} align="right" />
                                <Th label="Status" field="valuation_status" onSort={() => {}} align="center" />
                            </tr>
                        </thead>
                        <tbody>
                            {loading ? (
                                <tr>
                                    <td colSpan="6" className="py-20 text-center text-[var(--text-muted)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
                                            <span className="text-sm">Loading valuations...</span>
                                        </div>
                                    </td>
                                </tr>
                            ) : data.length === 0 ? (
                                <tr>
                                    <td colSpan="6" className="py-20 text-center text-[var(--text-muted)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <SlidersHorizontal size={28} className="opacity-30" />
                                            <span className="text-base">No results match your filters</span>
                                            <div className="flex items-center gap-4 mt-1">
                                                <button
                                                    onClick={() => setAddOpen(true)}
                                                    className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent-purple)] hover:underline underline-offset-2"
                                                >
                                                    <Plus size={15} />
                                                    {industries.length === 1
                                                        ? `Add a ticker to ${industries[0]}`
                                                        : 'Add a ticker'}
                                                </button>
                                                {hasActiveFilters() && (
                                                    <button
                                                        onClick={clearAllFilters}
                                                        className="text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:underline underline-offset-2"
                                                    >
                                                        Clear all filters
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                    </td>
                                </tr>
                            ) : null}
                        </tbody>

                        {/* One tbody per section: header row + its holdings */}
                        {!loading && sections.map(section => {
                            const open = !collapsed.has(section.name);
                            return (
                                <tbody key={section.name}>
                                    <SectionHeaderRow
                                        section={section}
                                        open={open}
                                        onToggle={() => toggleSection(section.name)}
                                    />
                                    {open && section.rows.map(v => (
                                        <ValuationRow
                                            key={v.ticker}
                                            v={v}
                                            live={livePrices[v.ticker]}
                                            earningsDate={earningsDates[v.ticker]}
                                        />
                                    ))}
                                </tbody>
                            );
                        })}

                        {/* Inline add-ticker row */}
                        {!loading && (
                            <tbody>
                                <tr
                                    onClick={() => setAddOpen(true)}
                                    className="cursor-pointer hover:bg-[rgba(139,92,246,0.05)] transition-colors duration-150 group"
                                >
                                    <td colSpan="6" className="px-6 py-4">
                                        <span className="inline-flex items-center gap-2 text-[14px] font-medium text-[var(--text-muted)] group-hover:text-[var(--accent-purple)] transition-colors">
                                            <span className="flex items-center justify-center w-5 h-5 rounded-full border border-dashed border-current">
                                                <Plus size={13} />
                                            </span>
                                            {industries.length === 1
                                                ? `Add a ticker to ${industries[0]}`
                                                : 'Add a ticker'}
                                        </span>
                                    </td>
                                </tr>
                            </tbody>
                        )}
                    </table>
                </div>

                {/* Footer summary */}
                {!loading && data.length > 0 && (
                    <div className="px-6 py-4 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--glass-border)] bg-[rgba(255,255,255,0.01)] text-[13px] text-[var(--text-muted)]">
                        <span>
                            {data.length} holdings in {sections.length} sections
                            {pag.total > data.length && <span className="ml-1 opacity-70">(of {pag.total})</span>}
                        </span>
                        <span className="opacity-70">Today's move via batch quotes · cached 2 min</span>
                    </div>
                )}
            </div>

            {/* Inline add-ticker modal */}
            {addOpen && (
                <AddTickerModal
                    industryList={industryList}
                    defaultGroup={industries.length === 1 ? industries[0] : ''}
                    onClose={() => setAddOpen(false)}
                    onAdded={fetchValuations}
                />
            )}
        </div>
    );
}

/* ─── Section header row (collapsible, with today's price move) ─── */
function SectionHeaderRow({ section, open, onToggle }) {
    const { name, rows, avgChange, up, down, priced } = section;
    const pos = (avgChange ?? 0) >= 0;
    const moveColor = avgChange == null
        ? 'var(--text-muted)'
        : pos ? 'var(--accent-success)' : 'var(--accent-danger)';

    return (
        <tr
            onClick={onToggle}
            className="cursor-pointer select-none border-y border-[var(--glass-border)] bg-[rgba(139,92,246,0.05)] hover:bg-[rgba(139,92,246,0.09)] transition-colors"
        >
            <td colSpan="6" className="px-4 py-2.5">
                <div className="flex flex-wrap items-center gap-2.5">
                    <ChevronRightIcon
                        size={14}
                        className={`text-[var(--text-muted)] transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
                    />
                    <span className="text-[14px] font-semibold text-[var(--text-primary)]">{name}</span>
                    <span className="px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-[rgba(139,92,246,0.12)] text-[var(--accent-purple)]">
                        {rows.length}
                    </span>

                    <div className="ml-auto flex items-center gap-3">
                        {priced > 0 && (
                            <span className="text-[12px] text-[var(--text-muted)] tabular-nums">
                                <span className="text-[var(--accent-success)] font-semibold">{up}</span> 漲
                                <span className="mx-1 opacity-40">·</span>
                                <span className="text-[var(--accent-danger)] font-semibold">{down}</span> 跌
                                {priced < rows.length && (
                                    <span className="ml-1 opacity-60">({priced}/{rows.length} 有報價)</span>
                                )}
                            </span>
                        )}
                        <span
                            className="min-w-[72px] text-right font-mono text-[14px] font-bold tabular-nums"
                            style={{ color: moveColor }}
                            title="Section average of today's move (equal-weighted)"
                        >
                            {avgChange == null ? '—' : `${pos ? '+' : ''}${avgChange.toFixed(2)}%`}
                        </span>
                    </div>
                </div>
            </td>
        </tr>
    );
}

/* ─── One holding row ─── */
function ValuationRow({ v, live, earningsDate }) {
    const livePrice = live?.price;
    const currentPrice = livePrice ?? v.current_price;
    const fairValue = v.pe_median_price;
    const change = live?.change_percent;

    // Recalculate diff: (fairValue - currentPrice) / fairValue * 100
    // Positive = undervalued, Negative = overvalued
    const hasFair = fairValue != null && fairValue > 0;
    const liveDiff = !hasFair ? null
        : currentPrice
            ? ((fairValue - currentPrice) / fairValue * 100).toFixed(1)
            : (v.valuation_diff_pct ?? null);
    const hasDiff = liveDiff != null && liveDiff !== '' && !Number.isNaN(parseFloat(liveDiff));
    const isUndervalued = hasDiff && parseFloat(liveDiff) >= 0;

    return (
        <tr className="border-b border-[var(--glass-border)] hover:bg-[rgba(139,92,246,0.03)] transition-colors duration-150">
            <td className="px-4 py-2.5 pl-10">
                <div className="flex items-baseline gap-2">
                    <Link
                        href={`/stock/${v.ticker}`}
                        className="font-mono text-[14px] font-bold text-[var(--accent-cyan)] hover:underline underline-offset-2"
                    >
                        {v.ticker}
                    </Link>
                    {earningsDate && (
                        <span className="inline-flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
                            <CalendarDays size={9} />
                            {new Date(earningsDate + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                        </span>
                    )}
                </div>
                {v.company_name && (
                    <div className="text-[12px] text-[var(--text-muted)] truncate max-w-[220px]">
                        {v.company_name}
                    </div>
                )}
            </td>
            <td className="px-4 py-2.5 text-right font-mono text-[14px] tabular-nums">
                {currentPrice != null ? `$${currentPrice.toFixed(2)}` : <span className="text-[var(--text-muted)]">—</span>}
            </td>
            <td className={`px-4 py-2.5 text-right font-mono text-[13px] font-semibold tabular-nums ${
                change == null ? 'text-[var(--text-muted)]'
                : change >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
            }`}>
                {change == null ? '—' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
            </td>
            <td className="px-4 py-2.5 text-right font-mono text-[14px] tabular-nums text-[var(--text-secondary)]">
                {hasFair ? `$${fairValue.toFixed(2)}` : <span className="text-[var(--text-muted)]">—</span>}
            </td>
            <td className={`px-4 py-2.5 text-right font-mono text-[14px] font-semibold tabular-nums ${
                !hasDiff ? 'text-[var(--text-muted)]'
                : isUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
            }`}>
                {hasDiff ? `${isUndervalued ? '+' : ''}${liveDiff}%` : '—'}
            </td>
            <td className="px-4 py-2.5 text-center">
                {hasDiff
                    ? <StatusBadge status={isUndervalued ? 'undervalued' : 'overvalued'} />
                    : <span className="text-[12px] text-[var(--text-muted)]">N/A</span>}
            </td>
        </tr>
    );
}

/* ─── Inline Add-Ticker Modal ─── */
function AddTickerModal({ industryList, defaultGroup, onClose, onAdded }) {
    const [ticker, setTicker] = useState('');
    const [group, setGroup] = useState(defaultGroup || '');
    const [submitting, setSubmitting] = useState(false);
    const [jobId, setJobId] = useState(null);
    const [job, setJob] = useState(null);
    const [error, setError] = useState('');
    const pollRef = useRef(null);
    const refreshedRef = useRef(false);

    const isDone = job?.status === 'completed';
    const isFailed = job?.status === 'failed';
    const isRunning = job?.status === 'running';

    // Poll job status while running
    useEffect(() => {
        if (!jobId) return;
        pollRef.current = setInterval(async () => {
            try {
                const res = await axios.get(`/api/scrape/${jobId}`);
                setJob(res.data);
                if (res.data.status !== 'running') {
                    clearInterval(pollRef.current);
                    if (res.data.status === 'completed' && !refreshedRef.current) {
                        refreshedRef.current = true;
                        onAdded?.();
                    }
                }
            } catch {
                clearInterval(pollRef.current);
            }
        }, 2000);
        return () => clearInterval(pollRef.current);
    }, [jobId, onAdded]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setJob(null);
        setJobId(null);
        refreshedRef.current = false;

        const t = ticker.trim().toUpperCase();
        const g = group.trim();
        if (!t) { setError('Ticker is required.'); return; }
        if (!g) { setError('Please enter a group.'); return; }

        setSubmitting(true);
        try {
            const res = await axios.post('/api/scrape', { ticker: t, group: g });
            setJobId(res.data.job_id);
            setJob(res.data);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to start scrape job.');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-start justify-center pt-24 px-4 bg-black/60 backdrop-blur-sm animate-fade-in"
            onMouseDown={(e) => { if (e.target === e.currentTarget && !isRunning) onClose(); }}
        >
            <div className="w-full max-w-md bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-2xl p-6 shadow-2xl">
                {/* Header */}
                <div className="flex items-start justify-between mb-5">
                    <div>
                        <h3 className="text-lg font-bold text-[var(--text-primary)] flex items-center gap-2">
                            <Plus className="w-5 h-5 text-[var(--accent-purple)]" />
                            Add Ticker
                        </h3>
                        <p className="text-xs text-[var(--text-muted)] mt-1">
                            Scrapes the ticker's data and stores it under the chosen group.
                        </p>
                    </div>
                    <button
                        onClick={onClose}
                        disabled={isRunning}
                        className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors disabled:opacity-30"
                    >
                        <X size={18} />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                            Ticker Symbol
                        </label>
                        <input
                            type="text"
                            value={ticker}
                            onChange={e => setTicker(e.target.value.toUpperCase())}
                            placeholder="e.g. AAPL, NVDA, TSLA"
                            maxLength={10}
                            disabled={isRunning || submitting}
                            autoFocus
                            className="w-full bg-[var(--bg-primary)] border border-[var(--glass-border)] rounded-xl px-4 py-3 text-[var(--text-primary)] font-mono text-sm focus:outline-none focus:border-[rgba(139,92,246,0.5)] transition-colors disabled:opacity-50 placeholder-[var(--text-muted)]"
                            autoComplete="off"
                            spellCheck="false"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                            Industry Group
                        </label>
                        <input
                            list="valuation-industry-groups"
                            value={group}
                            onChange={e => setGroup(e.target.value)}
                            disabled={isRunning || submitting}
                            placeholder="e.g. 半導體, AI Infrastructure, Banks"
                            maxLength={50}
                            className="w-full bg-[var(--bg-primary)] border border-[var(--glass-border)] rounded-xl px-4 py-3 text-[var(--text-primary)] text-sm focus:outline-none focus:border-[rgba(139,92,246,0.5)] transition-colors disabled:opacity-50 placeholder-[var(--text-muted)]"
                            autoComplete="off"
                        />
                        <datalist id="valuation-industry-groups">
                            {industryList.map(g => (
                                <option key={g} value={g}>{g}</option>
                            ))}
                        </datalist>
                    </div>

                    {error && (
                        <p className="text-red-400 text-sm flex items-center gap-1.5">
                            <XCircle className="w-4 h-4 shrink-0" />
                            {error}
                        </p>
                    )}

                    {!isDone && (
                        <button
                            type="submit"
                            disabled={submitting || isRunning}
                            className="w-full py-3 px-4 bg-gradient-to-r from-[var(--accent-purple)] to-[var(--accent-cyan)] text-white text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                        >
                            {submitting || isRunning
                                ? <><Loader2 className="w-4 h-4 animate-spin" /> Scraping…</>
                                : <><Plus className="w-4 h-4" /> Start Scrape</>
                            }
                        </button>
                    )}
                </form>

                {/* Job status */}
                {job && (
                    <div className={`mt-5 rounded-xl border p-4 transition-colors ${
                        isDone ? 'border-emerald-500/30' :
                        isFailed ? 'border-red-500/30' :
                        'border-[var(--glass-border)]'
                    }`}>
                        <div className="flex items-center gap-2 mb-3">
                            {isDone && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
                            {isFailed && <XCircle className="w-4 h-4 text-red-400" />}
                            {isRunning && <Loader2 className="w-4 h-4 text-[var(--accent-purple)] animate-spin" />}
                            <span className={`font-semibold text-sm ${
                                isDone ? 'text-emerald-400' : isFailed ? 'text-red-400' : 'text-[var(--accent-purple)]'
                            }`}>
                                {isDone ? 'Completed' : isFailed ? 'Failed' : 'Running…'}
                            </span>
                        </div>
                        <ol className="space-y-1.5 max-h-48 overflow-y-auto">
                            {(job.steps || []).map((s, i) => (
                                <li key={i} className="flex items-start gap-2 text-[13px] text-[var(--text-secondary)]">
                                    <ChevronRightIcon className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[var(--text-muted)]" />
                                    <span className={i === job.steps.length - 1 && isRunning ? 'text-[var(--accent-cyan)]' : ''}>{s}</span>
                                </li>
                            ))}
                        </ol>
                        {isDone && (
                            <button
                                onClick={onClose}
                                className="mt-4 w-full py-2.5 px-4 bg-[rgba(139,92,246,0.12)] border border-[rgba(139,92,246,0.25)] text-[var(--accent-purple)] text-sm font-medium rounded-xl hover:bg-[rgba(139,92,246,0.2)] transition-colors"
                            >
                                Done — {job.ticker} added
                            </button>
                        )}
                    </div>
                )}
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
                <span className={`truncate ${selected.length ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'}`}>
                    {selected.length === 0
                        ? placeholder
                        : selected.length <= 2
                            ? selected.join(', ')
                            : `${selected.slice(0, 2).join(', ')} +${selected.length - 2} more`}
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
