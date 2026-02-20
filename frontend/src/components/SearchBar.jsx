import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'wouter';
import { Search, Loader2 } from 'lucide-react';
import axios from 'axios';

export default function SearchBar() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);
    const [isOpen, setIsOpen] = useState(false);
    const [selectedIndex, setSelectedIndex] = useState(-1);
    const [_, setLocation] = useLocation();
    const inputRef = useRef(null);
    const wrapperRef = useRef(null);

    // Close dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    useEffect(() => {
        const search = async () => {
            if (query.trim().length < 1) {
                setResults([]);
                setIsOpen(false);
                return;
            }
            setLoading(true);
            try {
                // URL encode the query to handle special characters
                const res = await axios.get(`/api/search`, {
                    params: { q: query.trim(), limit: 8 }
                });
                setResults(res.data);
                setIsOpen(res.data.length > 0);
                setSelectedIndex(-1);
            } catch (err) {
                console.error(err);
                setResults([]);
            } finally {
                setLoading(false);
            }
        };

        const timeoutId = setTimeout(search, 150); // Faster debounce for autocomplete
        return () => clearTimeout(timeoutId);
    }, [query]);

    const handleSelect = (ticker) => {
        setLocation(`/stock/${ticker}`);
        setQuery('');
        setResults([]);
        setIsOpen(false);
    };

    // Keyboard navigation
    const handleKeyDown = (e) => {
        if (!isOpen || results.length === 0) {
            if (e.key === 'Enter' && query.trim()) {
                // Direct navigation if typing a ticker
                setLocation(`/stock/${query.trim().toUpperCase()}`);
                setQuery('');
            }
            return;
        }

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                setSelectedIndex(prev => (prev < results.length - 1 ? prev + 1 : 0));
                break;
            case 'ArrowUp':
                e.preventDefault();
                setSelectedIndex(prev => (prev > 0 ? prev - 1 : results.length - 1));
                break;
            case 'Enter':
                e.preventDefault();
                if (selectedIndex >= 0) {
                    handleSelect(results[selectedIndex].ticker);
                } else if (results.length > 0) {
                    handleSelect(results[0].ticker);
                }
                break;
            case 'Escape':
                setIsOpen(false);
                setSelectedIndex(-1);
                break;
        }
    };

    return (
        <div className="relative w-full max-w-xl mx-auto" ref={wrapperRef}>
            <div className="relative group">
                <div className="absolute -inset-0.5 bg-gradient-to-r from-[var(--accent-purple)] to-[var(--accent-cyan)] rounded-2xl opacity-0 group-focus-within:opacity-20 blur transition-opacity duration-300" />
                <div className="relative">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-muted)] w-5 h-5" />
                    <input
                        ref={inputRef}
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        onFocus={() => results.length > 0 && setIsOpen(true)}
                        placeholder="Search stocks... (nvda, Apple, msft)"
                        className="w-full bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-xl py-3.5 pl-12 pr-4 text-[var(--text-primary)] text-sm focus:outline-none focus:border-[rgba(139,92,246,0.4)] transition-colors duration-200 placeholder-[var(--text-muted)]"
                        autoComplete="off"
                        spellCheck="false"
                    />
                    {loading && (
                        <Loader2 className="absolute right-4 top-1/2 -translate-y-1/2 text-[var(--accent-purple)] w-4 h-4 animate-spin" />
                    )}
                </div>
            </div>

            {isOpen && results.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-2 bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-xl overflow-hidden shadow-2xl shadow-black/40 z-50">
                    {results.map((stock, i) => (
                        <div
                            key={stock.ticker}
                            onClick={() => handleSelect(stock.ticker)}
                            onMouseEnter={() => setSelectedIndex(i)}
                            className={`px-4 py-3 cursor-pointer flex justify-between items-center transition-colors duration-150 ${
                                i !== results.length - 1 ? 'border-b border-[var(--glass-border)]' : ''
                            } ${selectedIndex === i ? 'bg-[var(--bg-tertiary)]' : 'hover:bg-[var(--bg-tertiary)]'}`}
                        >
                            <div className="flex items-center gap-3">
                                <span className="font-bold font-mono text-[var(--accent-cyan)] text-sm">{stock.ticker}</span>
                                <span className="text-sm text-[var(--text-secondary)] truncate max-w-[200px]">{stock.company_name}</span>
                            </div>
                            <span className="badge badge-neutral text-xs shrink-0">{stock.industry}</span>
                        </div>
                    ))}
                    <div className="px-4 py-2 text-[11px] text-[var(--text-muted)] bg-[var(--bg-tertiary)] border-t border-[var(--glass-border)]">
                        <span className="opacity-70">↑↓</span> Navigate · <span className="opacity-70">Enter</span> Select · <span className="opacity-70">Esc</span> Close
                    </div>
                </div>
            )}
        </div>
    );
}
