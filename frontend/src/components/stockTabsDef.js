/**
 * 個股頁的分頁定義 —— 純資料，不放元件（同 clusterTabsDef.js 的理由：
 * 元件檔一旦同時匯出常數，vite 的 fast refresh 會整檔重載）。
 *
 * 個股主頁原本把 12 顆藥丸平鋪在標頭，而且只有主頁看得到；11 個子頁各自只有
 * 一條「Back to」。這裡把切面依「結論 → 估值 → 基本面 → 市場 → 觀點」分成五群，
 * 由 StockPageHeader 畫成兩層分頁列，每一頁都掛同一條，切面之間直接互通。
 *
 * path 接在 /stock/:ticker 後面；'' 是主頁本身。
 * 「新聞」是主頁底部的區塊錨點，不是路由 —— anchor: true 讓分頁列用不同方式導過去。
 *
 * prefetch 指向各頁的 lazy 模組：路徑跟 App.jsx 的 lazy() 一樣，vite 會合成同一個
 * chunk，滑到分頁上就先把程式碼載回來，點下去不用等白屏。
 */
import {
  LayoutDashboard, Scale, Landmark, CandlestickChart, MessageSquareText,
  DollarSign, Activity, Target, Users,
  Sheet, Ratio, TableProperties, BarChart3, PieChart,
  Newspaper, Sparkles, FileText,
} from 'lucide-react';

export const STOCK_TAB_GROUPS = [
  {
    key: 'overview', label: '總覽', icon: LayoutDashboard,
    tabs: [
      { key: 'overview', label: '總覽', icon: LayoutDashboard, path: '', prefetch: () => import('../pages/StockDetail') },
    ],
  },
  {
    key: 'valuation', label: '估值', icon: Scale,
    tabs: [
      { key: 'dcf',      label: 'DCF',      icon: DollarSign, path: '/dcf',      prefetch: () => import('../pages/StockDCF') },
      { key: 'pe',       label: 'PE 歷史',  icon: Activity,   path: '/pe',       prefetch: () => import('../pages/StockPE') },
      { key: 'forecast', label: '分析師預估', icon: Target,   path: '/forecast', prefetch: () => import('../pages/StockForecast') },
      { key: 'peers',    label: '同業比較', icon: Users,      path: '/peers',    prefetch: () => import('../pages/StockPeers') },
    ],
  },
  {
    key: 'fundamentals', label: '基本面', icon: Landmark,
    tabs: [
      { key: 'financials', label: '三大財報', icon: Sheet,           path: '/financials', prefetch: () => import('../pages/StockFinancials') },
      { key: 'ratios',     label: '財務比率', icon: Ratio,           path: '/ratios',     prefetch: () => import('../pages/StockRatios') },
      { key: 'statistics', label: '統計數據', icon: TableProperties, path: '/statistics', prefetch: () => import('../pages/StockStatistics') },
      { key: 'metrics',    label: '營運分項', icon: BarChart3,       path: '/metrics',    prefetch: () => import('../pages/StockMetrics') },
      { key: 'segments',   label: '分部門',   icon: PieChart,        path: '/segments',   prefetch: () => import('../pages/StockSegments') },
    ],
  },
  {
    key: 'market', label: '市場', icon: CandlestickChart,
    tabs: [
      { key: 'options', label: '選擇權 IV', icon: CandlestickChart, path: '/options', prefetch: () => import('../pages/StockOptions') },
      { key: 'news',    label: '新聞',      icon: Newspaper,        path: '#news', anchor: true },
    ],
  },
  {
    key: 'views', label: '觀點', icon: MessageSquareText,
    tabs: [
      { key: 'ai',    label: 'AI 分析',  icon: Sparkles, path: '/ai',             prefetch: () => import('../pages/StockAIAnalysis') },
      // 財報筆記頁在 App.jsx 是靜態 import，沒有 chunk 可預載
      { key: 'notes', label: '財報筆記', icon: FileText, path: '/earnings-notes' },
    ],
  },
];

export const STOCK_TABS = STOCK_TAB_GROUPS.flatMap((g) => g.tabs.map((t) => ({ ...t, group: g.key })));

export const stockTabHref = (ticker, tab) => `/stock/${ticker}${tab.path}`;

export const findStockTab = (key) => STOCK_TABS.find((t) => t.key === key) ?? null;

/**
 * 從目前網址（wouter 的 location，不含 base）推出 active 的分頁 key。
 * 認不得的路徑回 null，分頁列就全部不亮。
 */
export function activeStockTabKey(location) {
  const m = /^\/stock\/[^/]+(?:\/([^/?#]+))?/.exec(location || '');
  if (!m) return null;
  const seg = m[1] ?? '';
  if (seg === '') return 'overview';
  if (seg === 'earnings-notes') return 'notes';
  return STOCK_TABS.some((t) => t.key === seg) ? seg : null;
}
