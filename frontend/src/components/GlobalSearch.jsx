import { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'wouter';
import axios from 'axios';
import { Search, X, TrendingUp, Building2, Loader2 } from 'lucide-react';

// Simple debounce hook
function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export default function GlobalSearch() {
  const [, navigate] = useLocation();
  const [query, setQuery]         = useState('');
  const [results, setResults]     = useState([]);
  const [loading, setLoading]     = useState(false);
  const [open, setOpen]           = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);

  const inputRef    = useRef(null);
  const containerRef = useRef(null);
  const debouncedQ  = useDebounce(query.trim(), 200);

  // ── Fetch results ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!debouncedQ) { setResults([]); setLoading(false); return; }
    setLoading(true);
    axios.get('/api/search', { params: { q: debouncedQ, limit: 8 } })
      .then(res => { setResults(res.data); setActiveIdx(-1); })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [debouncedQ]);

  // ── Close on outside click ───────────────────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // ── Keyboard: Ctrl+K / Cmd+K to focus ──────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const goTo = useCallback((ticker) => {
    navigate(`/stock/${ticker}`);
    setQuery('');
    setResults([]);
    setOpen(false);
    inputRef.current?.blur();
  }, [navigate]);

  const handleKeyDown = (e) => {
    if (!open || !results.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const target = activeIdx >= 0 ? results[activeIdx] : results[0];
      if (target) goTo(target.ticker);
    } else if (e.key === 'Escape') {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  const showDropdown = open && (loading || results.length > 0 || (debouncedQ && !loading));

  return (
    <div ref={containerRef} className="relative w-full sm:w-72">
      {/* Input */}
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border transition-all duration-200 ${
        open
          ? 'border-[var(--accent-cyan)] bg-[var(--bg-secondary)] shadow-lg shadow-[rgba(0,82,255,0.12)]'
          : 'border-[var(--glass-border)] bg-[rgba(255,255,255,0.6)] hover:border-[rgba(91,97,110,0.35)]'
      }`}>
        {loading
          ? <Loader2 size={14} className="text-[var(--text-muted)] shrink-0 animate-spin" />
          : <Search size={14} className="text-[var(--text-muted)] shrink-0" />
        }
        <input
          ref={inputRef}
          type="text"
          value={query}
          aria-label="Global stock search"
          placeholder="Search ticker or company…"
          className="flex-1 bg-transparent text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none min-w-0"
          onFocus={() => setOpen(true)}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onKeyDown={handleKeyDown}
          autoComplete="off"
          spellCheck="false"
        />
        {query ? (
          <button onClick={() => { setQuery(''); setResults([]); inputRef.current?.focus(); }}
            className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors shrink-0">
            <X size={13} />
          </button>
        ) : (
          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-[var(--text-muted)] border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)]">
            ⌘K
          </kbd>
        )}
      </div>

      {/* Dropdown */}
      {showDropdown && (
        <div className="absolute top-full mt-2 left-0 right-0 z-[200] rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] shadow-2xl shadow-[rgba(10,11,13,0.22)] overflow-hidden animate-fade-in">
          {loading && (
            <div className="flex items-center justify-center py-6 text-xs text-[var(--text-muted)] gap-2">
              <Loader2 size={14} className="animate-spin" /> Searching…
            </div>
          )}

          {!loading && results.length === 0 && debouncedQ && (
            <div className="py-6 text-center text-xs text-[var(--text-muted)]">
              No results for <span className="font-semibold text-[var(--text-primary)]">"{debouncedQ}"</span>
            </div>
          )}

          {!loading && results.length > 0 && (
            <ul className="py-1 max-h-72 overflow-y-auto">
              {results.map((r, idx) => (
                <li key={r.ticker}>
                  <button
                    onMouseEnter={() => setActiveIdx(idx)}
                    onMouseDown={(e) => { e.preventDefault(); goTo(r.ticker); }}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                      idx === activeIdx
                        ? 'bg-[rgba(0,82,255,0.1)]'
                        : 'hover:bg-[rgba(91,97,110,0.08)]'
                    }`}
                  >
                    {/* Ticker badge */}
                    <div className="shrink-0 w-10 h-10 rounded-lg bg-gradient-to-br from-[#0052ff] to-[#578bfa] flex items-center justify-center">
                      <TrendingUp size={14} className="text-white" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm font-mono text-[var(--text-primary)]">{r.ticker}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[rgba(0,82,255,0.08)] text-[var(--text-muted)] font-medium truncate max-w-[120px]">
                          {r.industry}
                        </span>
                      </div>
                      <p className="text-xs text-[var(--text-muted)] truncate mt-0.5">{r.company_name}</p>
                    </div>
                    <Building2 size={13} className={`shrink-0 transition-opacity ${idx === activeIdx ? 'opacity-60 text-[var(--accent-cyan)]' : 'opacity-0'}`} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* Footer hint */}
          {!loading && results.length > 0 && (
            <div className="flex items-center gap-3 px-3 py-2 border-t border-[var(--glass-border)] bg-[rgba(255,255,255,0.015)]">
              <span className="text-[10px] text-[var(--text-muted)] flex items-center gap-1">
                <kbd className="px-1 py-0.5 rounded border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] font-mono">↑↓</kbd>
                navigate
              </span>
              <span className="text-[10px] text-[var(--text-muted)] flex items-center gap-1">
                <kbd className="px-1 py-0.5 rounded border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] font-mono">↵</kbd>
                open
              </span>
              <span className="text-[10px] text-[var(--text-muted)] flex items-center gap-1">
                <kbd className="px-1 py-0.5 rounded border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)] font-mono">esc</kbd>
                close
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
