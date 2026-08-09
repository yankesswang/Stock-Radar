import { Component } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// 防止單一筆內容 render 失敗就讓整頁白屏（App 無全域 error boundary）
class MarkdownBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err) { console.error('[Markdown] 渲染失敗:', err); }
  render() {
    if (this.state.failed) {
      return (
        <div className="text-[var(--accent-danger)] text-sm">
          ⚠️ 內容渲染失敗。
          <pre className="mt-2 whitespace-pre-wrap text-[var(--text-muted)] text-xs">{this.props.raw}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

// 暗色主題的 markdown 元件樣式（無 typography plugin）
const COMPONENTS = {
  h1: (p) => <h1 className="text-lg font-bold mt-4 mb-2 text-[var(--text-primary)]" {...p} />,
  h2: (p) => <h2 className="text-base font-semibold mt-4 mb-1.5 text-[var(--text-primary)]" {...p} />,
  h3: (p) => <h3 className="text-sm font-semibold mt-3 mb-1 text-[var(--text-primary)]" {...p} />,
  p:  (p) => <p className="my-2 leading-relaxed text-[var(--text-secondary)]" {...p} />,
  ul: (p) => <ul className="list-disc pl-5 my-2 space-y-1 text-[var(--text-secondary)]" {...p} />,
  ol: (p) => <ol className="list-decimal pl-5 my-2 space-y-1 text-[var(--text-secondary)]" {...p} />,
  li: (p) => <li className="leading-relaxed" {...p} />,
  a:  (p) => <a className="text-[var(--accent-cyan)] underline" target="_blank" rel="noreferrer" {...p} />,
  blockquote: (p) => <blockquote className="border-l-2 border-[var(--accent-cyan)] pl-3 my-2 text-[var(--text-muted)]" {...p} />,
  // react-markdown v10 移除了 inline prop → 改用「有換行或 language- 才算區塊」判斷，避免行內 code 變大灰塊
  code: ({ className, children, ...p }) => {
    const isBlock = /language-/.test(className || '') || String(children ?? '').includes('\n');
    return isBlock
      ? <code className="block p-3 my-2 rounded-lg bg-black/40 text-[13px] overflow-x-auto whitespace-pre" {...p}>{children}</code>
      : <code className="px-1 py-0.5 rounded bg-white/10 text-[var(--accent-cyan)] text-[13px]" {...p}>{children}</code>;
  },
  table: (p) => <div className="overflow-x-auto my-3"><table className="w-full text-sm border-collapse" {...p} /></div>,
  th: (p) => <th className="border border-white/15 px-2 py-1 bg-white/5 text-left font-semibold" {...p} />,
  td: (p) => <td className="border border-white/10 px-2 py-1" {...p} />,
  hr: () => <hr className="my-4 border-white/10" />,
  // 財報筆記的官方簡報截圖多為淺底寬圖，給白底避免暗色主題下看不清
  img: ({ alt, ...p }) => (
    <img
      alt={alt || ''}
      loading="lazy"
      className="my-3 w-full rounded-lg border border-white/10 bg-white"
      {...p}
    />
  ),
};

const CALLOUT_EMOJI = {
  tip: '💡', warning: '⚠️', info: 'ℹ️', note: '📝', danger: '🚨',
  quote: '❝', question: '❓', success: '✅', example: '📌', abstract: '📋',
};

// 清理：去 YAML frontmatter、裸 HTML 標籤（如 <a id="item-N"></a> 錨點）、Obsidian callout → emoji 引用
function clean(md) {
  if (!md) return '';
  let body = md.replace(/^---\n[\s\S]*?\n---\n*/, '');
  body = body.replace(/<\/?[a-zA-Z][^>]*>/g, ''); // 移除裸 HTML 標籤（保留 <50% 這類比較符號）
  body = body.replace(/^>\s*\[!(\w+)\]\s*(.*)$/gim,
    (_m, type, title) => `> ${CALLOUT_EMOJI[type.toLowerCase()] || '📌'} **${(title || type).trim()}**`);
  return body.trim();
}

export default function Markdown({ children }) {
  const cleaned = clean(children);
  return (
    <MarkdownBoundary raw={cleaned}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {cleaned}
      </ReactMarkdown>
    </MarkdownBoundary>
  );
}
