// 量容器寬度（ResizeObserver）：圖表依實際寬度排版，而不是固定 viewBox 等比縮小（手機上字會小到看不見）。
import { useEffect, useRef, useState } from 'react';

export function useWidth<T extends HTMLElement = HTMLDivElement>(fallback = 900): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => { const x = Math.round(el.getBoundingClientRect().width); if (x > 0) setW(x); };
    measure();
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', measure); return () => window.removeEventListener('resize', measure); }
    const ro = new ResizeObserver(measure); ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
