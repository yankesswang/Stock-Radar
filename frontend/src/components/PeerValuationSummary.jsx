/**
 * PeerValuationSummary — 個股頁估值區的同業摘要卡。
 *
 * 完整比較（推算合理價、十個指標的對照表、逐檔明細）在 /stock/:ticker/peers。
 * 一個產業動輒二十幾檔同業，整套攤在個股頁上就是把頁面撐長兩倍還讀不完，
 * 所以這裡只放「一眼要知道的三件事」，其餘跳頁看：
 *
 *   1. 同業中位 P/E 推算的合理價，以及跟現價差多少
 *   2. 本檔在同業裡的三個名次（便宜／賺錢／貴的道理）
 *   3. 一個明確的入口
 *
 * 名次比百分位好懂，而且小樣本不會誇大 —— 「第 7 便宜／共 22 檔」講得清楚。
 */
import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import { Users, Loader2, ArrowRight, TrendingUp, TrendingDown } from 'lucide-react';
import { MIN_RANK_N, fmtNum, fmtMoney } from './peerFormat';

// 摘要只挑三個名次：貴不貴（P/E）、賺不賺（ROE）、營收貴不貴（P/S）
const HIGHLIGHT = [
  { key: 'pe', label: 'P/E', suffix: '便宜' },
  { key: 'roe', label: 'ROE', suffix: '高' },
  { key: 'ps', label: 'P/S', suffix: '便宜' },
];

function RankChip({ stat, suffix }) {
  if (!stat || stat.rank == null || stat.n < MIN_RANK_N) return null;
  const top = stat.rank <= Math.ceil(stat.rank_total / 3);
  const bottom = stat.rank > Math.ceil((stat.rank_total * 2) / 3);
  const tone = top
    ? 'var(--accent-success-text)'
    : bottom ? 'var(--accent-amber-text)' : 'var(--text-secondary)';
  return (
    <span className="inline-flex items-baseline gap-1 rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-2.5 py-1">
      <span className="text-[11px] font-semibold text-[var(--text-muted)]">{stat.label}</span>
      <span className="font-mono text-[12.5px] font-bold tabular-nums text-[var(--text-primary)]">
        {fmtNum(stat.target, stat.unit)}
      </span>
      <span className="text-[11px] text-[var(--text-muted)]">
        · 第 <b className="font-mono font-bold" style={{ color: tone }}>{stat.rank}</b> {suffix}／{stat.rank_total}
      </span>
    </span>
  );
}

export default function PeerValuationSummary({ ticker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!ticker) return;
    let alive = true;
    setLoading(true);
    axios.get(`/api/compare/peers/${ticker}`)
      .then(r => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [ticker]);

  const byKey = useMemo(
    () => Object.fromEntries((data?.stats || []).map(s => [s.key, s])),
    [data],
  );

  if (loading) {
    return (
      <div className="card flex items-center justify-center gap-2 !p-5 text-sm text-[var(--text-muted)]">
        <Loader2 size={16} className="animate-spin" /> 載入同業比較…
      </div>
    );
  }
  if (!data || data.peer_count <= 1) {
    return (
      <div className="card !p-5" id="peers">
        <h3 className="mb-1 flex items-center gap-2 text-base font-semibold text-[var(--text-secondary)]">
          <Users size={16} /> 同業比較
        </h3>
        <p className="text-sm text-[var(--text-muted)]">
          {data ? `${data.industry} 目前只有這一檔在追蹤，沒有同業可比。` : '抓不到同業資料。'}
        </p>
      </div>
    );
  }

  const im = data.implied || {};
  const cy = im.current_year;
  const under = cy?.diff_pct != null && cy.diff_pct < 0;

  return (
    <div className="card !p-5" id="peers">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div>
          <h3 className="flex flex-wrap items-baseline gap-x-2 text-base font-semibold text-[var(--text-secondary)]">
            <span className="inline-flex items-center gap-2"><Users size={16} /> 同業比較</span>
            <span className="text-xs font-normal text-[var(--text-muted)]">
              {data.industry} · {data.peer_count - 1} 檔同業
            </span>
          </h3>
          <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--text-meta)]">
            {cy?.fair_price != null ? (
              <>
                同業中位 P/E {im.peer_median_pe}x 推算合理價{' '}
                <b className="font-mono text-[15px] font-bold text-[var(--text-primary)]">
                  {fmtMoney(cy.fair_price)}
                </b>
                <span className="ml-1 inline-flex items-center gap-0.5 font-semibold"
                      style={{ color: under ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                  {under ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                  {under ? '低估' : '高估'} {Math.abs(cy.diff_pct)}%
                </span>
                <span className="text-[var(--text-muted)]">
                  （本檔自己 5 年中位 PE 推 {fmtMoney(im.own_fair_price)}）
                </span>
              </>
            ) : (
              <>同業沒有足夠的 P/E 可以推算合理價</>
            )}
          </p>
        </div>

        <Link
          href={`/stock/${ticker}/peers`}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-[rgba(0,82,255,0.25)] bg-[rgba(0,82,255,0.08)] px-3.5 py-2 text-[13px] font-semibold text-[var(--accent-cyan)] transition-colors hover:bg-[rgba(0,82,255,0.14)]"
        >
          完整同業比較 <ArrowRight size={13} />
        </Link>
      </div>

      {/* 三個名次 —— 貴不貴、賺不賺、營收貴不貴，看得出「貴有沒有貴的道理」 */}
      <div className="mt-3 flex flex-wrap gap-2">
        {HIGHLIGHT.map(h => <RankChip key={h.key} stat={byKey[h.key]} suffix={h.suffix} />)}
      </div>
    </div>
  );
}
