import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme } from '../useTheme';

const ICON = { light: Sun, dark: Moon, system: Monitor };

/** 一顆按鈕循環「亮 → 暗 → 跟隨系統」，圖示跟著目前偏好走 */
export default function ThemeToggle({ className = '', size = 16 }) {
  const { pref, cycle, label } = useTheme();
  const Icon = ICON[pref] ?? Sun;
  const title = `主題：${label}（點擊切換）`;
  return (
    <button
      type="button"
      onClick={cycle}
      title={title}
      aria-label={title}
      className={`inline-flex shrink-0 items-center justify-center rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] hover:border-[var(--glass-border-hover)] active:bg-[rgba(0,82,255,0.06)] ${className}`}
    >
      <Icon size={size} />
    </button>
  );
}
