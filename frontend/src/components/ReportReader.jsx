import { useMemo, useState, useRef, useCallback, useEffect } from 'react';
import { ChevronDown, ChevronRight, ListTree } from 'lucide-react';
import Markdown from './Markdown';
import { headingId } from './markdownText';

/**
 * 長篇報告的閱讀容器。
 *
 * 每日報告的 `stocks` / `warroom` 兩段各兩萬多字，原本整段丟進一個
 * max-h-[70vh] 的捲動框：內容 10,714px 高、要在框裡捲 14 個畫面，沒有目錄、
 * 沒有錨點，而且「📚 附錄（完整內容）」跟「⏱️ 90 秒晨報」攤在同一層。
 * 這裡把它切成有標題的段落、給目錄、並把作者自己標成參考用的段落預設收起來。
 *
 * 內文不再自帶捲動框：巢狀捲軸會吃掉瀏覽器的尋找（Cmd-F 捲不到框內）、
 * 上一頁的捲動位置還原、以及滾輪在框邊界的交接。正文跟著整頁捲，目錄改成
 * sticky 留在左側 —— 一樣看得到導覽，但捲的自始至終只有一個東西。
 *
 * 換報告段落時由呼叫端給 key 讓它重掛（附錄收合／目前段落一起歸零），
 * 捲回開頭則是點擊當下就做 —— 兩件事都不必靠 content 變動的 effect。
 */

// 固定 navbar（top-4 + 內距）約 76px 高，錨點要落在它下面
const ANCHOR_OFFSET = '5.5rem';

// 作者已經在標題上寫明是參考／附錄性質的段落 —— 預設收起來，要看再展開
const FOLD_RE = /附錄|被篩掉的來源|產出驗證明細/;

// 把 markdown 依 # / ## 切塊。程式碼圍籬內的 # 不算標題。
function splitByHeading(md) {
  const blocks = [];
  let cur = { level: 0, title: '', lines: [] };
  let fenced = false;
  const flush = () => { if (cur.title || cur.lines.some(l => l.trim())) blocks.push(cur); };
  for (const line of String(md || '').split('\n')) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    const m = fenced ? null : /^(#{1,2})\s+(.+?)\s*$/.exec(line);
    if (m) {
      flush();
      cur = { level: m[1].length, title: m[2].replace(/[*`]/g, '').trim(), lines: [line] };
    } else {
      cur.lines.push(line);
    }
  }
  flush();
  return blocks.map(b => ({ ...b, text: b.lines.join('\n').trim(), id: b.title ? headingId(b.title) : '' }));
}

// # 層級的附錄要連它底下的 ## 一起收；## 層級的參考段落自己收自己。
function buildSections(md) {
  const blocks = splitByHeading(md);
  const out = [];
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i];
    if (b.level === 1 && FOLD_RE.test(b.title)) {
      const children = [];
      let j = i + 1;
      while (j < blocks.length && blocks[j].level === 2) { children.push(blocks[j]); j++; }
      out.push({ ...b, fold: true, children });
      i = j - 1;
    } else {
      out.push({ ...b, fold: b.level === 2 && FOLD_RE.test(b.title), children: [] });
    }
  }
  return out;
}

const charCount = (s) => s.replace(/\s/g, '').length;

function Folded({ section, open, onToggle, tickers, markHeld, quotes }) {
  const chars = charCount(section.text) + section.children.reduce((n, c) => n + charCount(c.text), 0);
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="digest-focusable flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-[var(--bg-tertiary)]"
      >
        {open ? <ChevronDown size={15} className="shrink-0 text-[var(--text-muted)]" />
              : <ChevronRight size={15} className="shrink-0 text-[var(--text-muted)]" />}
        <span className="text-sm font-semibold text-[var(--text-primary)]">{section.title}</span>
        <span className="text-[13px] text-[var(--text-meta)]">
          {section.children.length > 0 && `${section.children.length} 個小節 · `}
          約 {chars.toLocaleString()} 字
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--glass-border)] px-4 pb-4 pt-1">
          <Markdown tickers={tickers} markHeld={markHeld} quotes={quotes}>
            {[section.text, ...section.children.map(c => c.text)].join('\n\n')}
          </Markdown>
        </div>
      )}
    </div>
  );
}

/**
 * @param {object} quotes  ticker → { price, change_percent, last_date }，標題上掛現價
 * @param {boolean} markHeld  在標題上標「持有中」。機會掃描是陌生開發，掃到手上
 *   已有的部位要一眼認得出來；持倉復盤那種整份都是自己的部位就不該開。
 */
export default function ReportReader({ content, tickers, markHeld = false, quotes = null }) {
  const sections = useMemo(() => buildSections(content), [content]);
  const [openFolds, setOpenFolds] = useState(() => new Set());
  const [active, setActive] = useState(0);
  const bodyRef = useRef(null);
  const tocRef = useRef(null);

  // 捲到哪一段就把目錄那一項點亮 —— 十幾個畫面高的文件，沒有這個會迷路。
  // 用 IntersectionObserver 開一條貼著視窗上緣的窄帶，落在帶子裡的最上面那段
  // 就是「現在在讀的」；比 scroll 事件便宜，也不會每一幀都重排。
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const hosts = [...body.querySelectorAll('[data-sec]')];
    if (!hosts.length) return;
    const inBand = new Set();
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          const i = Number(e.target.dataset.sec);
          if (e.isIntersecting) inBand.add(i); else inBand.delete(i);
        });
        if (inBand.size) setActive(Math.min(...inBand));
      },
      { rootMargin: '-88px 0px -78% 0px', threshold: 0 },
    );
    hosts.forEach(el => io.observe(el));
    return () => io.disconnect();
  }, [sections, openFolds]);

  // 目錄比視窗高時，捲到下半部的段落不該把亮起來的那一項留在看不見的地方。
  // 只能動目錄自己的 scrollTop —— scrollIntoView 會連同所有可捲動的祖先一起捲，
  // 包含視窗本身：目錄一旦落在初次可視範圍外，整頁會在載入當下自己往下跳。
  useEffect(() => {
    const box = tocRef.current;
    const el = box?.querySelector('[data-toc-active="true"]');
    if (!box || !el) return;
    const top = el.offsetTop - box.offsetTop;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (top + el.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTop = top + el.offsetHeight - box.clientHeight;
    }
  }, [active]);

  const toggle = useCallback((idx) => {
    setOpenFolds(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  }, []);

  // 目錄。收起來的附錄連它的小節一起列，點了自動展開再跳過去。
  // 用段落序號定位而不是標題 id —— 「📋 被篩掉的來源」一份報告裡出現三次，
  // id 會撞在一起，三個目錄項都會跳到第一個。
  const toc = useMemo(() => {
    const items = [];
    sections.forEach((s, idx) => {
      if (!s.title) return;
      items.push({ key: `s${idx}`, sec: idx, childId: null, title: s.title, level: s.level });
      s.children.forEach(c => {
        items.push({ key: `s${idx}-${c.id}`, sec: idx, childId: c.id, title: c.title, level: 2 });
      });
    });
    return items;
  }, [sections]);

  const jump = useCallback((item) => {
    if (item.childId) setOpenFolds(prev => new Set(prev).add(item.sec));
    // 展開是 state 更新，要等這一幀畫完才找得到節點
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const host = bodyRef.current?.querySelector(`[data-sec="${item.sec}"]`);
      if (!host) return;
      const el = (item.childId && host.querySelector(`#${CSS.escape(item.childId)}`)) || host;
      // scroll-margin-top（下面 style 帶的）會讓落點自動避開固定的 navbar
      el.scrollIntoView({ block: 'start', behavior: 'auto' });
    }));
  }, []);

  const foldIdx = sections.map((s, i) => (s.fold ? i : -1)).filter(i => i >= 0);
  const allOpen = foldIdx.length > 0 && foldIdx.every(i => openFolds.has(i));

  return (
    <div className="lg:grid lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-7">
      {/* 目錄：窄螢幕橫排在上，寬螢幕變成跟著捲的 sticky 側欄 —— 正文本來就該有
          行長上限，與其讓右半邊空著，不如把導覽放進去。 */}
      {toc.length > 1 && (
        <nav
          ref={tocRef}
          aria-label="報告目錄"
          className="mb-4 rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] lg:sticky lg:top-[5.5rem] lg:mb-0 lg:max-h-[calc(100dvh-7.5rem)] lg:self-start lg:overflow-y-auto lg:overscroll-contain"
        >
          <div className="flex items-center gap-1.5 px-3 pb-1.5 pt-3">
            <ListTree size={13} className="shrink-0 text-[var(--text-muted)]" />
            <span className="text-[12px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">目錄</span>
            {foldIdx.length > 0 && (
              <button
                onClick={() => setOpenFolds(allOpen ? new Set() : new Set(foldIdx))}
                className="digest-focusable ml-auto rounded-md px-1.5 py-0.5 text-[12px] font-medium text-[var(--text-meta)] transition-colors hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]"
              >
                {allOpen ? '收合附錄' : '展開附錄'}
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1 p-2 pt-0 lg:flex-col lg:flex-nowrap lg:gap-0.5">
            {toc.map(item => {
              const on = item.sec === active && !item.childId;
              return (
                <button
                  key={item.key}
                  data-toc-active={on}
                  aria-current={on ? 'true' : undefined}
                  onClick={() => jump(item)}
                  title={item.title}
                  className={`digest-focusable rounded-md px-2 py-1.5 text-left text-[13px] transition-colors lg:w-full lg:truncate ${
                    item.level === 2 ? 'lg:pl-4' : ''
                  } ${
                    on
                      ? 'bg-[var(--bg-tertiary)] font-semibold text-[var(--accent-cyan)]'
                      : item.level === 1
                        ? 'font-semibold text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'
                        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
                  }`}
                >
                  {item.title}
                </button>
              );
            })}
          </div>
        </nav>
      )}

      <div ref={bodyRef} className="min-w-0" style={{ scrollMarginTop: ANCHOR_OFFSET }}>
        {sections.map((s, i) => (
          <div key={i} data-sec={i} className={i === 0 ? '' : 'mt-8'} style={{ scrollMarginTop: ANCHOR_OFFSET }}>
            {s.fold
              ? <Folded section={s} tickers={tickers} markHeld={markHeld} quotes={quotes}
                        open={openFolds.has(i)} onToggle={() => toggle(i)} />
              : <Markdown tickers={tickers} markHeld={markHeld} quotes={quotes}>{s.text}</Markdown>}
          </div>
        ))}
      </div>
    </div>
  );
}
