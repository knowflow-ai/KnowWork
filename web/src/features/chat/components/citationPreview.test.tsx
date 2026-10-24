import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp, routeFetch } from '../../../test/render';
import { register } from '../../../slots/registry';
import { Citation } from './Citation';
import { AnswerSources } from './AnswerSources';
import { citedFiles } from './citations';

afterEach(() => vi.unstubAllGlobals());

const SOURCE = {
  title: '差旅办法',
  excerpt: '报销要在 30 天内提交',
  sourceId: 'demo',
  docRef: 'kb1/d1',
  locate: { type: 'text' as const, quote: '30 天内提交' },
};

describe('引用打开原文预览', () => {
  it('注册了引用预览器的知识源：在预览抽屉里打开并标出引用的那段，出处气泡随之收起', async () => {
    routeFetch({ 'GET /files/d1': new Response('第一章\n报销要在 30 天内提交\n第二章') });
    const resolve = vi.fn(async (docRef: string) =>
      docRef === 'kb1/d1' ? { name: '差旅办法.txt', url: '/files/d1' } : null,
    );
    register('citation.preview', { sourceId: 'demo', resolve });
    renderWithApp(<Citation index={1} source={SOURCE} />);
    await userEvent.click(screen.getByRole('button', { name: '查看出处 1' }));
    await userEvent.click(await screen.findByRole('button', { name: '查看原文' }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('kb1/d1'));
    const dialog = await screen.findByRole('dialog', { name: '差旅办法.txt' });
    await waitFor(() => expect(dialog.querySelector('mark')?.textContent).toBe('30 天内提交'));
    await waitFor(() => expect(screen.queryByRole('button', { name: '查看原文' })).not.toBeInTheDocument());
    expect(screen.queryByText('出处 1')).not.toBeInTheDocument();
  });

  it('文档没了或没有权限时提示一句；没注册的知识源仍跳原文链接', async () => {
    routeFetch({});
    register('citation.preview', { sourceId: 'gone', resolve: async () => null });
    renderWithApp(
      <>
        <Citation index={1} source={{ ...SOURCE, sourceId: 'gone' }} />
        <Citation
          index={2}
          source={{ title: '外部', sourceId: 'other', docRef: 'x', url: 'http://kf/doc' }}
        />
      </>,
    );
    await userEvent.click(screen.getByRole('button', { name: '查看出处 1' }));
    await userEvent.click(await screen.findByRole('button', { name: '查看原文' }));
    expect(await screen.findByText('原文已不可用，可能文档已删除或你没有权限')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(screen.getByRole('button', { name: '查看出处 2' }));
    expect(await screen.findByRole('link', { name: '查看原文' })).toHaveAttribute('href', 'http://kf/doc');
  });

  it('回答的来源：按钮写几个来源，点开面板逐条列出；点一条在预览抽屉里打开原文并定位，没注册预览器的跳原文链接', async () => {
    routeFetch({ 'GET /files/d1': new Response('第一章\n报销要在 30 天内提交\n第二章') });
    register('citation.preview', {
      sourceId: 'demo',
      resolve: async () => ({ name: '差旅办法.txt', url: '/files/d1' }),
    });
    const sources = {
      1: { ...SOURCE, page: 2 },
      2: { ...SOURCE, locate: undefined, page: 2 },
      3: { title: '外部', url: 'http://kf/doc' },
    };
    renderWithApp(<AnswerSources files={citedFiles('见[2][1]，另见[3]', sources)} />);
    await userEvent.click(screen.getByRole('button', { name: '2 个来源' }));
    const list = await screen.findByRole('list', { name: '引用的文件' });
    expect(within(list).getByRole('link', { name: '查看原文 外部' })).toHaveAttribute(
      'href',
      'http://kf/doc',
    );
    const item = within(list).getByRole('button', { name: '查看原文 差旅办法' });
    expect(item).toHaveTextContent('[1] [2] · 第 2 页');
    expect(item).toHaveTextContent('报销要在 30 天内提交');
    await userEvent.click(item);
    const dialog = await screen.findByRole('dialog', { name: '差旅办法.txt' });
    await waitFor(() => expect(screen.queryByRole('list', { name: '引用的文件' })).toBeNull());
    expect(dialog).toBeInTheDocument();
  });
});
