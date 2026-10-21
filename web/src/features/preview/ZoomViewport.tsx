/**
 * 可缩放的滚动区，照 DSH 官方预览（client/zoom/ZoomViewport.js，MIT）：
 * - 默认适应宽度（只把比视口宽的页面缩小）；选了固定档或用触控板捏合、Ctrl+滚轮后改成固定比例；
 * - 缩放先只改 CSS 变量，画面立即跟手；停下 120 毫秒后才把比例交给页面按新清晰度重画；
 * - 缩放以鼠标所在点为锚，内容不跳；控件平时收起，鼠标移到底部 72 像素内才浮出，离开 420 毫秒后收起。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ZoomControls } from './ZoomControls';
import { clampZoom, FIT_WIDTH, fitWidthZoom, type ZoomPreference } from './zoom';

const PINCH_SETTLE_MS = 120;
const CONTROLS_HIDE_MS = 420;
const CONTROLS_REVEAL_PX = 72;

/** 鼠标下面那一页上的点，缩放后据此把它拉回鼠标下。 */
function pointerAnchor(clientX: number, clientY: number) {
  const hit = document.elementFromPoint?.(clientX, clientY);
  const surface = hit?.closest('[data-zoom-surface]');
  if (!surface) return undefined;
  const bounds = surface.getBoundingClientRect();
  return { surface, x: clientX - bounds.left, y: clientY - bounds.top, clientX, clientY };
}

export function ZoomViewport({
  preference,
  pageWidth,
  inset,
  onPreference,
  onRenderZoom,
  children,
}: {
  readonly preference: ZoomPreference;
  /** 100% 时的页面宽度（CSS 像素），还没量出来时按 100% 算。 */
  readonly pageWidth: number | undefined;
  /** 内容左右留白之和，适应宽度时扣掉。 */
  readonly inset: number;
  readonly onPreference: (preference: ZoomPreference) => void;
  readonly onRenderZoom: (zoom: number) => void;
  readonly children: ReactNode;
}) {
  const [viewportWidth, setViewportWidth] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(false);
  const fitZoom = fitWidthZoom(viewportWidth, pageWidth, inset);
  const zoom = preference.kind === 'fit-width' ? fitZoom : preference.scale;
  // 正在显示的比例与模式：捏合时先于偏好变化，渲染只读它（直接改 DOM 是为了缩放后立刻按新布局对准锚点）
  const [shown, setShown] = useState<{ readonly zoom: number; readonly mode: ZoomPreference['kind'] }>({
    zoom,
    mode: preference.kind,
  });
  const zoomRef = useRef(zoom);
  const frame = useRef<HTMLElement>(null);
  const scrollport = useRef<HTMLDivElement>(null);
  const pinchTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inRevealZone = useRef(false);
  const controlsActive = useRef(false);

  const applyZoom = useCallback((next: number, mode: ZoomPreference['kind']) => {
    zoomRef.current = next;
    frame.current?.setAttribute('data-zoom-mode', mode);
    frame.current?.style.setProperty('--document-zoom', String(next));
    setShown({ zoom: next, mode });
  }, []);
  const showControls = useCallback(() => {
    clearTimeout(hideTimer.current);
    setControlsVisible(true);
  }, []);
  const scheduleHide = useCallback(() => {
    clearTimeout(hideTimer.current);
    if (inRevealZone.current || controlsActive.current || pinchTimer.current !== undefined) return;
    hideTimer.current = setTimeout(() => setControlsVisible(false), CONTROLS_HIDE_MS);
  }, []);
  const onControlsActive = useCallback(
    (active: boolean) => {
      controlsActive.current = active;
      if (active) showControls();
      else scheduleHide();
    },
    [scheduleHide, showControls],
  );

  /** 改比例并保持锚点不动；没给鼠标位置时以视口中心为锚。 */
  const zoomTo = useCallback(
    (requested: number, clientX?: number, clientY?: number) => {
      const node = scrollport.current;
      const previous = zoomRef.current;
      const next = clampZoom(requested);
      if (!node || next === previous) return previous;
      const bounds = node.getBoundingClientRect();
      const x = clientX === undefined ? node.clientWidth / 2 : clientX - bounds.left;
      const y = clientY === undefined ? node.clientHeight / 2 : clientY - bounds.top;
      const anchor = pointerAnchor(bounds.left + x, bounds.top + y);
      const { scrollLeft, scrollTop } = node;
      const ratio = next / previous;
      applyZoom(next, 'fixed');
      if (anchor === undefined) {
        node.scrollLeft = (scrollLeft + x) * ratio - x;
        node.scrollTop = (scrollTop + y) * ratio - y;
      } else {
        const moved = anchor.surface.getBoundingClientRect();
        node.scrollLeft += moved.left + anchor.x * ratio - anchor.clientX;
        node.scrollTop += moved.top + anchor.y * ratio - anchor.clientY;
      }
      return next;
    },
    [applyZoom],
  );
  const setFixed = useCallback(
    (next: number) => {
      clearTimeout(pinchTimer.current);
      pinchTimer.current = undefined;
      onPreference({ kind: 'fixed', scale: zoomTo(next) });
    },
    [onPreference, zoomTo],
  );
  const setFitWidth = useCallback(() => {
    clearTimeout(pinchTimer.current);
    pinchTimer.current = undefined;
    applyZoom(fitZoom, 'fit-width');
    onPreference(FIT_WIDTH);
  }, [applyZoom, fitZoom, onPreference]);

  useLayoutEffect(() => {
    const node = scrollport.current;
    if (!node) return undefined;
    const measure = () => setViewportWidth(node.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // 比例确定下来（换档、适应宽度跟着视口变）后交给页面重画；捏合进行中不打扰
  useEffect(() => {
    if (pinchTimer.current !== undefined) return;
    applyZoom(zoom, preference.kind);
    onRenderZoom(zoom);
  }, [applyZoom, onRenderZoom, preference.kind, zoom]);

  // 触控板捏合在 Chromium 里就是带 Ctrl 的滚轮事件
  useEffect(() => {
    const node = scrollport.current;
    if (!node) return undefined;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      showControls();
      const unit =
        event.deltaMode === WheelEvent.DOM_DELTA_LINE
          ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? node.clientHeight
            : 1;
      const delta = Math.max(-40, Math.min(40, event.deltaY * unit));
      zoomTo(zoomRef.current * Math.exp(-delta * 0.01), event.clientX, event.clientY);
      clearTimeout(pinchTimer.current);
      pinchTimer.current = setTimeout(() => {
        pinchTimer.current = undefined;
        onPreference({ kind: 'fixed', scale: zoomRef.current });
        scheduleHide();
      }, PINCH_SETTLE_MS);
    };
    node.addEventListener('wheel', wheel, { passive: false });
    return () => {
      node.removeEventListener('wheel', wheel);
      clearTimeout(pinchTimer.current);
      pinchTimer.current = undefined;
      clearTimeout(hideTimer.current);
    };
  }, [onPreference, scheduleHide, showControls, zoomTo]);

  return (
    <section
      ref={frame}
      className="kw-zoom-frame"
      data-zoom-mode={shown.mode}
      style={{ '--document-zoom': shown.zoom } as React.CSSProperties}
      onPointerMove={(event) => {
        const bottom = event.currentTarget.getBoundingClientRect().bottom;
        inRevealZone.current = event.pointerType === 'mouse' && event.clientY >= bottom - CONTROLS_REVEAL_PX;
        if (inRevealZone.current) showControls();
        else scheduleHide();
      }}
      onPointerLeave={() => {
        inRevealZone.current = false;
        scheduleHide();
      }}
    >
      <div ref={scrollport} className="kw-zoom-scrollport">
        {children}
      </div>
      <ZoomControls
        zoom={shown.zoom}
        fitWidth={preference.kind === 'fit-width'}
        visible={controlsVisible}
        onZoom={setFixed}
        onFitWidth={setFitWidth}
        onActiveChange={onControlsActive}
      />
    </section>
  );
}
