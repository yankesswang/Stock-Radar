/* Stock Radar 主題 —— 做法與 alpha_note/server/static/theme.js 一致。
 *
 * 三種偏好：light（預設）/ dark / system，存在 localStorage['stockradar.theme']。
 * 實際套用的是 <html data-theme="light|dark">，CSS 只看這個屬性、不看
 * prefers-color-scheme —— 使用者的選擇要能蓋過系統設定；system 模式才回頭
 * 問系統，並跟著系統即時切換。
 *
 * 防閃白：index.html 的 <head> 先 inline 一段同樣的讀取邏輯搶在繪製前套用，
 * 這支模組負責切換 API、事件廣播與跨分頁同步。
 */
const KEY = 'stockradar.theme';
const VALID = ['light', 'dark', 'system'];
const ORDER = ['light', 'dark', 'system'];
const EVENT = 'stockradar:theme';
const THEME_COLOR = { light: '#ffffff', dark: '#0a0b0d' };

const mq = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null;

function read() {
  try {
    const v = localStorage.getItem(KEY);
    return VALID.includes(v) ? v : 'light';
  } catch {
    return 'light';
  }
}

function resolve(pref) {
  if (pref === 'system') return mq && mq.matches ? 'dark' : 'light';
  return pref;
}

function apply(pref) {
  const actual = resolve(pref);
  const root = document.documentElement;
  root.setAttribute('data-theme', actual);
  root.style.colorScheme = actual;
  const meta = document.querySelector('meta[name="theme-color"]:not([media])');
  if (meta) meta.setAttribute('content', THEME_COLOR[actual]);
  return actual;
}

function emit(pref) {
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { pref, actual: resolve(pref) } }));
}

export const Theme = {
  KEY,
  EVENT,
  get: read,
  resolved: () => resolve(read()),
  set(pref) {
    if (!VALID.includes(pref)) pref = 'light';
    try { localStorage.setItem(KEY, pref); } catch { /* private mode */ }
    apply(pref);
    emit(pref);
    return pref;
  },
  // 亮 → 暗 → 跟隨系統 → 亮
  cycle() {
    return Theme.set(ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length]);
  },
  label(pref) {
    return { light: '亮色', dark: '暗色', system: '跟隨系統' }[pref || read()];
  },
  /* 訂閱變更；回傳取消函式。cb({ pref, actual }) */
  subscribe(cb) {
    const onEvt = (e) => cb(e.detail);
    window.addEventListener(EVENT, onEvt);
    return () => window.removeEventListener(EVENT, onEvt);
  },
};

if (typeof window !== 'undefined') {
  // system 模式時跟著系統即時切換
  if (mq) {
    const onSys = () => { if (read() === 'system') { apply('system'); emit('system'); } };
    if (mq.addEventListener) mq.addEventListener('change', onSys);
    else mq.addListener(onSys);
  }
  // 另一個分頁改了主題，這邊同步
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) { apply(read()); emit(read()); }
  });
  apply(read());
}

export default Theme;
