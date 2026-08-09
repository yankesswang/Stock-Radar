/**
 * Shared recharts styling.
 *
 * The charts were originally styled for a dark theme and kept white-on-white
 * strokes (rgba(255,255,255,0.04) grids, 0.06 axis lines) after the app moved to
 * the light palette in index.css — which rendered them invisible. These are
 * neutral slate alphas instead: they read against both light and dark
 * backgrounds, so they survive a future theme switch.
 *
 * WarRoom is intentionally excluded — it ships its own dark palette (`C.bg`).
 */

export const CHART_GRID = 'rgba(148,163,184,0.18)';   // gridlines: present, not loud
export const CHART_AXIS = 'rgba(148,163,184,0.35)';   // axis rules, matches --glass-border
export const CHART_REFERENCE = 'rgba(100,116,139,0.5)'; // benchmark lines: must be readable

export const chartTooltipStyle = {
    backgroundColor: 'var(--bg-primary)',
    border: '1px solid var(--glass-border)',
    borderRadius: '10px',
    fontSize: '12px',
    boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
};

export const chartTooltipLabelStyle = {
    color: 'var(--text-muted)',
    marginBottom: '4px',
};
