import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from '../lib/apiClient';
import {
    FileText, Loader2, Wallet, ShieldAlert, ExternalLink, Sparkles, HelpCircle,
} from 'lucide-react';
import ClusterTabs from '../components/ClusterTabs';
import { EARNINGS_TABS } from '../components/clusterTabsDef';
import { STANCE_STYLE } from '../components/earningsStance';

const FILTERS = [
    { key: 'all',   label: '全部' },
    { key: 'add',   label: '加碼' },
    { key: 'hold',  label: '續抱' },
    { key: 'trim',  label: '減碼' },
    { key: 'exit',  label: '出清' },
    { key: 'avoid', label: '不進場' },
    { key: 'watch', label: '觀察' },
    { key: 'todo',  label: '待補' },
];

function fmtPct(v) {
    if (v == null) return '—';
    return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
}

export default function EarningsConclusions() {
    const [rows, setRows]       = useState([]);
    const [meta, setMeta]       = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState('');
    const [heldOnly, setHeldOnly] = useState(false);
    const [filter, setFilter]   = useState('all');

    useEffect(() => {
        setLoading(true);
        axios.get('/api/earnings-notes/conclusions', { params: { held_only: heldOnly } })
            .then(({ data }) => {
                setRows(data.conclusions || []);
                setMeta({ count: data.count, held: data.held_count, byStance: data.by_stance });
                setError(data.error || '');
            })
            .catch(() => setError('讀取財報結論失敗'))
            .finally(() => setLoading(false));
    }, [heldOnly]);

    const shown = useMemo(() => rows.filter((r) => (
        filter === 'all' ? true : filter === 'todo' ? !r.stance : r.stance === filter
    )), [rows, filter]);

    const counts = useMemo(() => {
        const c = { all: rows.length, todo: rows.filter((r) => !r.stance).length };
        rows.forEach((r) => { if (r.stance) c[r.stance] = (c[r.stance] || 0) + 1; });
        return c;
    }, [rows]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-24 text-[var(--text-meta)]">
                <Loader2 className="animate-spin mr-2" size={18} /> 載入財報結論…
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto px-4 py-6">
            <ClusterTabs items={EARNINGS_TABS} active="/earnings-conclusions" className="mb-5" />
            <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2">
                <FileText size={20} /> 財報結論總覽
            </h1>
            <div className="text-[13px] text-[var(--text-meta)] mt-1 mb-5">
                每篇財報筆記的操作決定，新到舊{meta.count != null && ` · ${meta.count} 篇`}
                {meta.held != null && ` · 持倉 ${meta.held} 檔`}
            </div>

            {error && (
                <div className="rounded-xl border border-[rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.08)]
                                p-4 text-[13px] text-[var(--text-secondary)] mb-5">
                    {error}
                </div>
            )}

            <div className="flex gap-2 flex-wrap items-center mb-5">
                {FILTERS.map((f) => {
                    const on = filter === f.key;
                    const n = counts[f.key] ?? 0;
                    if (!n && f.key !== 'all') return null;
                    return (
                        <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={on}
                            className={`ui-focus tap cursor-pointer px-3 py-2 rounded-full text-[13px] font-medium border transition-colors duration-200 ${
                                on ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                   : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)] hover:border-[rgba(0,82,255,0.35)]'}`}>
                            {f.label} <span className="opacity-60">{n}</span>
                        </button>
                    );
                })}
                <button onClick={() => setHeldOnly((v) => !v)} aria-pressed={heldOnly}
                    className={`ui-focus tap cursor-pointer ml-auto px-3 py-2 rounded-full text-[13px] font-medium border inline-flex items-center gap-1.5 transition-colors duration-200 ${
                        heldOnly ? 'text-[var(--accent-success-text)] bg-[rgba(16,185,129,0.12)] border-[rgba(16,185,129,0.35)]'
                                 : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                    <Wallet size={14} /> 只看持倉
                </button>
            </div>

            <div className="space-y-2.5">
                {shown.map((r) => (
                    <div key={`${r.ticker}-${r.quarter}`}
                         className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
                        <div className="flex items-start gap-3 flex-wrap">
                            <Link href={`/stock/${r.ticker}`}
                                  className="ui-focus rounded text-[15px] font-bold text-[var(--text-primary)] hover:text-[var(--accent-cyan-text)]">
                                {r.ticker}
                            </Link>
                            <span className="num text-[12px] text-[var(--text-meta)] pt-0.5">
                                {r.quarter?.replace('_', ' ')} · {r.report_date} · {r.industry}
                            </span>
                            {r.held && (
                                <span className="num text-[11px] px-1.5 py-0.5 rounded border border-[rgba(16,185,129,0.35)]
                                                 bg-[rgba(16,185,129,0.12)] text-[var(--accent-success-text)]">
                                    持有 {r.position?.shares} 股
                                    {r.weight_pct != null && ` · ${r.weight_pct}%`}
                                </span>
                            )}
                            <div className="ml-auto flex items-center gap-2">
                                {r.stance ? (
                                    <span className={`text-[12px] font-semibold px-2 py-0.5 rounded-full border ${STANCE_STYLE[r.stance]}`}>
                                        {r.stance_label}
                                    </span>
                                ) : (
                                    <span className="text-[12px] px-2 py-0.5 rounded-full border border-dashed
                                                     border-[var(--glass-border)] text-[var(--text-meta)]
                                                     inline-flex items-center gap-1">
                                        <HelpCircle size={12} /> 待補
                                    </span>
                                )}
                                <Link href={`/stock/${r.ticker}/earnings-notes`}
                                      aria-label={`讀 ${r.ticker} 完整財報筆記`}
                                      className="ui-focus rounded text-[12px] text-[var(--text-secondary)] hover:text-[var(--accent-cyan-text)] inline-flex items-center gap-1">
                                    讀筆記 <ExternalLink size={12} aria-hidden="true" />
                                </Link>
                            </div>
                        </div>

                        {r.action_text ? (
                            <div className="mt-2 text-[13.5px] text-[var(--text-primary)] leading-relaxed">
                                {r.action_text}
                                {r.action_source === 'llm' && (
                                    <span className="ml-1.5 text-[11px] text-[var(--text-meta)] inline-flex items-center gap-0.5 align-middle">
                                        <Sparkles size={11} /> {r.source_label}
                                    </span>
                                )}
                            </div>
                        ) : (
                            // 抽取器不敢判的：只給筆記裡帶動作字眼的候選句，不當成結論
                            <div className="mt-2 text-[12.5px] text-[var(--text-meta)] leading-relaxed">
                                筆記沒有寫明確的動作行，候選句：
                                <ul className="list-disc ml-5 mt-1 space-y-0.5">
                                    {(r.action_candidates || []).slice(0, 2).map((c, i) => <li key={i}>{c}</li>)}
                                    {!(r.action_candidates || []).length && <li>（結論段沒有可辨識的動作句）</li>}
                                </ul>
                            </div>
                        )}

                        {r.summary_short && (
                            <div className="mt-1.5 text-[12.5px] text-[var(--text-secondary)] leading-relaxed">
                                {r.summary_short}
                            </div>
                        )}

                        <div className="num mt-2.5 flex gap-4 flex-wrap text-[12px] text-[var(--text-meta)]">
                            {r.stop_loss != null && (
                                <span className="num inline-flex items-center gap-1">
                                    <ShieldAlert size={12} /> 停損 ${r.stop_loss}
                                    {r.stop_distance_pct != null && (
                                        <span className="opacity-70">（筆記當時 -{r.stop_distance_pct}%）</span>
                                    )}
                                </span>
                            )}
                            {r.price_after_earnings != null && <span>筆記價 ${r.price_after_earnings}</span>}
                            {r.reaction_pct != null && (
                                <span className={r.reaction_pct >= 0 ? 'text-[var(--accent-success-text)]' : 'text-[var(--accent-danger-text)]'}>
                                    財報反應 {fmtPct(r.reaction_pct)}
                                </span>
                            )}
                        </div>
                    </div>
                ))}
                {!shown.length && !error && (
                    <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                                    p-8 text-center text-[var(--text-meta)]">
                        這個篩選沒有筆記
                    </div>
                )}
            </div>
        </div>
    );
}
