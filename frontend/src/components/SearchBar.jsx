import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'wouter';
import { Search, Loader2 } from 'lucide-react';
import axios from 'axios';

export default function SearchBar() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);
    const [_, setLocation] = useLocation();
    const inputRef = useRef(null);

    useEffect(() => {
        const search = async () => {
            if (query.length < 1) {
                setResults([]);
                return;
            }
            setLoading(true);
            try {
                const res = await axios.get(`/api/search?q=${query}&limit=5`);
                setResults(res.data);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        };

        const timeoutId = setTimeout(search, 300);
        return () => clearTimeout(timeoutId);
    }, [query]);

    const handleSelect = (ticker) => {
        setLocation(`/stock/${ticker}`);
        setQuery('');
        setResults([]);
    };

    return (
        <div className="relative w-full max-w-xl mx-auto">
            <div className="relative group">
                <div className="absolute -inset-0.5 bg-gradient-to-r from-[var(--accent-purple)] to-[var(--accent-cyan)] rounded-2xl opacity-0 group-focus-within:opacity-20 blur transition-opacity duration-300" />
                <div className="relative">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-muted)] w-5 h-5" />
                    <input
                        ref={inputRef}
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search stocks... (NVDA, AAPL, MSFT)"
                        className="w-full bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-xl py-3.5 pl-12 pr-4 text-[var(--text-primary)] text-sm focus:outline-none focus:border-[rgba(139,92,246,0.4)] transition-colors duration-200 placeholder-[var(--text-muted)]"
                    />
                    {loading && (
                        <Loader2 className="absolute right-4 top-1/2 -translate-y-1/2 text-[var(--accent-purple)] w-4 h-4 animate-spin" />
                    )}
                </div>
            </div>

            {results.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-2 bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-xl overflow-hidden shadow-2xl shadow-black/40 z-50">
                    {results.map((stock, i) => (
                        <div
                            key={stock.ticker}
                            onClick={() => handleSelect(stock.ticker)}
                            className={`px-4 py-3 hover:bg-[var(--bg-tertiary)] cursor-pointer flex justify-between items-center transition-colors duration-150 ${
                                i !== results.length - 1 ? 'border-b border-[var(--glass-border)]' : ''
                            }`}
                        >
                            <div className="flex items-center gap-3">
                                <span className="font-bold font-mono text-[var(--accent-cyan)] text-sm">{stock.ticker}</span>
                                <span className="text-sm text-[var(--text-secondary)]">{stock.company_name}</span>
                            </div>
                            <span className="badge badge-neutral text-xs">{stock.industry}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
