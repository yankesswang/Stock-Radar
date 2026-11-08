// 財報筆記的操作分類（stance）配色 —— 財報結論頁與論點追蹤頁共用同一份定義。
// 兩頁本來各自複製一份，改了一邊另一邊就漂移；後端 STANCE_LABELS 是單一來源，
// 前端的顏色也該是。
//
// 加碼／出清是要動手的，用實色；續抱、觀察是不動作，用中性色。
export const STANCE_STYLE = {
    add:   'bg-[rgba(16,185,129,0.15)] text-[var(--accent-green)] border-[rgba(16,185,129,0.35)]',
    hold:  'bg-[rgba(99,102,241,0.14)] text-[#818cf8] border-[rgba(99,102,241,0.32)]',
    trim:  'bg-[rgba(245,158,11,0.16)] text-[var(--accent-yellow)] border-[rgba(245,158,11,0.35)]',
    exit:  'bg-[rgba(248,113,113,0.16)] text-[var(--accent-danger)] border-[rgba(248,113,113,0.35)]',
    avoid: 'bg-[var(--bg-secondary)] text-[var(--text-muted)] border-[var(--glass-border)]',
    watch: 'bg-[rgba(6,182,212,0.14)] text-[var(--accent-cyan)] border-[rgba(6,182,212,0.32)]',
};

// stance 為空＝抽取器不敢判（筆記沒寫明確動作），畫面上要標「待補」而不是留白。
export const stanceClass = (stance) => STANCE_STYLE[stance] || STANCE_STYLE.avoid;
