/**
 * OpsProse — ops-center 色系的 markdown 閱讀器（盤後復盤全文、戰情室的市場日報共用）。
 *
 * app 的 Markdown.jsx 吃 app 主題變數；War Room 家族用自己的 --ops-* 色票，
 * 兩邊在同一份 markdown 上的字色／表格線完全不同，所以這裡自己帶一套 components。
 * 原本住在 PostMarketReview.jsx 裡，戰情室也要讀日報全文後抽出來共用。
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkCjkFriendly from 'remark-cjk-friendly';
import { C, alpha } from './opsTheme';

const MD_DARK = {
  h1: (p) => <h1 className="mb-2 mt-4 text-2xl font-bold" style={{ color: C.text }} {...p} />,
  h2: (p) => <h2 className="mb-2.5 mt-7 border-b pb-1.5 text-[21px] font-semibold" style={{ color: C.accent, borderColor: C.border }} {...p} />,
  h3: (p) => <h3 className="mb-2 mt-5 text-[18.5px] font-semibold" style={{ color: C.text }} {...p} />,
  h4: (p) => <h4 className="mb-2 mt-6 border-l-2 pl-2.5 font-mono text-[18.5px] font-bold tracking-wide" style={{ color: C.text, borderColor: C.accent }} {...p} />,
  p: (p) => <p className="my-3 text-[18px] leading-[1.75]" style={{ color: C.sub }} {...p} />,
  ul: (p) => <ul className="my-3 list-disc space-y-2 pl-6 text-[18px] leading-[1.75]" style={{ color: C.sub }} {...p} />,
  ol: (p) => <ol className="my-3 list-decimal space-y-2 pl-6 text-[18px] leading-[1.75]" style={{ color: C.sub }} {...p} />,
  li: (p) => <li className="leading-relaxed" {...p} />,
  strong: (p) => <strong style={{ color: C.text }} {...p} />,
  a: (p) => <a className="underline decoration-dotted underline-offset-2" style={{ color: C.accent }} target="_blank" rel="noreferrer" {...p} />,
  blockquote: (p) => (
    <blockquote className="my-3.5 border-l-2 pl-3.5 text-[17px] leading-[1.75]" style={{ borderColor: C.accent, color: C.muted }} {...p} />
  ),
  code: ({ className, children, ...p }) => {
    const isBlock = /language-/.test(className || '') || String(children ?? '').includes('\n');
    return isBlock ? (
      <code className="my-2 block overflow-x-auto whitespace-pre rounded-lg p-3.5 text-[15.5px]" style={{ background: C.deep, color: C.sub }} {...p}>{children}</code>
    ) : (
      <code className="rounded px-1 py-0.5 text-[15.5px]" style={{ background: alpha(C.ink, 0.08), color: C.accent }} {...p}>{children}</code>
    );
  },
  table: (p) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-[16px]" {...p} /></div>,
  th: (p) => <th className="border px-2 py-1 text-left font-semibold" style={{ borderColor: C.border, background: alpha(C.ink, 0.04), color: C.text }} {...p} />,
  td: (p) => <td className="border px-2 py-1" style={{ borderColor: C.border, color: C.sub }} {...p} />,
  hr: () => <hr className="my-4" style={{ borderColor: C.border }} />,
};

// 「**結論：**現價」這種全形標點貼著 ** 的寫法，CommonMark 不認 → 靠 cjk-friendly 修
const REMARK = [remarkGfm, remarkCjkFriendly];

// 散文一行 65–75ch 最好讀 —— 限寬，不讓它橫跨整個面板。
export default function OpsProse({ children, maxWidth = 1080 }) {
  return (
    <div className="mx-auto" style={{ maxWidth }}>
      <ReactMarkdown remarkPlugins={REMARK} components={MD_DARK}>{children}</ReactMarkdown>
    </div>
  );
}
