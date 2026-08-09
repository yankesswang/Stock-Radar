import { useState, useEffect, useRef, useCallback } from 'react';
import { Check, ChevronDown, Loader2 } from 'lucide-react';

/**
 * GroupSelect — a compact, themed listbox for reassigning a ticker's sector/group.
 *
 * Custom (not native <select>) so it can match the dark glass theme, animate open,
 * and give a clear current-selection affordance. Keyboard + a11y supported.
 *
 * Props:
 *   value     - currently selected option (string)
 *   options   - string[] of choices (current value is ensured to be present)
 *   onChange  - (next: string) => void   (only fired when next !== value)
 *   loading   - show spinner + disable (async in flight)
 *   disabled  - disable interaction
 *   size      - 'sm' (tight grid cell) | 'badge' (inline pill)
 *   align     - 'left' | 'right'  (menu edge alignment)
 *   ariaLabel - accessible label for the trigger
 */
export default function GroupSelect({
  value,
  options = [],
  onChange,
  loading = false,
  disabled = false,
  size = 'sm',
  align = 'left',
  ariaLabel = 'Change sector / group',
}) {
  const opts = Array.from(new Set([value, ...options].filter(Boolean)));

  const [open, setOpen] = useState(false);
  const [render, setRender] = useState(false);   // kept in DOM through exit anim
  const [enter, setEnter] = useState(false);     // drives enter/exit transition
  const [activeIdx, setActiveIdx] = useState(0);

  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const optionRefs = useRef([]);

  const isDisabled = disabled || loading;

  // Mount → next frame fade/slide in; on close, animate out then unmount.
  useEffect(() => {
    if (open) {
      setRender(true);
      const id = requestAnimationFrame(() => setEnter(true));
      return () => cancelAnimationFrame(id);
    }
    setEnter(false);
    const t = setTimeout(() => setRender(false), 160);
    return () => clearTimeout(t);
  }, [open]);

  const close = useCallback((focusTrigger = true) => {
    setOpen(false);
    if (focusTrigger) requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  const openMenu = useCallback(() => {
    if (isDisabled) return;
    setActiveIdx(Math.max(0, opts.indexOf(value)));
    setOpen(true);
  }, [isDisabled, opts, value]);

  const select = useCallback((opt) => {
    if (opt && opt !== value) onChange?.(opt);
    close();
  }, [value, onChange, close]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    return () => document.removeEventListener('mousedown', onDocDown);
  }, [open]);

  // Keep the active option scrolled into view.
  useEffect(() => {
    if (open && enter) optionRefs.current[activeIdx]?.scrollIntoView({ block: 'nearest' });
  }, [open, enter, activeIdx]);

  const onKeyDown = (e) => {
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIdx((i) => (i + 1) % opts.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIdx((i) => (i - 1 + opts.length) % opts.length);
        break;
      case 'Home':
        e.preventDefault();
        setActiveIdx(0);
        break;
      case 'End':
        e.preventDefault();
        setActiveIdx(opts.length - 1);
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        select(opts[activeIdx]);
        break;
      case 'Escape':
        e.preventDefault();
        close();
        break;
      case 'Tab':
        setOpen(false);
        break;
      default:
        break;
    }
  };

  const triggerSize =
    size === 'badge'
      ? 'gap-1.5 px-2.5 py-1 text-xs rounded-full'
      : 'gap-1 px-1.5 py-1 text-[11px] rounded-md w-full';

  return (
    <div className="relative" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        disabled={isDisabled}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        title={ariaLabel}
        className={[
          'inline-flex items-center justify-between font-medium',
          'border border-[var(--glass-border)] bg-[var(--bg-primary)]',
          'text-[var(--text-secondary)] cursor-pointer select-none',
          'transition-colors duration-200',
          'hover:text-[var(--text-primary)] hover:border-[rgba(139,92,246,0.45)]',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(139,92,246,0.55)]',
          open ? 'border-[rgba(139,92,246,0.55)] text-[var(--text-primary)]' : '',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          triggerSize,
        ].join(' ')}
      >
        <span className="truncate">{value || '—'}</span>
        {loading ? (
          <Loader2 className="w-3 h-3 shrink-0 animate-spin text-[var(--accent-purple)]" />
        ) : (
          <ChevronDown
            className={`w-3 h-3 shrink-0 text-[var(--text-muted)] transition-transform duration-200 motion-reduce:transition-none ${open ? 'rotate-180' : ''}`}
          />
        )}
      </button>

      {render && (
        <ul
          role="listbox"
          tabIndex={-1}
          aria-label={ariaLabel}
          onKeyDown={onKeyDown}
          className={[
            'absolute z-30 mt-1 min-w-full max-w-[260px] max-h-60 overflow-auto',
            align === 'right' ? 'right-0' : 'left-0',
            'rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]',
            'p-1 shadow-[0_12px_32px_rgba(0,0,0,0.45)] backdrop-blur-sm',
            'origin-top transition duration-150 ease-out motion-reduce:transition-none',
            enter ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 -translate-y-1 scale-95',
          ].join(' ')}
        >
          {opts.map((opt, i) => {
            const selected = opt === value;
            const active = i === activeIdx;
            return (
              <li
                key={opt}
                ref={(el) => (optionRefs.current[i] = el)}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setActiveIdx(i)}
                onClick={() => select(opt)}
                className={[
                  'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer',
                  'transition-colors duration-150 motion-reduce:transition-none',
                  active ? 'bg-[rgba(139,92,246,0.16)] text-[var(--text-primary)]' : 'text-[var(--text-secondary)]',
                ].join(' ')}
              >
                <Check
                  className={`w-3.5 h-3.5 shrink-0 ${selected ? 'text-[var(--accent-cyan)]' : 'opacity-0'}`}
                />
                <span className="truncate">{opt}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
