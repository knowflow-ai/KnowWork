/** 逐步回放：只显示前 shown 步，播放时按固定间隔往前走，走到最后一步停下。没在回放时显示全部。 */
import { useEffect, useState } from 'react';

const REPLAY_INTERVAL_MS = 1200;

export function useReplay(total: number) {
  const [picked, setPicked] = useState<number>();
  const [playing, setPlaying] = useState(false);
  const shown = Math.min(picked ?? total, total);
  useEffect(() => {
    if (!playing) return undefined;
    const timer = setTimeout(() => {
      const next = Math.min(total, shown + 1);
      setPicked(next);
      if (next >= total) setPlaying(false);
    }, REPLAY_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [playing, shown, total]);
  return {
    shown,
    replaying: picked !== undefined,
    playing,
    back: () => setPicked(Math.max(1, shown - 1)),
    forward: () => setPicked(Math.min(total, shown + 1)),
    toggle: () => {
      if (!playing && shown >= total) setPicked(1);
      setPlaying(!playing);
    },
  };
}
