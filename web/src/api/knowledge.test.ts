import { afterEach, describe, expect, it, vi } from 'vitest';
import * as k from './knowledge';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/knowledge-sources';
const config: k.SourceConfig = {
  authMode: 'assertion',
  datasetIds: [],
  webBaseUrl: null,
  similarityThreshold: 0.2,
  vectorSimilarityWeight: 0.3,
  timeoutSeconds: 15,
};
const input = (patch: Partial<k.SourceInput> = {}): k.SourceInput => ({
  name: '制度库',
  baseUrl: 'https://kf.corp.cn',
  enabled: true,
  config,
  serviceKey: 'k'.repeat(32),
  ...patch,
});

describe('知识源', () => {
  it('校验输入', () => {
    const none = undefined;
    const storedAssertion = { authMode: 'assertion', configured: true } as const;
    const shared = { ...config, authMode: 'shared', datasetIds: ['d1'] } as const;
    expect(k.validateSourceInput(input({ name: ' ' }), none)).toMatch(/名称/);
    expect(k.validateSourceInput(input({ baseUrl: 'kf.corp.cn' }), none)).toMatch(/http/);
    expect(k.validateSourceInput(input({ config: { ...config, datasetIds: ['a b'] } }), none)).toBe(
      '知识库 ID 格式不合法',
    );
    expect(k.validateSourceInput(input({ serviceKey: 'short' }), none)).toMatch(/至少 32/);
    expect(k.validateSourceInput(input({ serviceKey: undefined }), none)).toBe(
      '签名断言方式必须配置服务密钥',
    );
    expect(k.validateSourceInput(input({ serviceKey: undefined }), storedAssertion)).toBeNull();
    // 统一账号：开放范围可以留空（那个账号读得到的全部）；换到这种方式时，旧的服务密钥不算数
    expect(
      k.validateSourceInput(
        input({ config: { ...shared, datasetIds: [] }, accountKey: 'kf-key-1234' }),
        none,
      ),
    ).toBeNull();
    expect(k.validateSourceInput(input({ config: shared }), storedAssertion)).toBe(
      '统一账号方式必须配置 KnowFlow API Key',
    );
    expect(k.validateSourceInput(input({ config: shared, accountKey: 'short' }), none)).toMatch(/至少 8/);
    expect(k.validateSourceInput(input({ config: shared, accountKey: 'kf-key-1234' }), none)).toBeNull();
    expect(
      k.validateSourceInput(input({ config: shared }), { authMode: 'shared', configured: true }),
    ).toBeNull();
  });

  it('统一账号只提交它自己的 Key，并解析 Key 状态', async () => {
    const api = routeFetch({
      [`POST ${B}`]: jsonResponse({}, 201),
      [`GET ${B}`]: jsonResponse([
        {
          id: 's3',
          name: '共享',
          kind: 'knowflow',
          enabled: true,
          auth_mode: 'shared',
          base_url: 'https://kf',
          config: { auth_mode: 'shared', dataset_ids: ['d1'] },
          service_key: { configured: false, last4: '' },
          account_key: { configured: true, last4: '9876' },
          created_by: 'u',
        },
      ]),
    });
    await k.createSource(
      input({ config: { ...config, authMode: 'shared', datasetIds: ['d1'] }, accountKey: 'kf-key-1234' }),
    );
    expect(api.calls[0]?.body).toMatchObject({ account_key: 'kf-key-1234' });
    expect(api.calls[0]?.body).not.toHaveProperty('service_key');
    const [source] = await k.listSources();
    expect(source?.authMode).toBe('shared');
    expect(source?.detail?.accountKey).toEqual({ configured: true, last4: '9876' });
  });

  it('按知识源分组列出知识库', async () => {
    routeFetch({
      'GET /api/workspaces/default/knowledge-bases': jsonResponse([
        {
          source_id: 's1',
          source_name: '合同库',
          auth_mode: 'shared',
          enabled: true,
          error: null,
          bases: [{ id: 's1.d1', kb_id: 'd1', name: '采购合同' }, { id: 'bad' }],
        },
        {
          source_id: 's2',
          source_name: '制度库',
          auth_mode: 'assertion',
          enabled: false,
          error: '暂时不可用',
          bases: [],
        },
        { bad: true },
      ]),
    });
    expect(await k.listKnowledgeBases()).toEqual([
      {
        sourceId: 's1',
        sourceName: '合同库',
        authMode: 'shared',
        enabled: true,
        error: null,
        bases: [{ id: 's1.d1', kbId: 'd1', name: '采购合同' }],
      },
      {
        sourceId: 's2',
        sourceName: '制度库',
        authMode: 'assertion',
        enabled: false,
        error: '暂时不可用',
        bases: [],
      },
    ]);
  });

  it('按权限解析摘要与详情', async () => {
    routeFetch({
      [`GET ${B}`]: jsonResponse([
        {
          id: 's1',
          name: '摘要',
          kind: 'knowflow',
          enabled: true,
          auth_mode: 'api_key', // 已去掉的方式按统一账号显示
        },
        {
          id: 's2',
          name: '详情',
          kind: 'knowflow',
          enabled: false,
          auth_mode: 'assertion',
          base_url: 'https://kf',
          config: { auth_mode: 'assertion', dataset_ids: ['d1'] },
          service_key: { configured: true, last4: '1234' },
          created_by: 'u',
        },
        { name: '坏' },
      ]),
    });
    const [summary, detail] = await k.listSources();
    expect(summary).toEqual({
      id: 's1',
      name: '摘要',
      kind: 'knowflow',
      enabled: true,
      authMode: 'shared',
      detail: null,
    });
    expect(detail?.detail).toMatchObject({
      baseUrl: 'https://kf',
      serviceKey: { last4: '1234' },
      config: { datasetIds: ['d1'], timeoutSeconds: 15 },
    });
  });

  it('新建不带 enabled，修改带上；测试、删除', async () => {
    const api = routeFetch({
      [`POST ${B}`]: jsonResponse({}, 201),
      [`PATCH ${B}/*`]: jsonResponse({}),
      [`DELETE ${B}/*`]: new Response(null, { status: 204 }),
      [`POST ${B}/*/test`]: jsonResponse({ ok: false, message: 'KnowFlow 不可达' }),
    });
    await k.createSource(input({ config: { ...config, webBaseUrl: '  ' } }));
    await k.updateSource('s1', input({ serviceKey: undefined }));
    await expect(k.testSource('s1')).resolves.toEqual({ ok: false, message: 'KnowFlow 不可达' });
    await k.deleteSource('s1');
    expect(api.calls[0]?.body).toEqual({
      name: '制度库',
      base_url: 'https://kf.corp.cn',
      kind: 'knowflow',
      service_key: 'k'.repeat(32),
      config: {
        auth_mode: 'assertion',
        dataset_ids: [],
        web_base_url: null,
        similarity_threshold: 0.2,
        vector_similarity_weight: 0.3,
        timeout_seconds: 15,
      },
    });
    expect(api.calls[1]?.body).toMatchObject({ enabled: true });
    expect(api.calls[1]?.body).not.toHaveProperty('service_key');
  });

  it('测试结果不完整时说明', async () => {
    routeFetch({ [`POST ${B}/*/test`]: jsonResponse(null) });
    await expect(k.testSource('s1')).resolves.toMatchObject({ ok: false });
  });
});
