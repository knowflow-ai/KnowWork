import { afterEach, describe, expect, it, vi } from 'vitest';
import * as m from './models';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const S = '/api/system';
const ok = () => new Response(null, { status: 204 });

describe('厂商', () => {
  it('校验地址', () => {
    expect(m.validateBaseUrl('')).toBe('请输入地址');
    expect(m.validateBaseUrl('ftp://x')).toMatch(/http/);
    expect(m.validateBaseUrl('https://u:p@x.com')).toMatch(/账号密码/);
    expect(m.validateBaseUrl('https://api.x.com/v1?a=1')).toMatch(/查询串/);
    expect(m.validateBaseUrl('https://api.x.com/v1')).toBeNull();
  });

  it('列表、新建、修改（密钥不传就不改，空串清除）、删除', async () => {
    const api = routeFetch({
      [`GET ${S}/model-providers`]: jsonResponse([
        {
          id: 'p1',
          name: 'DS',
          kind: 'openai_compatible',
          base_url: 'https://a',
          api_key_configured: true,
          api_key_last4: 'abcd',
          enabled: true,
        },
        {},
      ]),
      [`POST ${S}/model-providers`]: jsonResponse({}, 201),
      [`PATCH ${S}/model-providers/*`]: jsonResponse({}),
      [`DELETE ${S}/model-providers/*`]: ok(),
    });
    expect(await m.listProviders()).toEqual([
      {
        id: 'p1',
        name: 'DS',
        kind: 'openai_compatible',
        baseUrl: 'https://a',
        apiKeyConfigured: true,
        apiKeyLast4: 'abcd',
        enabled: true,
      },
    ]);
    await m.createProvider({ name: ' DS ', baseUrl: ' https://a ', apiKey: '', enabled: true });
    await m.updateProvider('p1', { name: 'DS', baseUrl: 'https://a', enabled: false });
    await m.updateProvider('p1', { name: 'DS', baseUrl: 'https://a', enabled: false, apiKey: '' });
    await m.deleteProvider('p1');
    expect(api.calls[1]?.body).toEqual({ name: 'DS', base_url: 'https://a', api_key: null, enabled: true });
    expect(api.calls[2]?.body).not.toHaveProperty('api_key');
    expect(api.calls[3]?.body).toMatchObject({ api_key: '' });
  });
});

describe('模型与分组', () => {
  const model = {
    id: 'm1',
    provider_id: 'p1',
    alias: 'chat',
    name: 'deepseek-chat',
    capabilities: ['chat'],
    context_length: 64000,
    max_output_tokens: 8192,
    input_price_per_mtok: '2.000000',
    output_price_per_mtok: 8,
    currency: 'CNY',
    enabled: true,
  };

  it('解析模型（单价可能是字符串）并按约定增改删、测试', async () => {
    const api = routeFetch({
      [`GET ${S}/models`]: jsonResponse([model, { id: 'x' }]),
      [`POST ${S}/models`]: jsonResponse({}, 201),
      [`PATCH ${S}/models/*`]: jsonResponse({}),
      [`DELETE ${S}/models/*`]: ok(),
      [`POST ${S}/models/*/test`]: jsonResponse({
        ok: true,
        capability: 'chat',
        latency_ms: 120,
        upstream_status: 200,
        message: '',
      }),
    });
    const [parsed] = await m.listModels();
    expect(parsed).toMatchObject({
      alias: 'chat',
      inputPrice: 2,
      outputPrice: 8,
      contextLength: 64000,
      maxOutputTokens: 8192,
      thinking: null,
    });
    const input = {
      providerId: 'p1',
      alias: ' chat ',
      name: ' n ',
      capabilities: ['chat'],
      contextLength: null,
      maxOutputTokens: 4096,
      thinking: 'off' as const,
      inputPrice: 1,
      outputPrice: null,
      enabled: true,
    };
    await m.createModel(input);
    await m.updateModel('m1', input);
    await m.deleteModel('m1');
    await expect(m.testModel('m1')).resolves.toEqual({ ok: true, latencyMs: 120, message: '' });
    expect(api.calls[1]?.body).toMatchObject({
      provider_id: 'p1',
      alias: 'chat',
      name: 'n',
      input_price_per_mtok: 1,
      max_output_tokens: 4096,
      thinking: 'off',
    });
    expect(api.calls[2]?.body).not.toHaveProperty('provider_id');
    expect(m.capabilityLabel('vision')).toBe('看图');
    expect(m.capabilityLabel('x')).toBe('x');
  });

  it('测试结果不完整时说明', async () => {
    routeFetch({ [`POST ${S}/models/*/test`]: jsonResponse('x') });
    await expect(m.testModel('m1')).resolves.toMatchObject({ ok: false });
  });
});

describe('用量', () => {
  it('全空间与个人用量按查询参数调用', async () => {
    const row = {
      key: 'u1',
      calls: 3,
      input_tokens: 10,
      output_tokens: 5,
      total_tokens: 15,
      cost: '0.120000',
    };
    const api = routeFetch({
      [`GET ${B}/usage`]: jsonResponse([row, {}]),
      [`GET ${B}/usage/me`]: jsonResponse([row]),
    });
    const query = { start: '2026-10-01', end: '2026-10-19', groupBy: 'user' as const };
    expect(await m.fetchUsage(query)).toEqual([
      { key: 'u1', calls: 3, inputTokens: 10, outputTokens: 5, totalTokens: 15, cost: 0.12 },
    ]);
    await m.fetchMyUsage({ ...query, groupBy: 'day' });
    expect(api.calls[0]?.query.toString()).toBe('start=2026-10-01&end=2026-10-19&group_by=user');
    expect(api.calls[1]?.query.get('group_by')).toBe('day');
  });
});
