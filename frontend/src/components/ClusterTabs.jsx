/**
 * ClusterTabs — 同一個「主題群」底下各頁之間的分頁列。
 *
 * 導覽列的「分析」下拉原本掛了 11 個項目，其中三組其實是同一件事的不同切面：
 *   市場（Macro／板塊 RS）、War Room（戰情／盤後復盤／Signals）、財報（行事曆／結論／論點）。
 * 下拉只留一個入口，同群的其他頁改成頁內分頁 —— 每個群一個入口、進去再切面，
 * 而不是把 11 個平行選項全攤在下拉裡讓人自己分類。
 *
 * 分頁是真的連結（wouter <Link>）不是 state，所以每一頁的網址、書籤、
 * 瀏覽器上一頁全部照舊 —— 這次改的是導覽，不是路由。
 *
 * theme='ops' 給 WarRoom / PostMarketReview 那套自帶暗色系的頁面用，
 * 其餘吃 app 主題變數的頁面用預設的 light。
 */
import { Link } from 'wouter';
import { C } from './opsTheme';

export default function ClusterTabs({ items, active, theme = 'light', className = '' }) {
  if (theme === 'ops') {
    return (
      <div
        className={`flex items-center gap-1 border p-1 ${className}`}
        style={{ borderColor: C.border, background: C.panel }}
        role="tablist"
      >
        {items.map((t) => {
          const Icon = t.icon;
          const on = t.href === active;
          return (
            <Link
              key={t.href}
              href={t.href}
              role="tab"
              aria-selected={on}
              className="inline-flex cursor-pointer items-center gap-1.5 whitespace-nowrap px-3 py-1.5 font-mono text-[12px] font-bold uppercase tracking-wider transition-colors duration-150"
              style={
                on
                  ? { background: 'rgba(34,211,238,0.12)', color: C.accent, boxShadow: `inset 0 0 0 1px ${C.accent}` }
                  : { color: C.muted }
              }
            >
              <Icon size={12} />
              {t.label}
            </Link>
          );
        })}
      </div>
    );
  }

  return (
    <div
      className={`inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-1 ${className}`}
      role="tablist"
    >
      {items.map((t) => {
        const Icon = t.icon;
        const on = t.href === active;
        return (
          <Link
            key={t.href}
            href={t.href}
            role="tab"
            aria-selected={on}
            className={`inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 ${
              on
                ? 'bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)]'
                : 'text-[var(--text-meta)] hover:text-[var(--text-primary)]'
            }`}
          >
            <Icon size={13} />
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}
