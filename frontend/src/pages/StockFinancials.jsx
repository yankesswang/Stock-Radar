/**
 * 三大財報 —— SEC 申報的損益表／資產負債表／現金流量表、由它們推算的基本面指標、
 * 公司自訂的 Non-GAAP 對帳、SEC 給不了的補充欄位。
 *
 * 以前這些跟 stockanalysis.com 爬來的「營運分項拆解」塞在同一頁（/metrics），
 * 分頁列上「三大財報」與「營運分項」兩顆按鈕其實是同一頁的兩個捲動位置。
 * 現在拆開：這頁只放 SEC／新聞稿來源的報表，/metrics 只放營運分項。
 * SecFinancials 自己把切面記在 ?tab=income|balance|cashflow|metrics，舊書籤照舊。
 */
import { useRoute } from 'wouter';
import { FileQuestion } from 'lucide-react';
import StockPageHeader from '../components/StockPageHeader';
import SecFinancials from '../components/SecFinancials';
import SecSupplement from '../components/SecSupplement';
import NonGaapPanel from '../components/NonGaapPanel';
import { useStockTabs } from '../components/useStockHeader';

export default function StockFinancials() {
    const [, params] = useRoute('/stock/:ticker/financials');
    const ticker = params?.ticker?.toUpperCase();
    const tabsInfo = useStockTabs(ticker);
    const fin = tabsInfo?.tabs?.financials;

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            <StockPageHeader
                ticker={ticker}
                meta={fin?.note ? <>{fin.note} · 來源 SEC 申報＋XBRL companyfacts</> : '來源 SEC 申報＋XBRL companyfacts'}
            />

            {fin?.available === false ? (
                <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-6 max-w-2xl">
                    <h2 className="text-[15px] font-bold inline-flex items-center gap-2 mb-2">
                        <FileQuestion size={16} aria-hidden="true" /> {ticker} 還沒有 SEC 財報資料
                    </h2>
                    <p className="text-[13.5px] text-[var(--text-body)] leading-relaxed">
                        三大表由 <code className="font-mono text-[12.5px]">scripts/earnings/fetch_financials.py</code> 從 SEC
                        申報抓進本機資料庫，掛在每日 04:00 的補抓排程上；不在美國申報的公司（ADR 之外的外國股）
                        不會有。財務比率與統計數據走另一條爬蟲，不受影響。
                    </p>
                </section>
            ) : (
                <>
                    <SecFinancials ticker={ticker} />
                    {/* 公司自訂的 Non-GAAP：只有新聞稿裡有，SEC 與 stockanalysis 都給不了 */}
                    <NonGaapPanel ticker={ticker} />
                    <SecSupplement ticker={ticker} />
                </>
            )}
        </div>
    );
}
