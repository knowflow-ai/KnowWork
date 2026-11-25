import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithApp } from '../../../test/render';
import { JumpToLatest } from '../components/JumpToLatest';
import { useFollowLatest } from './useFollowLatest';

function Area() {
  const { ref, atLatest, onScroll, toLatest } = useFollowLatest();
  return (
    <>
      <div data-testid="area" ref={ref} onScroll={onScroll} />
      {atLatest ? null : <JumpToLatest onClick={toLatest} />}
    </>
  );
}

function scrollTo(element: HTMLElement, top: number) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: 2000 });
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: 600 });
  Object.defineProperty(element, 'scrollTop', { configurable: true, value: top });
  fireEvent.scroll(element);
}

describe('回到最新（照 ChatGPT、DeepSeek）', () => {
  it('停在底部附近不出按钮；往上翻了出「回到最新」，点了平滑滚到底；回到底部按钮收起', async () => {
    renderWithApp(<Area />);
    const area = screen.getByTestId('area');
    const scroll = vi.fn();
    area.scrollTo = scroll as typeof area.scrollTo;
    scrollTo(area, 1350);
    expect(
      screen.queryByRole('button', { name: '回到最新' }),
      '离底部 50 像素还算在最新处',
    ).not.toBeInTheDocument();
    scrollTo(area, 400);
    await userEvent.click(screen.getByRole('button', { name: '回到最新' }));
    expect(scroll).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' });
    scrollTo(area, 1400);
    expect(screen.queryByRole('button', { name: '回到最新' })).not.toBeInTheDocument();
  });
});
