import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import ModelsPage from './ModelsPage';
import { providerInput } from './providerForm';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const ok = () => new Response(null, { status: 204 });
const PROVIDER = {
  id: 'p1',
  name: 'DeepSeek',
  kind: 'openai_compatible',
  base_url: 'https://api.deepseek.com/v1',
  api_key_configured: true,
  api_key_last4: 'abcd',
  enabled: true,
};
const MODEL = {
  id: 'm1',
  provider_id: 'p1',
  alias: 'chat-default',
  name: 'deepseek-chat',
  capabilities: ['chat'],
  context_length: 64000,
  input_price_per_mtok: 2,
  output_price_per_mtok: null,
  currency: 'CNY',
  enabled: true,
};

function api(extra = {}) {
  return routeFetch({
    [`GET ${B}/model-providers`]: jsonResponse([PROVIDER]),
    [`GET ${B}/models`]: jsonResponse([MODEL]),
    [`GET ${B}/model-groups`]: jsonResponse([
      { id: 'g1', name: '通用', description: '日常对话', model_ids: ['m1'] },
    ]),
    [`POST ${B}/model-providers`]: jsonResponse({}, 201),
    [`PATCH ${B}/model-providers/*`]: jsonResponse({}),
    [`DELETE ${B}/model-providers/*`]: ok(),
    [`POST ${B}/models`]: jsonResponse({}, 201),
    [`PATCH ${B}/models/*`]: jsonResponse({}),
    [`DELETE ${B}/models/*`]: ok(),
    [`POST ${B}/models/*/test`]: jsonResponse({ ok: true, latency_ms: 85, message: '' }),
    [`POST ${B}/model-groups`]: jsonResponse({}, 201),
    [`PATCH ${B}/model-groups/*`]: jsonResponse({}),
    [`DELETE ${B}/model-groups/*`]: ok(),
    ...extra,
  });
}

const fill = (dialog: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

describe('模型', () => {
  it('列出模型、厂商名与单价，连通测试给出结果', async () => {
    const calls = api();
    renderWithApp(<ModelsPage />);
    const row = (await screen.findByText('chat-default')).closest('tr') as HTMLElement;
    expect(within(row).getByText('deepseek-chat')).toBeInTheDocument();
    expect(await within(row).findByText('DeepSeek')).toBeInTheDocument();
    expect(within(row).getByText('2 CNY / —')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '测试' }));
    expect(await screen.findByText('「chat-default」连接正常，耗时 85 毫秒')).toBeInTheDocument();
    expect(calls.find('POST', `${B}/models/m1/test`)).toHaveLength(1);
  });

  it('测试不通时说明原因', async () => {
    api({
      [`POST ${B}/models/*/test`]: jsonResponse({ ok: false, latency_ms: null, message: '上游返回 401' }),
    });
    renderWithApp(<ModelsPage />);
    const row = (await screen.findByText('chat-default')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '测试' }));
    expect(await screen.findByText('「chat-default」不通：上游返回 401')).toBeInTheDocument();
  });

  it('添加与修改模型、删除需确认', async () => {
    const calls = api();
    renderWithApp(<ModelsPage />);
    await screen.findByText('chat-default');
    await waitFor(() => expect(screen.getByRole('button', { name: /添加模型/ })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: /添加模型/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '别名', 'bad alias');
    fill(dialog, '上游模型名', 'deepseek-reasoner');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText('只能用字母、数字与 _ . : -，最长 64 个字符')).toBeInTheDocument();
    fill(dialog, '别名', 'reasoner');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/models`)[0]?.body).toMatchObject({
        provider_id: 'p1',
        alias: 'reasoner',
        name: 'deepseek-reasoner',
        capabilities: ['chat'],
      }),
    );

    const row = screen.getByText('chat-default').closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const edit = await screen.findByRole('dialog');
    fill(edit, '上游模型名', 'deepseek-chat-v3');
    await userEvent.click(within(edit).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/models/m1`)[0]?.body).toMatchObject({
        name: 'deepseek-chat-v3',
        input_price_per_mtok: 2,
      }),
    );

    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除模型「chat-default」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/models/m1`)).toHaveLength(1));
  });
});

describe('厂商', () => {
  it('显示密钥末四位；添加时校验地址；修改留空不改密钥', async () => {
    const calls = api();
    renderWithApp(<ModelsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '厂商' }));
    const row = (await screen.findByText('https://api.deepseek.com/v1')).closest('tr') as HTMLElement;
    expect(within(row).getByText('已配置 ··abcd')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /添加厂商/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '通义');
    fill(dialog, 'API 地址', 'dashscope.aliyuncs.com');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText(/地址须为 http/)).toBeInTheDocument();
    fill(dialog, 'API 地址', 'https://dashscope.aliyuncs.com/compatible-mode/v1');
    fill(dialog, 'API Key', 'sk-test');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/model-providers`)[0]?.body).toEqual({
        name: '通义',
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key: 'sk-test',
        enabled: true,
      }),
    );

    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const edit = await screen.findByRole('dialog');
    expect(within(edit).getByText('已配置（末四位 abcd），留空表示不修改')).toBeInTheDocument();
    await userEvent.click(within(edit).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/model-providers/p1`)[0]?.body).not.toHaveProperty('api_key'),
    );

    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除厂商「DeepSeek」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/model-providers/p1`)).toHaveLength(1));
  });

  it('providerInput：新密钥、清除与不改三种情况', () => {
    const values = { name: 'x', baseUrl: 'https://a', enabled: true };
    expect(providerInput({ ...values, apiKey: ' k ' }, true, false).apiKey).toBe('k');
    expect(providerInput(values, true, true).apiKey).toBe('');
    expect(providerInput(values, true, false).apiKey).toBeUndefined();
    expect(providerInput(values, false, false).apiKey).toBe('');
  });
});

describe('分组', () => {
  it('新建、修改与删除分组', async () => {
    const calls = api();
    renderWithApp(<ModelsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '分组' }));
    const row = (await screen.findByText('日常对话')).closest('tr') as HTMLElement;
    await userEvent.click(screen.getByRole('button', { name: /新建分组/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '推理');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/model-groups`)[0]?.body).toEqual({
        name: '推理',
        description: '',
        model_ids: [],
      }),
    );
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const edit = await screen.findByRole('dialog');
    fill(edit, '说明', '日常对话与写作');
    await userEvent.click(within(edit).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/model-groups/g1`)[0]?.body).toEqual({
        name: '通用',
        description: '日常对话与写作',
        model_ids: ['m1'],
      }),
    );
    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除分组「通用」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/model-groups/g1`)).toHaveLength(1));
  });

  it('删除失败时提示后端原因', async () => {
    api({ [`DELETE ${B}/model-groups/*`]: jsonResponse({ detail: '分组还在被引用' }, 409) });
    renderWithApp(<ModelsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '分组' }));
    const row = (await screen.findByText('日常对话')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除分组「通用」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    expect(await screen.findByText('分组还在被引用')).toBeInTheDocument();
  });
});
