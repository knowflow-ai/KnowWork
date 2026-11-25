import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { CompactionNotice } from './CompactionNotice';
import { compactedAmount } from '../events/compactions';
import type { CompactionView } from '../events/types';

const base: CompactionView = { id: 'cp-1', status: 'completed', time: 1, afterRuns: 0 };

describe('CompactionNotice（对话里的一次上下文压缩）', () => {
  it('做完：一行说明压掉了多少，点「查看摘要」在右侧看摘要正文', async () => {
    renderWithApp(
      <CompactionNotice
        compaction={{ ...base, items: 3, tokens: 51234, summary: '用户在核对**三份合同**' }}
      />,
    );
    expect(
      screen.getByText(/上面的对话已压缩成摘要（3 条记录，约 51,234 tokens），模型从这里往后只看摘要/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '查看摘要' }));
    const sheet = await screen.findByRole('dialog', { name: '压缩摘要' });
    expect(sheet).toHaveTextContent('用户在核对三份合同');
  });

  it('进行中、出错各写一句；没写出摘要就结束的不显示；没有摘要正文时不给查看', () => {
    const { rerender, container } = renderWithApp(
      <CompactionNotice compaction={{ ...base, status: 'running' }} />,
    );
    expect(screen.getByText('正在压缩上下文…')).toBeInTheDocument();
    rerender(<CompactionNotice compaction={{ ...base, status: 'error', error: '摘要模型没有回应' }} />);
    expect(screen.getByText('上下文压缩没有完成：摘要模型没有回应')).toBeInTheDocument();
    rerender(<CompactionNotice compaction={{ ...base }} />);
    expect(screen.queryByRole('button', { name: '查看摘要' })).toBeNull();
    rerender(<CompactionNotice compaction={{ ...base, status: 'ended' }} />);
    expect(container.querySelector('.kw-compaction')).toBeNull();
  });

  it('压掉多少缺哪样就不写哪样', () => {
    expect(compactedAmount({ items: 2 })).toBe('（2 条记录）');
    expect(compactedAmount({ tokens: 900 })).toBe('（约 900 tokens）');
    expect(compactedAmount({})).toBe('');
  });
});
