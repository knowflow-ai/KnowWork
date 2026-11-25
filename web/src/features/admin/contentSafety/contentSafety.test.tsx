import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { splitWords, validateContentSafety } from '../../../api/contentSafety';
import ContentSafetyPage from './ContentSafetyPage';

afterEach(() => vi.unstubAllGlobals());

const PATH = '/api/workspaces/default/content-safety';
const saved = (version: number, patch: Record<string, unknown> = {}) => ({
  version,
  input_enabled: true,
  output_enabled: true,
  words: '星河计划\n某某银行',
  word_count: 2,
  block_message: '当前对话内容违反公司规范，请修改后重新输入',
  updated_at: '2026-11-22T04:00:00+00:00',
  ...patch,
});

describe('内容安全配置页', () => {
  it('没配过：两个方向默认勾上，填好词一次保存，提示 5 秒内生效', async () => {
    const calls = routeFetch({
      [`GET ${PATH}`]: jsonResponse(
        saved(0, { input_enabled: false, output_enabled: false, words: '', word_count: 0 }),
      ),
      [`PUT ${PATH}`]: jsonResponse(saved(1)),
    });
    renderWithApp(<ContentSafetyPage />);
    const words = await screen.findByRole('textbox', { name: '自定义词表' });
    expect(screen.getByRole('checkbox', { name: /检查输入/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /检查输出/ })).toBeChecked();
    await userEvent.type(words, '星河计划，某某银行；星河计划');
    expect(screen.getByText('已填 2 个')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', PATH)).toHaveLength(1));
    expect(calls.find('PUT', PATH)[0]?.body).toEqual({
      version: 0,
      input_enabled: true,
      output_enabled: true,
      word_sources: ['custom'],
      words: '星河计划，某某银行；星河计划',
      block_message: '当前对话内容违反公司规范，请修改后重新输入',
    });
    expect(await screen.findByText('已保存，5 秒内生效')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '自定义词表' })).toHaveValue('星河计划\n某某银行');
  });

  it('开了检查却没填词：保存前就说明，不发请求', async () => {
    const calls = routeFetch({
      [`GET ${PATH}`]: jsonResponse(
        saved(0, { input_enabled: false, output_enabled: false, words: '', word_count: 0 }),
      ),
    });
    renderWithApp(<ContentSafetyPage />);
    await userEvent.click(await screen.findByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('用自定义词表就要填敏感词');
    expect(calls.find('PUT', PATH)).toHaveLength(0);
  });

  it('别的管理员先改过：载入最新版本、保留本页的修改，再点保存就覆盖', async () => {
    let latest = 1;
    const calls = routeFetch({
      [`GET ${PATH}`]: () => jsonResponse(saved(latest)),
      [`PUT ${PATH}`]: (call) =>
        (call.body as { version: number }).version === 2
          ? jsonResponse(saved(3, { words: '新词' }))
          : jsonResponse({ detail: '配置已被其他管理员修改' }, 409),
    });
    renderWithApp(<ContentSafetyPage />);
    const words = await screen.findByRole('textbox', { name: '自定义词表' });
    latest = 2;
    await userEvent.clear(words);
    await userEvent.type(words, '新词');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(await screen.findByText(/已被其他管理员修改/)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '自定义词表' })).toHaveValue('新词');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', PATH)).toHaveLength(2));
    expect((calls.find('PUT', PATH)[1]?.body as { version: number }).version).toBe(2);
  });

  it('普通成员进来：没有权限', async () => {
    routeFetch({ [`GET ${PATH}`]: jsonResponse({ detail: '只有空间所有者和管理员能配置内容安全' }, 403) });
    renderWithApp(<ContentSafetyPage />);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
  });
});

describe('现成词表与上传 txt', () => {
  const BUILTIN = { id: 'builtin', label: '内置词表', description: '约 2800 个常见违规词', word_count: 2809 };
  const withBuiltin = (patch: Record<string, unknown> = {}) =>
    saved(1, { word_sources: ['custom'], available_sources: [BUILTIN], ...patch });

  it('扩展登记了现成词表：列出来、默认不勾、可下载查看；只勾它时自定义词表收起', async () => {
    const calls = routeFetch({
      [`GET ${PATH}`]: jsonResponse(withBuiltin()),
      [`PUT ${PATH}`]: jsonResponse(withBuiltin({ version: 2, word_sources: ['builtin'] })),
    });
    renderWithApp(<ContentSafetyPage />);
    const builtin = await screen.findByRole('checkbox', { name: /内置词表/ });
    expect(builtin).not.toBeChecked();
    expect(screen.getByRole('link', { name: '下载查看' })).toHaveAttribute(
      'href',
      `${PATH}/sources/builtin/words`,
    );
    await userEvent.click(builtin);
    await userEvent.click(screen.getByRole('checkbox', { name: /自定义词表/ }));
    expect(screen.queryByRole('textbox', { name: '自定义词表' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', PATH)).toHaveLength(1));
    expect((calls.find('PUT', PATH)[0]?.body as { word_sources: string[] }).word_sources).toEqual([
      'builtin',
    ]);
  });

  it('一种词表都不勾：保存前就说明', async () => {
    const calls = routeFetch({ [`GET ${PATH}`]: jsonResponse(withBuiltin()) });
    renderWithApp(<ContentSafetyPage />);
    await userEvent.click(await screen.findByRole('checkbox', { name: /自定义词表/ }));
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请至少选一种词表');
    expect(calls.find('PUT', PATH)).toHaveLength(0);
  });

  it('上传 txt：读进文本框（拆好、去重），GBK 存的也认得', async () => {
    routeFetch({ [`GET ${PATH}`]: jsonResponse(saved(1)) });
    renderWithApp(<ContentSafetyPage />);
    const words = await screen.findByRole('textbox', { name: '自定义词表' });
    const picker = screen.getByLabelText('选择词表 txt 文件');
    await userEvent.upload(picker, new File(['甲词，乙词\n甲词；丙词'], 'words.txt'));
    await waitFor(() => expect(words).toHaveValue('甲词\n乙词\n丙词'));
    expect(await screen.findByText('已读入 3 个词，检查后点保存')).toBeInTheDocument();
    // 「星河」两个字的 GBK 编码
    const gbk = new Uint8Array([0xd0, 0xc7, 0xba, 0xd3]);
    await userEvent.upload(picker, new File([gbk], 'gbk.txt'));
    await waitFor(() => expect(words).toHaveValue('星河'));
  });
});

describe('内容安全的规则', () => {
  it('拆词：换行、中英文逗号与分号都算分隔，去空白去重', () => {
    expect(splitWords('星河计划\n某某银行，Project X;星河计划；\r\n  客户甲 ,, ')).toEqual([
      '星河计划',
      '某某银行',
      'Project X',
      '客户甲',
    ]);
  });

  it('校验：关闭时不要求填词，上限照样校验', () => {
    const base = {
      version: 0,
      inputEnabled: false,
      outputEnabled: false,
      wordSources: ['custom'],
      words: '',
      blockMessage: '',
    };
    expect(validateContentSafety(base)).toBeUndefined();
    expect(validateContentSafety({ ...base, words: '长'.repeat(257) })).toMatch('超过 256 个字');
    expect(validateContentSafety({ ...base, outputEnabled: true, words: '词' })).toBe(
      '开了检查就要填阻断提示',
    );
  });
});
