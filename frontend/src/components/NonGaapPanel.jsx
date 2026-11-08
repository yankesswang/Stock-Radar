import { useState, useEffect } from 'react';
import axios from '../lib/apiClient';
import { Scale, ExternalLink, ChevronDown, ChevronRight } from 'lucide-react';

// 公司自訂的 Non-GAAP，來源是財報新聞稿的對帳表。
// SEC 的 XBRL 只標記 GAAP，stockanalysis 給的是 S&P 正規化值——
// 「公司自己怎麼調整」只有新聞稿裡有。
const MONTHS = [{ v: 3, label: '單季' }, { v: 12, label: '全年' }];

function fmt(v, unit) {
    if (v == null) return '—';
    if (unit === 'pct') return `${v.toFixed(1)}%`;
    const a = Math.abs(v);
    const sign = v < 0 ? '-' : '';
    // 新聞稿多以千為單位；這裡不換算，只做可讀的縮寫
    if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(0)}K`;
    return `${sign}${a.toFixed(2)}`;
}

export default function NonGaapPanel({ ticker }) {
    const [months, setMonths] = useState(3);
    const [data, setData]     = useState(null);
    const [state, setState]   = useState('loading');
    const [open, setOpen]     = useState(null);

    useEffect(() => {
        if (!ticker) return;
        setState('loading');
        axios.get(`/api/financials/${ticker}/nongaap`, { params: { months, limit: 8 } })
            .then(({ data: d }) => {
                setData(d);
                setState(d.measures?.length ? 'ok' : 'none');
            })
            .catch(() => setState('none'));
    }, [ticker, months]);

    if (state === 'none' && months === 3) {
        // 有些公司只報 GAAP（AAPL、GOOGL、META…），這段就整個不出現
        return null;
    }

    const periods = data?.periods ?? [];

    return (
        <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
            <div className="flex items-center gap-2 flex-wrap mb-3">
                <h2 className="text-[15px] font-bold text-[var(--text-primary)] inline-flex items-center gap-2">
                    <Scale size={16} aria-hidden="true" /> GAAP vs 公司 Non-GAAP
                </h2>
                <span className="text-[11.5px] text-[var(--text-meta)]">
                    {data?.source || '財報新聞稿'}
                    {data?.filed && ` · ${data.filed} 申報`}
                </span>
                <div className="ml-auto flex gap-2 items-center">
                    {MONTHS.map((o) => (
                        <button key={o.v} onClick={() => setMonths(o.v)} aria-pressed={months === o.v}
                            className={`ui-focus tap cursor-pointer px-3 py-2 rounded-full text-[12px] font-medium border transition-colors duration-200 ${
                                months === o.v
                                    ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                    : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                            {o.label}
                        </button>
                    ))}
                    {data?.source_url && (
                        <a href={data.source_url} target="_blank" rel="noopener noreferrer"
                           className="ui-focus rounded text-[12px] text-[var(--text-secondary)]
                                      hover:text-[var(--accent-cyan-text)] inline-flex items-center gap-1">
                            看新聞稿 <ExternalLink size={11} aria-hidden="true" />
                        </a>
                    )}
                </div>
            </div>

            {state === 'loading' && (
                <div className="space-y-1" aria-busy="true">
                    {Array.from({ length: 5 }).map((_, i) => (
                        <div key={i} className="h-8 rounded bg-[var(--bg-tertiary)] animate-pulse opacity-70" />
                    ))}
                </div>
            )}

            {state === 'none' && (
                <div className="text-[12.5px] text-[var(--text-meta)] py-3">
                    這個期間長度沒有對帳資料
                </div>
            )}

            {state === 'ok' && data && (
                <>
                    <div className="overflow-auto max-h-[60vh]">
                        <table className="data-table w-full text-[12.5px] border-collapse">
                            <caption className="sr-only">{ticker} GAAP 與公司自訂 Non-GAAP 的對照</caption>
                            <thead>
                                <tr className="text-[var(--text-meta)]">
                                    <th scope="col" className="py-2 px-3 text-left font-medium sticky-col">指標</th>
                                    {periods.map((p) => (
                                        <th key={p} scope="col" title={p}
                                            className="num py-2 px-3 text-right font-medium whitespace-nowrap">
                                            {data.period_labels?.[p] || p}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {data.measures.map((m) => {
                                    const isOpen = open === m.label;
                                    return [
                                        <tr key={m.label} className="border-t border-[var(--glass-border)]">
                                            <th scope="row" className="sticky-col py-1.5 px-3 text-left font-normal">
                                                <button onClick={() => setOpen(isOpen ? null : m.label)}
                                                    aria-expanded={isOpen}
                                                    className="ui-focus cursor-pointer text-left text-[var(--text-primary)]
                                                               hover:text-[var(--accent-cyan-text)] transition-colors duration-200">
                                                    {isOpen ? <ChevronDown size={12} className="inline mr-1" />
                                                            : <ChevronRight size={12} className="inline mr-1" />}
                                                    {m.label}
                                                </button>
                                            </th>
                                            {periods.map((p) => {
                                                const v = m.values[p];
                                                if (!v) return <td key={p} className="py-1.5 px-3 text-right text-[var(--text-meta)]">—</td>;
                                                return (
                                                    <td key={p} className="num py-1.5 px-3 text-right whitespace-nowrap">
                                                        {/* 兩個數字疊著放：上面 Non-GAAP、下面 GAAP，
                                                            中間的差距才是這張表真正要看的東西 */}
                                                        <div className="text-[var(--text-primary)] font-medium">
                                                            {fmt(v.nongaap, m.unit)}
                                                        </div>
                                                        <div className="text-[11px] text-[var(--text-meta)]">
                                                            GAAP {fmt(v.gaap, m.unit)}
                                                        </div>
                                                    </td>
                                                );
                                            })}
                                        </tr>,
                                        isOpen && (
                                            <tr key={`${m.label}-adj`} className="border-t border-[var(--glass-border)]">
                                                <td colSpan={periods.length + 1} className="p-0">
                                                    <div className="px-3 py-2 bg-[var(--bg-tertiary)] text-[12px]">
                                                        <div className="text-[11px] text-[var(--text-meta)] mb-1">
                                                            調整項（{m.gaap_label} → {m.label}）
                                                        </div>
                                                        {(m.values[periods[0]]?.adjustments || []).map((a, i) => (
                                                            <div key={i} className="flex gap-3 py-0.5">
                                                                <span className="text-[var(--text-secondary)]">{a.label}</span>
                                                                <span className="num ml-auto text-[var(--text-primary)]">
                                                                    {fmt(a.value, 'amount')}
                                                                </span>
                                                            </div>
                                                        ))}
                                                        {m.values[periods[0]]?.residual != null
                                                            && Math.abs(m.values[periods[0]].residual) > 1 && (
                                                            // 對不平代表有調整項沒被解析到，講出來而不是藏著
                                                            <div className="mt-1 text-[11.5px] text-[var(--accent-amber-text)]">
                                                                未解析到的差額 {fmt(m.values[periods[0]].residual, 'amount')}
                                                            </div>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        ),
                                    ];
                                })}
                            </tbody>
                        </table>
                    </div>
                    <p className="text-[11.5px] text-[var(--text-meta)] mt-2">
                        Non-GAAP 由公司自行定義，各家剔除的項目不同 —— 適合看同一家公司的趨勢，不適合跨公司排序比較。
                    </p>
                </>
            )}
        </section>
    );
}
