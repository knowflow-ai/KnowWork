import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithApp } from '../../../test/render';
import { FileLinks, Markdown } from './Markdown';

describe('回答正文里的行内代码（照 DSH 官方渲染与交付插件）', () => {
  it('行内代码是官方样式的代码小块；点到这一轮改过、交付的文件变成文件链接，点了在右侧打开', async () => {
    const open = vi.fn();
    renderWithApp(
      <FileLinks opener={{ open }}>
        <Markdown
          text="改好了 `合同-修订版.docx`，命令是 `pandoc 合同-修订版.docx -o 合同.pdf`，没动 `other.docx`。"
          files={['/sessions/s1/合同-修订版.docx']}
        />
      </FileLinks>,
    );
    const link = screen.getByRole('button', { name: '合同-修订版.docx' });
    expect(link).toHaveAttribute('title', '/sessions/s1/合同-修订版.docx');
    expect(link.querySelector('code')).toHaveClass('kw-inline-code');
    await userEvent.click(link);
    expect(open).toHaveBeenCalledWith({ path: '/sessions/s1/合同-修订版.docx' });
    expect(screen.getByText('pandoc 合同-修订版.docx -o 合同.pdf')).toHaveClass('kw-inline-code');
    expect(
      screen.queryByRole('button', { name: 'other.docx' }),
      '这一轮没碰过的文件不链',
    ).not.toBeInTheDocument();
  });

  it('这里打不开文件（没给打开方式）时只是代码小块', () => {
    renderWithApp(<Markdown text="见 `合同-修订版.docx`" files={['合同-修订版.docx']} />);
    expect(screen.queryByRole('button', { name: '合同-修订版.docx' })).not.toBeInTheDocument();
    expect(screen.getByText('合同-修订版.docx')).toHaveClass('kw-inline-code');
  });
});
