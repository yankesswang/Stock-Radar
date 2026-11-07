/**
 * OpsDatePicker — ops 暗色系的月曆日期選擇（盤後復盤用）。
 *
 * 原本是 <select> 下拉列 60 幾個 ISO 日期，看不出「哪幾天有資料、哪幾天空著」；
 * 月曆一眼就看得到週末／缺日，也能直接跳月份。
 *
 * - `available` 是有資料的日期（YYYY-MM-DD）清單；不在裡面的日子畫成灰、不可點。
 * - 不依賴任何日期套件；月曆用本地時間算，避免 UTC 偏一天。
 * - Esc 或點外面關閉；按鈕 aria-expanded、日期格 aria-pressed／aria-disabled。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { C, alpha } from './opsTheme';

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];
const pad = (n) => String(n).padStart(2, '0');
const iso = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
const todayIso = () => {
  const t = new Date();
  return iso(t.getFullYear(), t.getMonth(), t.getDate());
};

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push(null);
  for (let d = 1; d <= daysInMonth; d += 1) cells.push(d);
  while (cells.length % 7) cells.push(null);
  return cells;
}

export default function OpsDatePicker({ value, available = [], onChange, className = '' }) {
  const [open, setOpen] = useState(false);
  const avail = useMemo(() => new Set(available), [available]);
  const [cursor, setCursor] = useState(() => {
    const src = value || available[0] || todayIso();
    return { y: Number(src.slice(0, 4)), m: Number(src.slice(5, 7)) - 1 };
  });
  const wrapRef = useRef(null);

  // 開啟時把月曆定位到目前選的那個月（在事件裡做，不用 effect 追 state）。
  const toggle = () => {
    if (!open && value) setCursor({ y: Number(value.slice(0, 4)), m: Number(value.slice(5, 7)) - 1 });
    setOpen((v) => !v);
  };

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // 有資料的月份範圍，超出就不給翻，免得翻到一片灰。
  const bounds = useMemo(() => {
    if (!available.length) return null;
    const sorted = [...available].sort();
    return { min: sorted[0].slice(0, 7), max: sorted[sorted.length - 1].slice(0, 7) };
  }, [available]);
  const cursorKey = `${cursor.y}-${pad(cursor.m + 1)}`;
  const canPrev = !bounds || cursorKey > bounds.min;
  const canNext = !bounds || cursorKey < bounds.max;
  const shift = (delta) => setCursor(({ y, m }) => {
    const d = new Date(y, m + delta, 1);
    return { y: d.getFullYear(), m: d.getMonth() };
  });

  const cells = monthGrid(cursor.y, cursor.m);
  const today = todayIso();
  const monthCount = available.filter((d) => d.startsWith(cursorKey)).length;

  return (
    <div ref={wrapRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex cursor-pointer items-center gap-2 border px-2.5 py-1.5 font-mono text-[12.5px] font-semibold tabular-nums transition-colors duration-200 hover:bg-[var(--ops-deep)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--ops-accent)]"
        style={{ borderColor: open ? C.accent : C.border, color: C.text, background: C.panel }}
        title="選擇復盤日期"
      >
        <CalendarDays size={13} style={{ color: C.accent }} />
        {value || '—'}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="選擇復盤日期"
          className="absolute right-0 z-40 mt-1 w-[272px] border p-3 shadow-2xl"
          style={{ background: C.panel, borderColor: C.border, boxShadow: `0 12px 32px ${C.shadow}, inset 0 0 24px ${alpha(C.accent, 0.03)}` }}
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              onClick={() => shift(-1)}
              disabled={!canPrev}
              className="cursor-pointer border p-1 transition-colors duration-150 hover:bg-[var(--ops-deep)] disabled:cursor-not-allowed disabled:opacity-30"
              style={{ borderColor: C.border, color: C.sub }}
              aria-label="上個月"
            >
              <ChevronLeft size={13} />
            </button>
            <div className="text-center">
              <div className="font-mono text-[13px] font-bold tabular-nums" style={{ color: C.text }}>
                {cursor.y} / {pad(cursor.m + 1)}
              </div>
              <div className="font-mono text-[10px]" style={{ color: C.muted }}>
                {monthCount} 天有復盤
              </div>
            </div>
            <button
              type="button"
              onClick={() => shift(1)}
              disabled={!canNext}
              className="cursor-pointer border p-1 transition-colors duration-150 hover:bg-[var(--ops-deep)] disabled:cursor-not-allowed disabled:opacity-30"
              style={{ borderColor: C.border, color: C.sub }}
              aria-label="下個月"
            >
              <ChevronRight size={13} />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-y-0.5 text-center">
            {WEEKDAYS.map((w, i) => (
              <div
                key={w}
                className="py-1 font-mono text-[10px] uppercase tracking-wider"
                style={{ color: i === 0 || i === 6 ? C.muted : C.dim }}
              >
                {w}
              </div>
            ))}
            {cells.map((d, i) => {
              if (d == null) return <div key={`e${i}`} />;
              const key = iso(cursor.y, cursor.m, d);
              const has = avail.has(key);
              const selected = key === value;
              const isToday = key === today;
              return (
                <button
                  key={key}
                  type="button"
                  disabled={!has}
                  aria-pressed={selected}
                  aria-disabled={!has}
                  onClick={() => { onChange(key); setOpen(false); }}
                  title={has ? `${key} 的復盤` : `${key} 沒有復盤資料`}
                  className="mx-auto flex h-8 w-8 items-center justify-center font-mono text-[12px] tabular-nums transition-colors duration-150 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--ops-accent)] disabled:cursor-not-allowed"
                  style={{
                    color: selected ? C.bg : has ? C.text : C.muted,
                    background: selected ? C.accent : 'transparent',
                    opacity: has ? 1 : 0.35,
                    boxShadow: isToday && !selected ? `inset 0 0 0 1px ${C.accent}` : 'none',
                    cursor: has ? 'pointer' : 'not-allowed',
                    fontWeight: selected || isToday ? 700 : 500,
                  }}
                  onMouseEnter={(e) => { if (has && !selected) e.currentTarget.style.background = `${alpha(C.accent, 0.133)}`; }}
                  onMouseLeave={(e) => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
                >
                  {d}
                </button>
              );
            })}
          </div>

          <div className="mt-2 flex items-center justify-between border-t pt-2 font-mono text-[10px]" style={{ borderColor: C.border, color: C.muted }}>
            <span>
              <span className="mr-1 inline-block h-2 w-2 align-middle" style={{ background: C.accent }} /> 已選
              <span className="ml-3 mr-1 inline-block h-2 w-2 align-middle" style={{ boxShadow: `inset 0 0 0 1px ${C.accent}` }} /> 今天
            </span>
            {available[0] && available[0] !== value && (
              <button
                type="button"
                className="cursor-pointer font-bold uppercase tracking-wider hover:underline"
                style={{ color: C.accent }}
                onClick={() => { onChange(available[0]); setOpen(false); }}
              >
                最新
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
