import { useState, useEffect, useMemo } from 'react';
import { useSearch, useLocation } from 'wouter';
import axios from 'axios';
import { LayoutList } from 'lucide-react';
import DailyDigestPanel from '../components/DailyDigestPanel';

/**
 * 每日報告完整版。
 *
 * Portfolio 頁只掛總覽（`variant="summary"`），要逐檔對照表、兩萬字的盤後復盤、
 * 全部候選與全部新聞就來這一頁 —— 這裡不吃 Portfolio 的即時報價載入，秒開。
 */
export default function Briefing() {
  const search = useSearch();
  const [, navigate] = useLocation();
  const { tab, section } = useMemo(() => {
    const q = new URLSearchParams(search || '');
    let tab = q.get('tab') || undefined;
    let section = q.get('section') || undefined;
    // 舊連結：「AI 持倉」分頁與「市場個股 › 盤後復盤」子分頁都併進「持倉復盤」了
    if (tab === 'ai' || (tab === 'market' && section === 'warroom')) { tab = 'review'; section = undefined; }
    return { tab, section };
  }, [search]);

  // 復盤已經不是這一頁的分頁 —— 完整畫面在 /review（War Room 群的第二個切面）。
  // 舊書籤（?tab=review、/ai-report）直接送過去，不要停在一頁沒有那個分頁的畫面。
  useEffect(() => {
    if (tab === 'review') navigate('/review', { replace: true });
  }, [tab, navigate]);

  // AI 卡自帶的 weight_pct 是空的，持倉復盤對照表的權重欄會整欄「—」。
  // /api/portfolio/csv 很輕（~20KB、0.6s），這裡自己抓一份補上。
  const [weights, setWeights] = useState(null);
  useEffect(() => {
    let alive = true;
    axios.get('/api/portfolio/csv')
      .then(r => {
        if (!alive) return;
        // 日報涵蓋全部組合，所以權重用全帳戶總市值當分母（不是單一組合）
        const rows = Object.values(r.data?.portfolios || {})
          .flatMap(p => p.holdings || [])
          .filter(h => h.ticker && h.ticker !== 'CASH');
        const total = rows.reduce((n, h) => n + (h.market_value ?? h.cost_basis ?? 0), 0);
        if (!total) return;
        setWeights(Object.fromEntries(
          rows.map(h => [h.ticker, (h.market_value ?? h.cost_basis ?? 0) / total * 100]),
        ));
      })
      .catch(() => { /* 抓不到就讓權重欄留白，不擋日報 */ });
    return () => { alive = false; };
  }, []);

  return (
    <div className="container space-y-4 py-8 animate-fade-in">
      {/* 面板自己會列出四個分頁、也自己顯示日期，所以這裡只留頁名 —— 原本這段
          把「每日報告」寫了一次、分頁名字列了一次，下面 40px 處面板又各寫一次。 */}
      <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
        <LayoutList size={18} className="text-[var(--accent-cyan)]" aria-hidden="true" />
        每日報告
        <span className="text-sm font-normal text-[var(--text-meta)]">Daily Briefing</span>
      </h1>

      <DailyDigestPanel weights={weights} variant="full" initialTab={tab} initialSection={section} />
    </div>
  );
}
