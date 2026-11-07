import { useEffect, useRef, useState } from 'react';
import axios from '../lib/apiClient';

/**
 * Pre/post-market quotes for a list of tickers, keyed by symbol.
 *
 * The backend omits any symbol that isn't currently in an extended session, so
 * an empty map is the normal state during regular hours and overnight — callers
 * just render nothing. Polls every 60s while any quote is live, and every 5min
 * otherwise so the badges appear on their own when the pre-market opens.
 */
export function useExtendedQuotes(tickers) {
  const [quotes, setQuotes] = useState({});

  // Key by content, not identity: callers pass a fresh array each render.
  const key = (tickers || []).filter(Boolean).map((t) => t.toUpperCase()).sort().join(',');
  const liveRef = useRef(false);

  useEffect(() => {
    if (!key) return undefined;

    let cancelled = false;
    let timer;

    const poll = async () => {
      try {
        const { data } = await axios.get('/api/market/extended', { params: { symbols: key } });
        if (cancelled) return;
        const next = data?.quotes || {};
        setQuotes(next);
        liveRef.current = Object.keys(next).length > 0;
      } catch {
        if (!cancelled) setQuotes({});
      }
      if (!cancelled) timer = setTimeout(poll, liveRef.current ? 60_000 : 300_000);
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key]);

  // With no tickers there is nothing to show, and the effect above is skipped —
  // so answer from the argument rather than letting a stale map linger.
  return key ? quotes : EMPTY;
}

const EMPTY = {};
