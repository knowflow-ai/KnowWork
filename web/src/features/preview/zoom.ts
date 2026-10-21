/** 预览缩放的取值，照 DSH 官方预览（client/zoom/types.js 与 ZoomControls.js，MIT）。 */

export type ZoomPreference =
  { readonly kind: 'fit-width' } | { readonly kind: 'fixed'; readonly scale: number };

export const FIT_WIDTH: ZoomPreference = { kind: 'fit-width' };
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;
/** 菜单里的固定档位。 */
export const ZOOM_OPTIONS = [0.25, 0.5, 1, 1.5, 2] as const;

export const clampZoom = (zoom: number): number => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));

/** 按 25% 一档放大或缩小，落到最近的整档上。 */
export function steppedZoom(zoom: number, direction: 1 | -1): number {
  const step = direction === -1 ? Math.ceil(zoom / ZOOM_STEP) - 1 : Math.floor(zoom / ZOOM_STEP) + 1;
  return clampZoom(step * ZOOM_STEP);
}

/** 适应宽度：只把比视口宽的页面缩小，不放大到 100% 以上。 */
export function fitWidthZoom(viewportWidth: number, pageWidth: number | undefined, inset = 0): number {
  if (pageWidth === undefined || pageWidth <= 0) return 1;
  return Math.min(1, Math.max(1, viewportWidth - inset) / pageWidth);
}

export const percentOf = (zoom: number): string => `${Math.round(zoom * 100)}%`;
