/**
 * Color-coded DCF sensitivity matrix.
 *
 * Props:
 *   matrix      — { wacc_labels, tg_labels, matrix[][], base_wacc_idx, base_tg_idx }
 *   currentPrice — number | null
 *   corner       — corner-cell caption; the same grid is reused for the
 *                  margin-vs-growth matrix, whose axes are not WACC and growth
 */
export default function SensitivityMatrix({ matrix, currentPrice, corner = 'WACC \\ Growth' }) {
    if (!matrix || !matrix.matrix?.length) return null;

    const { wacc_labels, tg_labels, matrix: grid, base_wacc_idx, base_tg_idx } = matrix;

    const cellColor = (val) => {
        if (currentPrice == null) return {};
        const diff = (val / currentPrice - 1) * 100;
        if (diff > 0) {
            const intensity = Math.min(diff / 60, 1);
            return { background: `rgba(52, 211, 153, ${0.08 + intensity * 0.22})` };
        } else {
            const intensity = Math.min(Math.abs(diff) / 60, 1);
            return { background: `rgba(248, 113, 113, ${0.08 + intensity * 0.22})` };
        }
    };

    const isBase = (ri, ci) => ri === base_wacc_idx && ci === base_tg_idx;

    return (
        <div className="metrics-section">
            <div className="overflow-x-auto styled-scrollbar">
                <table className="metrics-table">
                    <thead>
                        <tr>
                            <th className="metrics-th-sticky" style={{ minWidth: '100px' }}>
                                <span className="text-[11px]">{corner}</span>
                            </th>
                            {tg_labels.map((tg) => (
                                <th key={tg} className="metrics-th">{tg}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {grid.map((row, ri) => (
                            <tr key={ri} className="metrics-tr">
                                <td className="metrics-td-sticky">
                                    <span className="text-[13px] font-semibold text-[var(--text-primary)]">
                                        {wacc_labels[ri]}
                                    </span>
                                </td>
                                {row.map((val, ci) => {
                                    const base = isBase(ri, ci);
                                    if (val == null) {
                                        return <td key={ci} className="metrics-td text-[var(--text-muted)]">&mdash;</td>;
                                    }
                                    return (
                                        <td
                                            key={ci}
                                            className="metrics-td"
                                            style={{
                                                ...cellColor(val),
                                                ...(base
                                                    ? { outline: '2px solid var(--accent-cyan)', outlineOffset: '-2px', borderRadius: '4px' }
                                                    : {}),
                                            }}
                                        >
                                            <span className={`font-mono tabular-nums ${base ? 'font-bold text-[var(--accent-cyan)]' : ''}`}>
                                                ${val.toFixed(2)}
                                            </span>
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
