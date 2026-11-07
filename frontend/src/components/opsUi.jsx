// Ops-center 暗色系的基礎元件（角標面板、徽章、篩選晶片、空狀態）。
// 只放元件；常數與工具在 opsTheme.js。
import { C, alpha } from './opsTheme';

export function Corner({ pos }) {
  const map = {
    tl: 'left-0 top-0 border-l border-t',
    tr: 'right-0 top-0 border-r border-t',
    bl: 'left-0 bottom-0 border-l border-b',
    br: 'right-0 bottom-0 border-r border-b',
  };
  return (
    <span
      className={`pointer-events-none absolute h-3 w-3 ${map[pos]}`}
      style={{ borderColor: C.accent, opacity: 0.55 }}
    />
  );
}

export function Panel({ label, status, children, className = '' }) {
  return (
    <section
      className={`relative border p-4 ${className}`}
      style={{ background: C.panel, borderColor: C.border, boxShadow: `inset 0 0 24px ${alpha(C.accent, 0.03)}` }}
    >
      <Corner pos="tl" /><Corner pos="tr" /><Corner pos="bl" /><Corner pos="br" />
      {label && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span className="font-mono uppercase" style={{ color: C.muted, fontSize: 12, letterSpacing: '0.14em' }}>
            {label}
          </span>
          {status}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ color, children }) {
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 font-mono text-[14px] font-semibold"
      style={{ color, background: `${alpha(color, 0.102)}`, border: `1px solid ${alpha(color, 0.333)}` }}
    >
      {children}
    </span>
  );
}

export function Empty({ children }) {
  return <div className="py-8 text-center font-mono text-xs" style={{ color: C.muted }}>{children}</div>;
}

// 可切換的篩選晶片 —— 決策全表的組合篩選、異常持倉的判定／組合篩選共用。
export function FilterChip({ active, color, children, onClick, title }) {
  const c = color || C.accent;
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className="inline-flex cursor-pointer items-center gap-1.5 border px-3 py-1.5 font-mono text-[14px] font-bold uppercase transition-colors duration-200"
      style={{
        borderColor: active ? c : C.border,
        color: active ? c : C.muted,
        background: active ? `${alpha(c, 0.071)}` : 'transparent',
      }}
    >
      {children}
    </button>
  );
}
