import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import KnowledgePage from './KnowledgePage';
import { toSourceInput } from './sourceForm';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/knowledge-sources';
const DETAIL = {
  id: 's1',
  name: '制度库',
  kind: 'knowflow',
  enabled: true,
  auth_mode: 'assertion',
  base_url: 'https://kf.corp.cn',
  config: {
    auth_mode: 'assertion',
    dataset_ids: [],
    web_base_url: null,
    similarity_threshold: 0.2,
    vector_similarity_weight: 0.3,
    timeout_seconds: 15,
  },
  service_key: { configured: true, last4: '9xyz' },
  created_by: 'u1',
};
const SUMMARY = {
  id: 's2',
  name: '销售资料',
  kind: 'knowflow',
  enabled: true,
  auth_mode: 'shared',
};

function api(extra = {}) {
  return routeFetch({
    [`GET ${B}`]: jsonResponse([DETAIL, SUMMARY]),
    [`POST ${B}`]: jsonResponse(DETAIL, 201),
    [`PATCH ${B}/*`]: jsonResponse(DETAIL),
    [`DELETE ${B}/*`]: new Response(null, { status: 204 }),
    [`POST ${B}/*/test`]: jsonResponse({ ok: true, message: '连接正常' }),
    ...extra,
  });
}

const fill = (dialog: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

describe('KnowledgePage', () => {
  it('可编辑的显示地址与操作，只能使用的不显示', async () => {
    const calls = api();
    renderWithApp(<KnowledgePage />);
    const row = (await screen.findByText('制度库')).closest('tr') as HTMLElement;
    expect(within(row).getByText('https://kf.corp.cn')).toBeInTheDocument();
    const other = screen.getByText('销售资料').closest('tr') as HTMLElement;
    expect(within(other).getByText('只能使用')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '测试' }));
    expect(await screen.findByText('「制度库」连接正常')).toBeInTheDocument();
    expect(calls.find('POST', `${B}/s1/test`)).toHaveLength(1);
  });

  it('添加知识源：默认统一账号，不提供签名断言，必须给 KnowFlow API Key', async () => {
    const calls = api();
    renderWithApp(<KnowledgePage />);
    await screen.findByText('制度库');
    await userEvent.click(screen.getByRole('button', { name: /添加知识源/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: '统一账号' })).toBeChecked();
    expect(within(dialog).queryByRole('radio', { name: /签名断言/ })).toBeNull();
    fill(dialog, '名称', '产品手册');
    fill(dialog, 'KnowFlow 地址', 'https://kf2.corp.cn');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('统一账号方式必须配置 KnowFlow API Key');
    fill(dialog, 'KnowFlow API Key', 'ragflow-sharedkey');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        name: '产品手册',
        base_url: 'https://kf2.corp.cn',
        account_key: 'ragflow-sharedkey',
        config: { auth_mode: 'shared', dataset_ids: [] },
        kind: 'knowflow',
      }),
    );
  });

  it('统一账号：只显示 KnowFlow API Key，开放的知识库可留空也可指定', async () => {
    const calls = api();
    renderWithApp(<KnowledgePage />);
    await screen.findByText('制度库');
    await userEvent.click(screen.getByRole('button', { name: /添加知识源/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '共享库');
    fill(dialog, 'KnowFlow 地址', 'https://kf2.corp.cn');
    expect(within(dialog).queryByLabelText('服务密钥')).toBeNull();
    fill(dialog, 'KnowFlow API Key', 'ragflow-sharedkey');
    expect(within(dialog).getByText(/这个账号在 KnowFlow 读得到的全部知识库/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText('开放的知识库 ID'), 'd1{enter}');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        account_key: 'ragflow-sharedkey',
        config: { auth_mode: 'shared', dataset_ids: ['d1'] },
      }),
    );
    expect(calls.find('POST', B)[0]?.body).not.toHaveProperty('service_key');
  });

  it('还在用签名断言的知识源，修改时可以改成统一账号，要重新填 Key', async () => {
    const calls = api();
    renderWithApp(<KnowledgePage />);
    const row = (await screen.findByText('制度库')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: /签名断言/ })).toBeChecked();
    await userEvent.click(within(dialog).getByRole('radio', { name: '统一账号' }));
    expect(within(dialog).queryByLabelText('服务密钥')).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('统一账号方式必须配置 KnowFlow API Key');
    fill(dialog, 'KnowFlow API Key', 'ragflow-sharedkey');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/s1`)[0]?.body).toMatchObject({
        account_key: 'ragflow-sharedkey',
        config: { auth_mode: 'shared' },
      }),
    );
    expect(calls.find('PATCH', `${B}/s1`)[0]?.body).not.toHaveProperty('service_key');
  });

  it('修改时留空不改密钥；删除需确认', async () => {
    const calls = api();
    renderWithApp(<KnowledgePage />);
    const row = (await screen.findByText('制度库')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('已配置（末四位 9xyz），留空表示不修改')).toBeInTheDocument();
    fill(dialog, '名称', '公司制度库');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/s1`)[0]?.body).toMatchObject({ name: '公司制度库', enabled: true }),
    );
    expect(calls.find('PATCH', `${B}/s1`)[0]?.body).not.toHaveProperty('service_key');
    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除知识源「制度库」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/s1`)).toHaveLength(1));
  });

  it('测试不通时说明', async () => {
    api({ [`POST ${B}/*/test`]: jsonResponse({ ok: false, message: 'KnowFlow 拒绝了签名' }) });
    renderWithApp(<KnowledgePage />);
    const row = (await screen.findByText('制度库')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '测试' }));
    expect(await screen.findByText('「制度库」不通：KnowFlow 拒绝了签名')).toBeInTheDocument();
  });

  it('toSourceInput 清理空白与空项', () => {
    const input = toSourceInput({
      name: 'x',
      baseUrl: 'https://a',
      enabled: true,
      authMode: 'shared',
      serviceKey: '  ',
      datasetIds: [' d1 ', ''],
      webBaseUrl: ' ',
      similarityThreshold: 0.2,
      vectorSimilarityWeight: 0.3,
      timeoutSeconds: 15,
    });
    expect(input.serviceKey).toBeUndefined();
    expect(input.config).toMatchObject({ datasetIds: ['d1'], webBaseUrl: null, authMode: 'shared' });
  });
});
