import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from '../lib/apiClient';
import { PieChart, ExternalLink } from 'lucide-react';

// 分部門營收，資料取自 SEC 申報而不是爬蟲 CSV。
// 兩邊都有分部門的 22 檔裡，CSV 一律只有 8 期，SEC 中位數 37 期——
// 而且 CSV 那 8 期在期間軸的第 18 欄之後，預設視窗根本看不到。
const MONTHS = [{ v: 3, label: '單季' }, { v: 12, label: '全年' }];

function money(v) {
    if (v == null) return '—';
    const a = Math.abs(v);
    const sign = v < 0 ? '-' : '';
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
    return `${sign}$${a.toFixed(0)}`;
}

export default function SecSegmentRevenue({ ticker }) {
    const [months, setMonths] = useState(3);
    const [axisIdx, setAxisIdx] = useState(0);
    const [cols, setCols]   = useState(8);
    const [data, setData]   = useState(null);
    const [state, setState] = useState('loading');

    useEffect(() => {
        if (!ticker) return;
        setState('loading');
        axios.get(`/api/segments/${ticker}`, { params: { months } })
            .then(({ data: d }) => {
                setData(d);
                setAxisIdx(0);
                setState(d.axes?.length ? 'ok' : 'none');
            })
            // 503 是背景排程佔著 DB 的暫時性錯誤，不能跟「這檔沒有分部門資料」
            // 一樣整塊收掉——那正是「分部門營收突然不見了」的來源
            .catch((e) => setState(e?.response?.status === 503 ? 'busy' : 'none'));
    }, [ticker, months]);

    const axis = data?.axes?.[axisIdx] ?? null;
    const table = useMemo(() => {
        if (!axis) return null;
        const periods = axis.periods.slice(0, cols);
        const byKey = {};
        const seen = [];
        periods.forEach((p) => p.segments.forEach((s) => {
            byKey[`${p.period_end}|${s.segment}`] = s;
            if (!s.is_total && s.segment !== '（合併）' && !seen.includes(s.segment)) seen.push(s.segment);
        }));
        const latest = periods[0]?.period_end;
        // 依最新一期營收排序，第一列就是最大的部門
        seen.sort((a, b) => (byKey[`${latest}|${b}`]?.revenue ?? -Infinity)
                          - (byKey[`${latest}|${a}`]?.revenue ?? -Infinity));
        return { periods, names: seen, byKey };
    }, [axis, cols]);

    if (state === 'none') return null;

    return (
        <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
            <div className="flex items-center gap-2 flex-wrap mb-3">
                <h2 className="text-[15px] font-bold text-[var(--text-primary)] inline-flex items-center gap-2">
                    <PieChart size={16} aria-hidden="true" /> 分部門營收
                </h2>
                <span className="text-[11.5px] text-[var(--text-meta)]">
                    來源：SEC 申報{axis ? ` · ${axis.periods.length} 期` : ''}
                </span>
                <div className="ml-auto flex gap-2 items-center flex-wrap">
                    {MONTHS.map((o) => (
                        <button key={o.v} onClick={() => setMonths(o.v)} aria-pressed={months === o.v}
                            className={`ui-focus tap cursor-pointer px-3 py-2 rounded-full text-[12px] font-medium border transition-colors duration-200 ${
                                months === o.v
                                    ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                    : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                            {o.label}
                        </button>
                    ))}
                    <select value={cols} onChange={(e) => setCols(Number(e.target.value))}
                            aria-label="顯示幾個期間"
                            className="ui-focus tap num rounded-lg border border-[var(--glass-border)]
                                       bg-[var(--bg-secondary)] px-2 py-1.5 text-[12px] cursor-pointer
                                       text-[var(--text-primary)]">
                        {[4, 8, 12, 20].map((n) => <option key={n} value={n}>{n} 期</option>)}
                    </select>
                    <Link href={`/stock/${ticker}/segments`}
                          className="ui-focus rounded text-[12px] text-[var(--text-secondary)]
                                     hover:text-[var(--accent-cyan-text)] inline-flex items-center gap-1">
                        完整分部門 <ExternalLink size={11} aria-hidden="true" />
                    </Link>
                </div>
            </div>

            {/* 一家公司常有好幾種拆法（產品別、地區別），各自加總都等於總營收，分開選 */}
            {data?.axes?.length > 1 && (
                <div className="flex gap-1.5 flex-wrap mb-3">
                    {data.axes.slice(0, 5).map((a, i) => (
                        <button key={a.report_name} onClick={() => setAxisIdx(i)} aria-pressed={axisIdx === i}
                            className={`ui-focus tap cursor-pointer px-2.5 py-2 rounded-lg text-[11.5px] border transition-colors duration-200 ${
                                axisIdx === i
                                    ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.08)] border-[rgba(0,82,255,0.35)]'
                                    : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                            {a.report_name.replace(/\s*\(Detail(s)?\)\s*$/i, '').slice(0, 38)}
                            <span className="opacity-60"> · {a.real_segment_count}</span>
                        </button>
                    ))}
                </div>
            )}

            {state === 'busy' && (
                <p className="text-[12.5px] text-[var(--text-secondary)] py-6 text-center">
                    資料庫正在更新中，稍後重新整理即可。
                </p>
            )}

            {state === 'loading' && (
                <div className="space-y-1" aria-busy="true">
                    {Array.from({ length: 6 }).map((_, i) => (
                        <div key={i} className="h-7 rounded bg-[var(--bg-tertiary)] animate-pulse opacity-70" />
                    ))}
                </div>
            )}

            {state === 'ok' && table && (
                <div className="overflow-auto max-h-[50vh]">
                    <table className="data-table w-full text-[12.5px] border-collapse">
                        <caption className="sr-only">{ticker} 各部門營收，依期間排列</caption>
                        <thead>
                            <tr className="text-[var(--text-meta)]">
                                <th scope="col" className="py-2 px-3 text-left font-medium sticky-col">部門</th>
                                {table.periods.map((p) => (
                                    <th key={p.period_end} scope="col"
                                        title={`${p.period_end}${p.fiscal_label ? `　會計季 ${p.fiscal_label}` : ''}`}
                                        className="num py-2 px-3 text-right font-medium whitespace-nowrap">
                                        {p.label || p.period_end}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {table.names.map((name) => (
                                <tr key={name} className="border-t border-[var(--glass-border)]">
                                    <th scope="row" className="sticky-col py-1.5 px-3 text-left font-normal text-[var(--text-primary)]">
                                        {name}
                                    </th>
                                    {table.periods.map((p) => (
                                        <td key={p.period_end}
                                            className="num py-1.5 px-3 text-right whitespace-nowrap text-[var(--text-primary)]">
                                            {money(table.byKey[`${p.period_end}|${name}`]?.revenue)}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}
