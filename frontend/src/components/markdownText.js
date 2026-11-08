// 每日報告／財報筆記的 markdown 前處理與錨點規則。
// 抽成獨立模組是因為 Markdown.jsx 一旦同時 export 元件與工具函式，
// vite 的 fast refresh 就會整檔重載（react-refresh/only-export-components）。

// ReportReader 的目錄要靠 id 跳，slug 規則兩邊必須一致 → 從這裡 export。
export function headingId(text) {
  const slug = String(text)
    .trim()
    .replace(/[#*`]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\p{L}\p{N}-]/gu, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60)
    .toLowerCase();
  return `h-${slug}`;
}


const CALLOUT_EMOJI = {
  tip: '💡', warning: '⚠️', info: 'ℹ️', note: '📝', danger: '🚨',
  quote: '❝', question: '❓', success: '✅', example: '📌', abstract: '📋',
};

// 清理：去 YAML frontmatter、裸 HTML 標籤（如 <a id="item-N"></a> 錨點）、Obsidian callout → emoji 引用
export function clean(md) {
  if (!md) return '';
  let body = md.replace(/^---\n[\s\S]*?\n---\n*/, '');
  body = body.replace(/<\/?[a-zA-Z][^>]*>/g, ''); // 移除裸 HTML 標籤（保留 <50% 這類比較符號）
  body = body.replace(/^>\s*\[!(\w+)\]\s*(.*)$/gim,
    (_m, type, title) => `> ${CALLOUT_EMOJI[type.toLowerCase()] || '📌'} **${(title || type).trim()}**`);
  // 「**市場狀態**：mixed\n**資料品質**：22/22 檔…」在 markdown 裡是同一段，會被接成
  // 一整行讀不斷。兩個獨立事實 → 補成硬換行，各佔一行。
  body = body.replace(/(\S)\n(?=\*\*[^*\n]{1,16}\*\*[：:])/g, '$1  \n');
  // 全形空格排版（「交易日\u3000：」）在比例字型下歪掉，程式碼區塊外一律收成一格
  body = body.split(/(```[\s\S]*?```)/).map((seg, i) =>
    i % 2 ? seg : seg.replace(/\u3000{2,}/g, '\u3000')).join('');
  return body.trim();
}
