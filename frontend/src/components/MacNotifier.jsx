import { useEffect, useMemo, useRef, useState } from 'react';
import axios from '../lib/apiClient';
import { Bell, BellRing, CalendarDays, TrendingUp, X, Trash2 } from 'lucide-react';

const POLL_INTERVAL_MS = 5 * 60 * 1000;
const EARNINGS_COOLDOWN_MS = 24 * 60 * 60 * 1000;
const MARKET_MOVE_COOLDOWN_MS = 4 * 60 * 60 * 1000;
const MAX_ITEMS = 8;
const AUTO_DISMISS_MS = 14 * 1000;
const STORAGE_KEY = 'stockradar_notifier_seen_v1';
const DAY_MS = 24 * 60 * 60 * 1000;
const EARNINGS_MILESTONES = new Set([0, 1, 3, 7]);

function loadSeenMap() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveSeenMap(map) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // ignore quota/storage errors
  }
}

function toDayStart(dateLike) {
  const d = new Date(dateLike);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export default function MacNotifier() {
  const [items, setItems] = useState([]);
  const [isOpen, setIsOpen] = useState(false);
  const [permission, setPermission] = useState(
    typeof window !== 'undefined' && 'Notification' in window ? Notification.permission : 'unsupported'
  );
  const seenRef = useRef({});
  const dismissTimersRef = useRef({});

  useEffect(() => {
    seenRef.current = loadSeenMap();
  }, []);

  const desktopReady = useMemo(
    () => permission === 'granted' && typeof window !== 'undefined' && 'Notification' in window,
    [permission]
  );

  const markSeen = (id) => {
    seenRef.current[id] = Date.now();
    saveSeenMap(seenRef.current);
  };

  const canNotify = (id, cooldownMs) => {
    const last = Number(seenRef.current[id] || 0);
    return Date.now() - last >= cooldownMs;
  };

  const pushItem = (item, cooldownMs) => {
    if (!canNotify(item.id, cooldownMs)) return;
    markSeen(item.id);
    setItems(prev => [item, ...prev.filter(x => x.id !== item.id)].slice(0, MAX_ITEMS));

    if (dismissTimersRef.current[item.id]) {
      window.clearTimeout(dismissTimersRef.current[item.id]);
    }
    dismissTimersRef.current[item.id] = window.setTimeout(() => {
      setItems(prev => prev.filter(x => x.id !== item.id));
      delete dismissTimersRef.current[item.id];
    }, AUTO_DISMISS_MS);

    if (desktopReady) {
      try {
        new Notification(item.title, { body: item.body });
      } catch {
        // ignore browser notification errors
      }
    }
  };

  const pollNotifications = async () => {
    try {
      const r = await axios.get('/api/earnings/calendar');
      const events = r?.data?.events || [];
      const today = toDayStart(new Date());

      events.forEach((ev) => {
        if (ev.status === 'reported') return;
        const earningsDay = toDayStart(ev.date);
        const diffDays = Math.round((earningsDay - today) / DAY_MS);
        if (!EARNINGS_MILESTONES.has(diffDays)) return;

        const whenText =
          diffDays === 0 ? '今天' :
            diffDays === 1 ? '明天' :
              `${diffDays} 天後`;

        pushItem(
          {
            id: `earnings:${ev.ticker}:${ev.date}:d${diffDays}`,
            type: 'earnings',
            icon: 'earnings',
            title: `${ev.ticker} 財報提醒`,
            body: `${whenText}（${ev.date}）公布財報`,
            timestamp: Date.now(),
          },
          EARNINGS_COOLDOWN_MS
        );
      });
    } catch {
      // ignore poll errors
    }

    try {
      const r = await axios.get('/api/notifications/market-movers');
      const events = r?.data?.events || [];
      const todayKey = new Date().toISOString().slice(0, 10);

      events.forEach((ev) => {
        const direction = ev.change_pct >= 0 ? '急漲' : '急跌';
        const changeText = `${ev.change_pct >= 0 ? '+' : ''}${ev.change_pct}%`;
        const volumeText = ev.volume_ratio ? `，量能 ${ev.volume_ratio}x` : '';

        pushItem(
          {
            id: `market:${ev.ticker}:${todayKey}`,
            type: 'market_move',
            icon: 'market_move',
            title: `${ev.ticker} 股價異常${direction}`,
            body: `現價 $${ev.price}，今日 ${changeText}${volumeText}`,
            timestamp: Date.now(),
          },
          MARKET_MOVE_COOLDOWN_MS
        );
      });
    } catch {
      // ignore poll errors
    }
  };

  useEffect(() => {
    pollNotifications();
    const timer = window.setInterval(pollNotifications, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktopReady]);

  const requestDesktopPermission = async () => {
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    try {
      const result = await Notification.requestPermission();
      setPermission(result);
    } catch {
      // ignore
    }
  };

  const removeItem = (id) => setItems(prev => prev.filter(x => x.id !== id));
  const removeItemWithTimer = (id) => {
    if (dismissTimersRef.current[id]) {
      window.clearTimeout(dismissTimersRef.current[id]);
      delete dismissTimersRef.current[id];
    }
    removeItem(id);
  };

  const clearAll = () => {
    Object.values(dismissTimersRef.current).forEach(timer => window.clearTimeout(timer));
    dismissTimersRef.current = {};
    setItems([]);
  };

  useEffect(() => () => {
    Object.values(dismissTimersRef.current).forEach(timer => window.clearTimeout(timer));
  }, []);

  const sendTestNotification = () => {
    pushItem(
      {
        id: `test:${Date.now()}`,
        type: 'test',
        icon: 'default',
        title: '測試通知 Test Notification',
        body: '通知功能正常，之後會自動提醒公司財報與股價異常波動。',
        timestamp: Date.now(),
      },
      0
    );
  };

  return (
    <div className="fixed top-24 right-4 z-[80] w-[min(360px,calc(100vw-2rem))] pointer-events-none">
      <div className="flex justify-end">
        <button
          onClick={() => setIsOpen(prev => !prev)}
          className="pointer-events-auto relative grid h-10 w-10 place-items-center rounded-full border border-[var(--glass-border)] bg-[var(--overlay-bg)] text-[var(--text-secondary)] shadow-[0_12px_28px_rgba(10,11,13,0.14)] backdrop-blur-md transition-colors hover:text-[var(--text-primary)]"
          title="通知中心"
        >
          {isOpen ? <X size={16} /> : <BellRing size={16} />}
          {items.length > 0 && !isOpen && (
            <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-[var(--accent-cyan)] px-1 text-[10px] font-semibold text-white">
              {items.length}
            </span>
          )}
        </button>
      </div>

      {isOpen && (
        <div className="mt-2 space-y-2">
          <div className="pointer-events-auto rounded-2xl border border-[var(--glass-border)] bg-[var(--overlay-bg)] backdrop-blur-md shadow-[0_16px_36px_rgba(10,11,13,0.16)] p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <BellRing size={14} className="text-[var(--accent-cyan)]" />
              <p className="text-xs font-semibold text-[var(--text-secondary)]">通知中心</p>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={sendTestNotification}
                className="px-2 py-1 rounded-lg text-[11px] font-medium bg-[rgba(87,139,250,0.16)] text-[var(--accent-purple)] hover:bg-[rgba(87,139,250,0.24)] transition-colors"
              >
                測試通知
              </button>
              {permission === 'default' && (
                <button
                  onClick={requestDesktopPermission}
                  className="px-2 py-1 rounded-lg text-[11px] font-medium bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)] hover:bg-[rgba(0,82,255,0.2)] transition-colors"
                >
                  啟用桌面通知
                </button>
              )}
              {items.length > 0 && (
                <button
                  onClick={clearAll}
                  className="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
                  title="Clear all"
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          </div>
          </div>

          {items.map(item => (
            <div
              key={item.id}
              className="pointer-events-auto rounded-2xl border border-[var(--glass-border)] bg-[var(--overlay-bg)] backdrop-blur-md shadow-[0_16px_36px_rgba(10,11,13,0.16)] p-3"
            >
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5 shrink-0">
                  {item.icon === 'earnings'
                    ? <CalendarDays size={14} className="text-[var(--accent-amber)]" />
                    : item.icon === 'market_move'
                      ? <TrendingUp size={14} className="text-[var(--accent-cyan)]" />
                    : <Bell size={14} className="text-[var(--accent-cyan)]" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-[var(--text-primary)]">{item.title}</p>
                  <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">{item.body}</p>
                </div>
                <button
                  onClick={() => removeItemWithTimer(item.id)}
                  className="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
                >
                  <X size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
