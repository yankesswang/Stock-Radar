/**
 * 個股標頭要的那幾個欄位（公司名、產業、市值、估值狀態、現價），給 StockPageHeader 用。
 *
 * 十二個切面共用同一個標頭，切分頁時頁面元件整個換掉，標頭如果每次都重抓，
 * 公司名和價格會閃一下再回來。所以回應存在模組層級的快取：同一檔第二次進來直接
 * 拿快取，價格超過一分鐘才回頭補抓一次。
 *
 * 主頁自己就抓了估值歷史（含這些欄位）和現價，可以用 override 傳進來，不再多打。
 *
 * useStockTabs 拿 /api/stocks/{t}/tabs：每個切面有沒有資料、一句話結論。分頁列
 * （灰化、角標）與主頁的總覽列都用它，所以同樣走模組快取並合併同時發出的請求。
 */
import { useEffect, useState } from 'react';
import axios from '../lib/apiClient';

const PRICE_TTL_MS = 60_000;
const cache = new Map(); // ticker → { info, price, priceAt }

function flattenInfo(d) {
  if (!d) return null;
  const v = d.latest_valuation || {};
  return {
    company_name: d.company_name ?? v.company_name ?? null,
    industry: d.industry ?? v.industry ?? null,
    market_cap_display: v.market_cap_display ?? null,
    valuation_status: v.valuation_status ?? null,
    valuation_diff_pct: v.valuation_diff_pct ?? null,
  };
}

export function useStockHeader(ticker, overrides = {}) {
  const hasInfo = overrides.info !== undefined;
  const hasPrice = overrides.price !== undefined;
  const [, bump] = useState(0);

  useEffect(() => {
    if (!ticker) return;
    const t = ticker.toUpperCase();
    const entry = cache.get(t) ?? { info: undefined, price: undefined, priceAt: 0 };
    cache.set(t, entry);
    let alive = true;

    // override 進來的順手放進快取：從主頁切到子頁時，子頁的標頭第一幀就有資料
    if (hasInfo && overrides.info) entry.info = overrides.info;
    if (hasPrice && overrides.price) { entry.price = overrides.price; entry.priceAt = Date.now(); }

    const jobs = [];
    if (!hasInfo && entry.info === undefined) {
      jobs.push(
        axios.get(`/api/stocks/${t}`)
          .then(({ data }) => { entry.info = flattenInfo(data); })
          .catch(() => { entry.info = null; }),
      );
    }
    if (!hasPrice && (entry.price === undefined || Date.now() - entry.priceAt > PRICE_TTL_MS)) {
      jobs.push(
        axios.get(`/api/stocks/${t}/price`)
          .then(({ data }) => { entry.price = data; entry.priceAt = Date.now(); })
          .catch(() => { if (entry.price === undefined) entry.price = null; }),
      );
    }
    if (jobs.length) Promise.all(jobs).then(() => { if (alive) bump((n) => n + 1); });
    return () => { alive = false; };
    // 依賴數值不依賴物件識別：呼叫端常在 render 裡現組 { price: … }，
    // 用物件當依賴的話每次 render 都會重跑這個 effect
  }, [ticker, hasInfo, hasPrice, overrides.info?.company_name, overrides.info?.valuation_status,
      overrides.info?.valuation_diff_pct, overrides.price?.price, overrides.price?.change]);

  const entry = ticker ? cache.get(ticker.toUpperCase()) : null;
  return {
    info: hasInfo ? overrides.info : (entry?.info ?? null),
    price: hasPrice ? overrides.price : (entry?.price ?? null),
  };
}

const TABS_TTL_MS = 5 * 60_000;
// 失敗的短一點：端點掛掉時不該讓整頁五分鐘都拿不到東西
const TABS_FAIL_TTL_MS = 30_000;
const tabsCache = new Map(); // ticker → { data, at, pending }

export function useStockTabs(ticker) {
  const [, bump] = useState(0);

  useEffect(() => {
    if (!ticker) return;
    const t = ticker.toUpperCase();
    let entry = tabsCache.get(t);
    const ttl = entry?.failed ? TABS_FAIL_TTL_MS : TABS_TTL_MS;
    if (entry?.data !== undefined && Date.now() - entry.at < ttl) return;
    let alive = true;
    if (!entry?.pending) {
      // 失敗回一個帶 failed 的哨兵而不是 null：呼叫端要分得出「還在載」與「載不到」，
      // 否則主頁的總覽列會永遠停在骨架屏，既不出內容也不出錯誤
      const pending = axios.get(`/api/stocks/${t}/tabs`)
        .then((r) => r.data)
        .catch(() => ({ failed: true }))
        .then((data) => {
          tabsCache.set(t, { data, at: Date.now(), failed: !!data.failed });
          return data;
        });
      entry = { ...(entry || {}), pending };
      tabsCache.set(t, entry);
    }
    entry.pending.then(() => { if (alive) bump((n) => n + 1); });
    return () => { alive = false; };
  }, [ticker]);

  const entry = ticker ? tabsCache.get(ticker.toUpperCase()) : null;
  return entry?.data ?? null;
}

/** AI 分析重新產生之後叫一下，讓分頁列與總覽列下一次重抓 */
export function invalidateStockTabs(ticker) {
  if (ticker) tabsCache.delete(ticker.toUpperCase());
}
