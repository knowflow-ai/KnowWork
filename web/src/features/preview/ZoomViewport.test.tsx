import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithApp } from '../../test/render';
import { ZoomViewport } from './ZoomViewport';
import { FIT_WIDTH } from './zoom';

// jsdom 不排版：把滚动区宽度固定成 524（扣掉左右 24 的留白后正好是 1000 宽页面的一半）
beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 524 });
});
afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
});

function setup() {
  const onPreference = vi.fn();
  const onRenderZoom = vi.fn();
  const view = renderWithApp(
    <ZoomViewport
      preference={FIT_WIDTH}
      pageWidth={1000}
      inset={24}
      onPreference={onPreference}
      onRenderZoom={onRenderZoom}
    >
      <div>页面</div>
    </ZoomViewport>,
  );
  return { ...view, onPreference, onRenderZoom };
}

describe('ZoomViewport', () => {
  it('适应宽度按滚动区宽度算出比例，交给页面按这个比例画', () => {
    const { onRenderZoom } = setup();
    expect(onRenderZoom).toHaveBeenLastCalledWith(0.5);
    expect(screen.getByRole('button', { name: '缩放比例' })).toHaveTextContent('50%');
  });

  it('触控板捏合（Ctrl+滚轮）立即改比例，停下后记成固定比例', async () => {
    const { container, onPreference } = setup();
    const scrollport = container.querySelector('.kw-zoom-scrollport') as HTMLElement;
    fireEvent.wheel(scrollport, { ctrlKey: true, deltaY: -40 });
    expect(container.querySelector('.kw-zoom-frame')?.getAttribute('data-zoom-mode')).toBe('fixed');
    await waitFor(() =>
      expect(onPreference).toHaveBeenCalledWith({
        kind: 'fixed',
        scale: expect.closeTo(0.5 * Math.exp(0.4), 5),
      }),
    );
    fireEvent.wheel(scrollport, { deltaY: 40 });
    expect(onPreference).toHaveBeenCalledTimes(1);
  });
});
