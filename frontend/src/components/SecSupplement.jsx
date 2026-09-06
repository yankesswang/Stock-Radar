import { useState, useEffect } from 'react';
import axios from 'axios';
import { Sparkles, AlertTriangle, Check } from 'lucide-react';

// stockanalysis 的 TTM 快照：補 SEC 沒有的欄位（員工數、WACC、Z-Score、分析師預估），
// 並拿來跟自算的 SEC 指標對帳。它沒有歷史，所以只負責「當下」這一格。
const VERDICT = {
    check:      { label: '待查',   tone: 'var(--accent-danger-text)',  icon: AlertTriangle },
    stale:      { label: '期間不同', tone: 'var(--text-meta)',          icon: null },
    definition: { label: '口徑不同', tone: 'var(--text-meta)',          icon: null },
    ok:         { label: '相符',   tone: 'var(--accent-success-text)', icon: Check },
};

function fmtNum(v) {
    if (v == null) return '—';
    const a = Math.abs(v);
    if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (a < 10) return v.toFixed(3);
    return v.toLocaleString('en-US', { maximumFractionDigits: 1 });
}

export default function SecSupplement({ ticker }) {
    const [data, setData]   = useState(null);
    const [state, setState] = useState('loading');
    const [showAll, setShowAll] = useState(false);

    useEffect(() => {
        if (!ticker) return;
        setState('loading');
        axios.get(`/api/financials/${ticker}/supplement`)
            .then(({ data: d }) => {
                setData(d);
                setState(d.groups?.length ? 'ok' : 'none');
            })
            .catch(() => setState('none'));
    }, [ticker]);

    if (state !== 'ok' || !data) return null;

    const recon = showAll ? data.reconciliation
        : data.reconciliation.filter((r) => r.verdict === 'check');

    return (
        <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
            <div className="flex items-center gap-2 flex-wrap mb-3">
                <h2 className="text-[15px] font-bold text-[var(--text-primary)] inline-flex items-center gap-2">
                    <Sparkles size={16} aria-hidden="true" /> 補充指標與對帳
                </h2>
                <span className="text-[11.5px] text-[var(--text-meta)]">
                    {data.source} · SEC 沒有的欄位由此補
                    {data.fetched_at ? ` · 抓取於 ${data.fetched_at.slice(0, 16).replace('T', ' ')}` : ''}
                </span>
            </div>

            {/* 每個分組一張「指標 × 數值」小表。這站只有 TTM 單一快照、沒有期間欄，
                所以是兩欄的鍵值表；分組並排而不是串成一張長表，是為了不讓 30 幾列
                把下面的對帳表推出畫面外 */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 mb-4">
                {data.groups.map((g) => (
                    <div key={g.group} className="rounded-lg border border-[var(--glass-border)]">
                        <table className="w-full text-[12px] border-collapse table-fixed">
                            <caption className="py-1.5 px-2.5 text-left text-[11px] text-[var(--text-meta)]">
                                {g.group}
                            </caption>
                            <tbody>
                                {g.items.map((it) => (
                                    <tr key={it.id} className="border-t border-[var(--glass-border)]">
                                        <th scope="row"
                                            className="py-1 px-2.5 text-left font-normal align-top leading-tight
                                                       text-[var(--text-secondary)]">
                                            {it.label}
                                        </th>
                                        <td className="num w-[38%] py-1 px-2.5 text-right align-top font-medium
                                                       whitespace-nowrap text-[var(--text-primary)]">
                                            {it.value ?? '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ))}
            </div>

            {data.reconciliation?.length > 0 && (
                <div>
                    <div className="flex items-center gap-2 mb-1.5">
                        <span className="text-[12px] font-semibold text-[var(--text-secondary)]">
                            對帳：自算的 SEC 指標 vs 對方 TTM
                        </span>
                        <span className="text-[11.5px] text-[var(--text-meta)]">
                            {data.flagged} 項待查
                        </span>
                        <button onClick={() => setShowAll((v) => !v)} aria-pressed={showAll}
                            className="ui-focus tap cursor-pointer ml-auto px-2.5 py-1.5 rounded-full text-[11.5px]
                                       border border-[var(--glass-border)] text-[var(--text-secondary)]
                                       transition-colors duration-200">
                            {showAll ? '只看待查' : '看全部'}
                        </button>
                    </div>
                    {/* 差異不等於誰錯：口徑不同、期間不同都會產生差距，所以分級標示，
                        只有 check 是真的值得看一眼的 */}
                    <div className="overflow-auto max-h-[40vh]">
                        <table className="data-table w-full text-[12px] border-collapse">
                            <caption className="sr-only">{ticker} 自算指標與 stockanalysis 的對帳</caption>
                            <thead>
                                <tr className="text-[var(--text-meta)]">
                                    <th scope="col" className="py-1.5 px-2 text-left font-medium sticky-col">指標</th>
                                    <th scope="col" className="num py-1.5 px-2 text-right font-medium">自算</th>
                                    <th scope="col" className="num py-1.5 px-2 text-right font-medium">對方</th>
                                    <th scope="col" className="num py-1.5 px-2 text-right font-medium">差異</th>
                                    <th scope="col" className="py-1.5 px-2 text-left font-medium">判定</th>
                                </tr>
                            </thead>
                            <tbody>
                                {recon.map((r) => {
                                    const v = VERDICT[r.verdict] || VERDICT.ok;
                                    return (
                                        <tr key={r.metric} className="border-t border-[var(--glass-border)]">
                                            <th scope="row" className="sticky-col py-1 px-2 text-left font-normal text-[var(--text-primary)]"
                                                title={r.ours_basis}>
                                                {r.metric}
                                            </th>
                                            <td className="num py-1 px-2 text-right">{fmtNum(r.ours)}</td>
                                            <td className="num py-1 px-2 text-right">{fmtNum(r.theirs)}</td>
                                            <td className="num py-1 px-2 text-right" style={{ color: v.tone }}>
                                                {(r.diff_pct * 100).toFixed(1)}%
                                            </td>
                                            <td className="py-1 px-2" style={{ color: v.tone }}>{v.label}</td>
                                        </tr>
                                    );
                                })}
                                {!recon.length && (
                                    <tr><td colSpan={5} className="py-3 text-center text-[var(--text-meta)]">
                                        沒有待查項目
                                    </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </section>
    );
}
