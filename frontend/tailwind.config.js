/** @type {import('tailwindcss').Config} */
export default {
  // 暗色靠 <html data-theme="dark">（src/theme.js 設定），不看 prefers-color-scheme，
  // 使用者的選擇才蓋得過系統。
  darkMode: ['selector', '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // Match --font-mono-family so `font-mono` gives the design system's
        // tabular figures instead of the browser default mono stack.
        mono: ['Fira Code', 'SFMono-Regular', 'ui-monospace', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 0.6s ease-out',
        'fade-in-up': 'fadeInUp 0.8s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
