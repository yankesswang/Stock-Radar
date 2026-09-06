/**
 * 各「主題群」底下有哪幾個切面 —— 純資料，不放元件（同 opsTheme.js 的理由：
 * 元件檔一旦同時匯出常數，vite 的 fast refresh 會整檔重載）。
 *
 * 導覽列的「分析」下拉每個群只放一個入口，群內切面由 ClusterTabs 畫成頁內分頁。
 */
import {
  Activity, Layers, Radar, ClipboardCheck, CalendarDays, FileText, Target,
} from 'lucide-react';

// 市場脈絡：由上而下的第一層，看的是「今天的天氣」
export const MARKET_TABS = [
  { href: '/macro', label: 'Macro', icon: Activity },
  { href: '/sectors', label: '板塊 RS', icon: Layers },
];

// 戰情：決策層 + 它的證據層
export const OPS_TABS = [
  { href: '/war-room', label: '戰情室', icon: Radar },
  { href: '/review', label: '盤後復盤', icon: ClipboardCheck },
  { href: '/signals', label: 'Signals', icon: Radar },
];

// 財報：時間軸（誰要公布）→ 結論（我決定怎麼做）→ 論點（下季拿什麼對帳）
export const EARNINGS_TABS = [
  { href: '/earnings', label: '行事曆', icon: CalendarDays },
  { href: '/earnings-conclusions', label: '結論', icon: FileText },
  { href: '/earnings-theses', label: '論點追蹤', icon: Target },
];
