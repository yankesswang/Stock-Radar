import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'wouter';
import { PlusCircle, Loader2, CheckCircle2, XCircle, ChevronRight } from 'lucide-react';
import axios from 'axios';

export default function AddStock() {
    const [ticker, setTicker] = useState('');
    const [group, setGroup] = useState('');
    const [groups, setGroups] = useState([]);
    const [submitting, setSubmitting] = useState(false);
    const [jobId, setJobId] = useState(null);
    const [job, setJob] = useState(null);
    const [error, setError] = useState('');
    const pollRef = useRef(null);
    const [, setLocation] = useLocation();

    // Load available groups on mount
    useEffect(() => {
        axios.get('/api/scrape')
            .then(res => {
                setGroups(res.data.groups || []);
            })
            .catch(() => {
                // Fallback groups if API unavailable
                setGroups([]);
            });
    }, []);

    // Poll job status while running
    useEffect(() => {
        if (!jobId) return;

        pollRef.current = setInterval(async () => {
            try {
                const res = await axios.get(`/api/scrape/${jobId}`);
                setJob(res.data);
                if (res.data.status !== 'running') {
                    clearInterval(pollRef.current);
                }
            } catch {
                clearInterval(pollRef.current);
            }
        }, 2000);

        return () => clearInterval(pollRef.current);
    }, [jobId]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setJob(null);
        setJobId(null);

        const t = ticker.trim().toUpperCase();
        if (!t) { setError('Ticker is required.'); return; }
        if (!group) { setError('Please select a group.'); return; }

        setSubmitting(true);
        try {
            const res = await axios.post('/api/scrape', { ticker: t, group });
            setJobId(res.data.job_id);
            setJob(res.data);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to start scrape job.');
        } finally {
            setSubmitting(false);
        }
    };

    const isDone = job?.status === 'completed';
    const isFailed = job?.status === 'failed';
    const isRunning = job?.status === 'running';

    return (
        <div className="container mx-auto py-12 px-4 max-w-2xl">
            {/* Header */}
            <div className="mb-8">
                <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2">
                    <PlusCircle className="w-6 h-6 text-[var(--accent-purple)]" />
                    Add Stock
                </h1>
                <p className="text-[var(--text-muted)] text-sm mt-1">
                    Enter a ticker and select a group — the system will scrape its data and store it in the database.
                </p>
            </div>

            {/* Form */}
            <form onSubmit={handleSubmit} className="bg-[var(--bg-secondary)] border border-[var(--glass-border)] rounded-2xl p-6 space-y-5">
                {/* Ticker */}
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
                        className="w-full bg-[var(--bg-primary)] border border-[var(--glass-border)] rounded-xl px-4 py-3 text-[var(--text-primary)] font-mono text-sm focus:outline-none focus:border-[rgba(139,92,246,0.5)] transition-colors disabled:opacity-50 placeholder-[var(--text-muted)]"
                        autoComplete="off"
                        spellCheck="false"
                    />
                </div>

                {/* Group */}
                <div>
                    <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                        Industry Group
                    </label>
                    <select
                        value={group}
                        onChange={e => setGroup(e.target.value)}
                        disabled={isRunning || submitting}
                        className="w-full bg-[var(--bg-primary)] border border-[var(--glass-border)] rounded-xl px-4 py-3 text-[var(--text-primary)] text-sm focus:outline-none focus:border-[rgba(139,92,246,0.5)] transition-colors disabled:opacity-50 appearance-none cursor-pointer"
                    >
                        <option value="" disabled>Select a group…</option>
                        {groups.map(g => (
                            <option key={g} value={g}>{g}</option>
                        ))}
                    </select>
                </div>

                {/* Error */}
                {error && (
                    <p className="text-red-400 text-sm flex items-center gap-1.5">
                        <XCircle className="w-4 h-4 shrink-0" />
                        {error}
                    </p>
                )}

                {/* Submit */}
                <button
                    type="submit"
                    disabled={submitting || isRunning}
                    className="w-full py-3 px-4 bg-gradient-to-r from-[var(--accent-purple)] to-[var(--accent-cyan)] text-white text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                    {submitting || isRunning
                        ? <><Loader2 className="w-4 h-4 animate-spin" /> Scraping…</>
                        : <><PlusCircle className="w-4 h-4" /> Start Scrape</>
                    }
                </button>
            </form>

            {/* Job status */}
            {job && (
                <div className={`mt-6 bg-[var(--bg-secondary)] border rounded-2xl p-5 transition-colors ${
                    isDone  ? 'border-emerald-500/30' :
                    isFailed ? 'border-red-500/30' :
                    'border-[var(--glass-border)]'
                }`}>
                    {/* Status header */}
                    <div className="flex items-center gap-2 mb-4">
                        {isDone && <CheckCircle2 className="w-5 h-5 text-emerald-400" />}
                        {isFailed && <XCircle className="w-5 h-5 text-red-400" />}
                        {isRunning && <Loader2 className="w-5 h-5 text-[var(--accent-purple)] animate-spin" />}
                        <span className={`font-semibold text-sm ${
                            isDone ? 'text-emerald-400' : isFailed ? 'text-red-400' : 'text-[var(--accent-purple)]'
                        }`}>
                            {isDone ? 'Completed' : isFailed ? 'Failed' : 'Running…'}
                        </span>
                        <span className="text-[var(--text-muted)] text-xs ml-auto font-mono">
                            Job {job.job_id}
                        </span>
                    </div>

                    {/* Steps */}
                    <ol className="space-y-1.5">
                        {(job.steps || []).map((s, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-[var(--text-secondary)]">
                                <ChevronRight className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[var(--text-muted)]" />
                                <span className={i === job.steps.length - 1 && isRunning ? 'text-[var(--accent-cyan)]' : ''}>{s}</span>
                            </li>
                        ))}
                        {isRunning && (
                            <li className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
                                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                                <span>Waiting for next update…</span>
                            </li>
                        )}
                    </ol>

                    {/* Navigate to stock on success */}
                    {isDone && (
                        <button
                            onClick={() => setLocation(`/stock/${job.ticker}`)}
                            className="mt-5 w-full py-2.5 px-4 bg-[rgba(139,92,246,0.12)] border border-[rgba(139,92,246,0.25)] text-[var(--accent-purple)] text-sm font-medium rounded-xl hover:bg-[rgba(139,92,246,0.2)] transition-colors flex items-center justify-center gap-2"
                        >
                            View {job.ticker} →
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
