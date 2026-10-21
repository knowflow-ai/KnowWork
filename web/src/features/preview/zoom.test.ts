import { describe, expect, it } from 'vitest';
import { clampZoom, fitWidthZoom, percentOf, steppedZoom } from './zoom';

describe('预览缩放', () => {
  it('按 25% 一档放大缩小，落到整档上，限制在 25% 到 400%', () => {
    expect(steppedZoom(1, 1)).toBe(1.25);
    expect(steppedZoom(1, -1)).toBe(0.75);
    expect(steppedZoom(0.86, 1)).toBe(1);
    expect(steppedZoom(0.86, -1)).toBe(0.75);
    expect(steppedZoom(0.25, -1)).toBe(0.25);
    expect(steppedZoom(4, 1)).toBe(4);
    expect(clampZoom(9)).toBe(4);
  });

  it('适应宽度只把比视口宽的页面缩小，不放大到 100% 以上', () => {
    expect(fitWidthZoom(500, 1000, 0)).toBe(0.5);
    expect(fitWidthZoom(524, 1000, 24)).toBe(0.5);
    expect(fitWidthZoom(2000, 1000, 24)).toBe(1);
    expect(fitWidthZoom(500, undefined)).toBe(1);
    expect(percentOf(0.856)).toBe('86%');
  });
});
