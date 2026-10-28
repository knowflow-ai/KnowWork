import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Metric, MetricGrid } from './Metric';
import { TrendChart, niceMax } from './TrendChart';

const days = (...values: (number | null)[]) =>
  values.map((value, i) => ({ label: `2026-10-${String(20 + i).padStart(2, '0')}`, value }));

describe('TrendChart', () => {
  afterEach(() => vi.restoreAllMocks());

  it('纵轴上限取好看的整数', () => {
    expect([0, 1, 3, 7, 12, 180, 999].map(niceMax)).toEqual([1, 1, 5, 10, 20, 200, 1000]);
  });

  it('给读屏软件报最近一天的值，没有数据的那天线断开', () => {
    const { container } = render(<TrendChart title="每天的提问数" points={days(3, 5, null, 8, 6)} />);
    expect(screen.getByRole('img', { name: '每天的提问数，最近一天 2026-10-24 为 6' })).toBeInTheDocument();
    expect(container.querySelectorAll('.kw-trend-line')).toHaveLength(2);
    expect(screen.getByText('10')).toBeInTheDocument(); // 纵轴上限
    expect(screen.getByText('2026-10-20')).toBeInTheDocument();
    expect(screen.getByText('2026-10-22')).toBeInTheDocument();
  });

  it('悬停显示那一天的值，移开就收起', () => {
    const { container } = render(
      <TrendChart
        title="引用率"
        points={days(0.5, 0.75, 0.6)}
        max={1}
        format={(v) => `${Math.round(v * 100)}%`}
      />,
    );
    const plot = container.querySelector('.kw-trend-plot') as HTMLElement;
    vi.spyOn(plot, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 200 } as DOMRect);
    fireEvent.mouseMove(plot, { clientX: 105 });
    expect(screen.getByRole('status')).toHaveTextContent('2026-10-21：75%');
    fireEvent.mouseLeave(plot);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('没有数据时也能画', () => {
    render(<TrendChart title="好评率" points={[]} />);
    expect(screen.getByRole('img', { name: '好评率，暂无数据' })).toBeInTheDocument();
  });
});

describe('Metric', () => {
  it('名称、数值和补充说明放在以名称命名的一组里', () => {
    render(
      <MetricGrid>
        <Metric label="提问" value="128" hint="比上一段时间多 12" />
      </MetricGrid>,
    );
    const metric = screen.getByRole('group', { name: '提问' });
    expect(metric).toHaveTextContent('128');
    expect(metric).toHaveTextContent('比上一段时间多 12');
  });
});
