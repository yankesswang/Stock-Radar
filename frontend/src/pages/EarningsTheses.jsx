import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
    Target, Loader2, Wallet, ChevronDown, ChevronRight,
    TrendingUp, TrendingDown, ExternalLink, Sparkles, Search,
} from 'lucide-react';
import ClusterTabs from '../components/ClusterTabs';
import { EARNINGS_TABS } from '../components/clusterTabsDef';
import { STANCE_STYLE } from '../components/earningsStance';

const DEEP_LABELS = {
    incremental_margin: '增量營益率',
    depreciation: '折舊推演',
    fcf: 'FCF',
    implied_dcf: '反推 DCF',
    eps_scenario: 'EPS 情境',
    other: '其他自算',
};

// 深度指標照 DEEP_LABELS 的順序分組，未知的 kind 排最後
function groupByKind(deep) {
    const order = Object.keys(DEEP_LABELS);
    const g = new Map();
    deep.forEach((d) => {
        if (!g.has(d.kind)) g.set(d.kind, []);
        g.get(d.kind).push(d);
    });
    return [...g.entries()].sort((a, b) => {
        const ia = order.indexOf(a[0]), ib = order.indexOf(b[0]);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
}

// 展開後才抓該檔的數據與深度指標——46 檔的成績單一次全載是幾百 KB 的浪費
function useInsights(ticker, open) {
    const [data, setData] = useState(null);
    useEffect(() => {
        if (!open || data) return;
        axios.get(`/api/earnings-notes/insights/${ticker}`)
            .then(({ data: d }) => setData(d))
            .catch(() => setData({ error: true }));
    }, [open, ticker, data]);
    return data;
}

function TickerCard({ row }) {
    const [open, setOpen] = useState(false);
    const insights = useInsights(row.ticker, open);
    const quarterMetrics = useMemo(() => {
        if (!insights?.metrics) return null;
        const q = Object.keys(insights.metrics)[0];
        return q ? insights.metrics[q] : null;
    }, [insights]);

    return (
        <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] overflow-hidden">
            <button onClick={() => setOpen((v) => !v)}
                    aria-expanded={open}
                    aria-label={`${open ? '收合' : '展開'} ${row.ticker} 的論點與數據`}
                    className="ui-focus cursor-pointer w-full text-left p-4 flex items-center gap-3 flex-wrap
                               hover:bg-[rgba(0,82,255,0.03)] transition-colors duration-200">
                {open ? <ChevronDown size={16} className="text-[var(--text-meta)]" />
                      : <ChevronRight size={16} className="text-[var(--text-meta)]" />}
                <span className="text-[15px] font-bold text-[var(--text-primary)]">{row.ticker}</span>
                <span className="num text-[12px] text-[var(--text-meta)]">
                    {row.quarter?.replace('_', ' ')} · {row.report_date}
                </span>
                {row.held && (
                    <span className="text-[11px] px-1.5 py-0.5 rounded border border-[rgba(16,185,129,0.35)]
                                     bg-[rgba(16,185,129,0.12)] text-[var(--accent-success-text)]">
                        持有 {row.position?.shares} 股
                    </span>
                )}
                <span className="text-[12px] text-[var(--text-secondary)]">
                    {row.thesis_count} 條論點
                    {row.tracked_count > 0 && (
                        <span className="text-[var(--accent-cyan-text)]"> · {row.tracked_count} 條可比上季</span>
                    )}
                </span>
                {row.stance && (
                    <span className={`ml-auto text-[12px] font-semibold px-2 py-0.5 rounded-full border ${STANCE_STYLE[row.stance]}`}>
                        {row.stance_label}
                    </span>
                )}
            </button>

            {open && (
                <div className="px-4 pb-4 border-t border-[var(--glass-border)] pt-3 space-y-4">
                    {row.action_text && (
                        <div className="text-[13px] text-[var(--text-primary)] leading-relaxed">
                            {row.action_text}
                        </div>
                    )}

                    <div>
                        <div className="text-[12px] font-semibold text-[var(--text-secondary)] mb-1.5">論點追蹤</div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-[12.5px] border-collapse">
                                <caption className="sr-only">
                                    {row.ticker} {row.quarter} 論點追蹤：各指標的本季現值與多空門檻
                                </caption>
                                <thead>
                                    <tr className="text-[var(--text-meta)] text-left">
                                        <th scope="col" className="py-1.5 pr-3 font-medium">指標</th>
                                        <th scope="col" className="py-1.5 pr-3 font-medium">本季現值</th>
                                        <th scope="col" className="py-1.5 pr-3 font-medium">
                                            <TrendingUp size={11} className="inline mr-1" aria-hidden="true" />多方需要看到
                                        </th>
                                        <th scope="col" className="py-1.5 font-medium">
                                            <TrendingDown size={11} className="inline mr-1" aria-hidden="true" />空方確認訊號
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {row.theses.map((t, i) => (
                                        <tr key={i} className="border-t border-[var(--glass-border)] align-top">
                                            <th scope="row" className="py-1.5 pr-3 text-left font-normal text-[var(--text-primary)]">{t.metric}</th>
                                            <td className="num py-1.5 pr-3 text-[var(--text-primary)] font-medium whitespace-nowrap">
                                                {t.current_value || '—'}
                                                {t.previous && (
                                                    // 上季畫的線，這季實際落在哪：跨季追蹤的重點
                                                    <div className="text-[11px] text-[var(--text-meta)] font-normal mt-0.5">
                                                        上季 {t.previous.value}
                                                    </div>
                                                )}
                                            </td>
                                            <td className="py-1.5 pr-3 text-[var(--accent-success-text)]">{t.bull_condition || '—'}</td>
                                            <td className="py-1.5 text-[var(--accent-danger-text)]">{t.bear_condition || '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {!insights && (
                        <div className="text-[12px] text-[var(--text-meta)] flex items-center gap-1.5">
                            <Loader2 size={12} className="animate-spin" /> 載入關鍵數據…
                        </div>
                    )}

                    {insights?.deep?.length > 0 && (
                        <div>
                            <div className="text-[12px] font-semibold text-[var(--text-secondary)] mb-1.5 flex items-center gap-1">
                                <Sparkles size={12} /> 自算深度指標
                            </div>
                            {/* 一檔最多 33 項，平鋪成一長串讀不動；按類別分組後
                                「反推 DCF 講什麼」「EPS 三情境長怎樣」一眼能挑 */}
                            {groupByKind(insights.deep).map(([kind, items]) => (
                                <div key={kind} className="mb-2">
                                    <div className="text-[11px] text-[var(--text-meta)] mb-1">
                                        {DEEP_LABELS[kind] || kind}
                                    </div>
                                    <div className="grid gap-1.5 sm:grid-cols-2">
                                        {items.map((d, i) => (
                                            <div key={i} className="rounded-lg border border-[var(--glass-border)] p-2">
                                                <div className="flex items-baseline gap-2">
                                                    <span className="text-[12.5px] font-semibold text-[var(--text-primary)]">
                                                        {d.label}
                                                    </span>
                                                    <span className="num ml-auto text-[12.5px] font-bold text-[var(--accent-cyan-text)] whitespace-nowrap">
                                                        {d.value}
                                                    </span>
                                                </div>
                                                {d.detail && (
                                                    <div className="text-[11.5px] text-[var(--text-meta)] mt-0.5 leading-snug">
                                                        {d.detail}
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {quarterMetrics && (
                        <div>
                            <div className="text-[12px] font-semibold text-[var(--text-secondary)] mb-1.5">關鍵數據</div>
                            {Object.entries(quarterMetrics).map(([section, rows]) => (
                                <div key={section} className="mb-2.5">
                                    <div className="text-[11px] text-[var(--text-meta)] mb-1">{section}</div>
                                    <div className="overflow-x-auto">
                                        <table className="w-full text-[12.5px] border-collapse">
                                            <tbody>
                                                {rows.map((m, i) => (
                                                    <tr key={i} className="border-t border-[var(--glass-border)]">
                                                        <td className="py-1 pr-3 text-[var(--text-secondary)]">{m.metric}</td>
                                                        <td className="num py-1 pr-3 font-medium text-[var(--text-primary)] whitespace-nowrap">{m.value}</td>
                                                        <td className="num py-1 pr-3 whitespace-nowrap text-[var(--text-meta)]">
                                                            {m.qoq && <>QoQ <span className={m.qoq.startsWith('-') ? 'text-[var(--accent-danger-text)]' : 'text-[var(--accent-success-text)]'}>{m.qoq}</span></>}
                                                        </td>
                                                        <td className="num py-1 pr-3 whitespace-nowrap text-[var(--text-meta)]">
                                                            {m.yoy && <>YoY <span className={m.yoy.startsWith('-') ? 'text-[var(--accent-danger-text)]' : 'text-[var(--accent-success-text)]'}>{m.yoy}</span></>}
                                                        </td>
                                                        <td className="py-1 text-[var(--text-meta)]">{m.comment}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="flex gap-3 text-[12px]">
                        <Link href={`/stock/${row.ticker}/earnings-notes`}
                              className="ui-focus rounded text-[var(--text-secondary)] hover:text-[var(--accent-cyan-text)] inline-flex items-center gap-1">
                            讀完整筆記 <ExternalLink size={11} aria-hidden="true" />
                        </Link>
                        <Link href={`/stock/${row.ticker}`}
                              className="ui-focus rounded text-[var(--text-secondary)] hover:text-[var(--accent-cyan-text)]">個股頁</Link>
                    </div>
                </div>
            )}
        </div>
    );
}

export default function EarningsTheses() {
    const [rows, setRows]         = useState([]);
    const [meta, setMeta]         = useState({});
    const [loading, setLoading]   = useState(true);
    const [error, setError]       = useState('');
    const [heldOnly, setHeldOnly] = useState(false);
    const [q, setQ]               = useState('');

    useEffect(() => {
        setLoading(true);
        axios.get('/api/earnings-notes/theses', { params: { held_only: heldOnly } })
            .then(({ data }) => {
                setRows(data.tickers || []);
                setMeta({ count: data.count, held: data.held_count, tracked: data.tracked_total });
                setError(data.error || '');
            })
            .catch(() => setError('讀取論點追蹤失敗'))
            .finally(() => setLoading(false));
    }, [heldOnly]);

    const shown = useMemo(() => {
        const s = q.trim().toUpperCase();
        return s ? rows.filter((r) => r.ticker.includes(s)) : rows;
    }, [rows, q]);

    const totalTheses = useMemo(
        () => rows.reduce((n, r) => n + r.thesis_count, 0), [rows]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-24 text-[var(--text-meta)]">
                <Loader2 className="animate-spin mr-2" size={18} /> 載入論點追蹤…
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto px-4 py-6">
            <ClusterTabs items={EARNINGS_TABS} active="/earnings-theses" className="mb-5" />
            <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2">
                <Target size={20} /> 論點追蹤
            </h1>
            <div className="text-[13px] text-[var(--text-meta)] mt-1 mb-4">
                每檔財報筆記寫下的可驗證論點：多方要看到什麼、空方確認訊號是什麼
                {meta.count != null && ` · ${meta.count} 檔 / ${totalTheses} 條論點`}
            </div>

            {meta.tracked === 0 && !error && (
                <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                                p-3 text-[12.5px] text-[var(--text-meta)] mb-4">
                    目前每檔都只有一季筆記，所以還沒有「上季門檻 vs 這季現值」可比。
                    下一季財報筆記進來後，同名論點會自動接上跨季對照。
                </div>
            )}

            {error && (
                <div className="rounded-xl border border-[rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.08)]
                                p-4 text-[13px] text-[var(--text-secondary)] mb-5">{error}</div>
            )}

            <div className="flex gap-2 flex-wrap items-center mb-4">
                <div className="relative">
                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-meta)]" />
                    {/* 有 placeholder 不等於有標籤：螢幕閱讀器需要 aria-label，
                        而且 focus:outline-none 不能不給替代焦點樣式 */}
                    <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜尋 ticker"
                           aria-label="以代號搜尋標的"
                           className="ui-focus tap pl-8 pr-3 py-2 rounded-full text-[13px] bg-[var(--bg-secondary)]
                                      border border-[var(--glass-border)] text-[var(--text-primary)]
                                      focus:border-[rgba(0,82,255,0.35)] w-44" />
                </div>
                <button onClick={() => setHeldOnly((v) => !v)}
                    className={`ui-focus tap cursor-pointer ml-auto px-3 py-2 rounded-full text-[13px] font-medium border inline-flex items-center gap-1.5 transition-colors duration-200 ${
                        heldOnly ? 'text-[var(--accent-success-text)] bg-[rgba(16,185,129,0.12)] border-[rgba(16,185,129,0.35)]'
                                 : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                    <Wallet size={14} /> 只看持倉
                </button>
            </div>

            <div className="space-y-2.5">
                {shown.map((r) => <TickerCard key={r.ticker} row={r} />)}
                {!shown.length && !error && (
                    <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                                    p-8 text-center text-[var(--text-meta)]">沒有符合的標的</div>
                )}
            </div>
        </div>
    );
}
