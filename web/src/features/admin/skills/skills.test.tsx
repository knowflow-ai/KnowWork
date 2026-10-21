import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch, type RecordedCall } from '../../../test/render';
import { listUsableSkills, type Skill } from '../../../api/skills';
import SkillsPage from './SkillsPage';
import { canPublish, filterSkills, skillState } from './skillsModel';
import { reasonLabel, stageLabel, successRate } from './runsModel';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';

const manifest = (name: string, version = '1.0.0') => ({
  name,
  description: `${name} 的说明`,
  version,
  allowedTools: ['bash', 'present'],
  completionTools: ['present'],
  env: [{ name: 'REPORT_API_KEY', required: true, description: '报表系统的密钥' }],
});

const raw = (over: Record<string, unknown>) => ({
  id: 'sk_1',
  scope: 'workspace',
  name: 'weekly-report',
  description: '汇总本周工作记录生成周报',
  status: 'published',
  enabled: true,
  draftRevision: 3,
  hasUnpublishedChanges: false,
  publishedVersion: '1.0.0',
  draft: manifest('weekly-report'),
  published: manifest('weekly-report'),
  updatedAt: '2026-10-19T08:00:00+00:00',
  ...over,
});

const SKILLS = [
  raw({}),
  raw({
    id: 'sk_2',
    name: 'contract-review',
    description: '逐条核对合同条款',
    status: 'draft',
    publishedVersion: null,
  }),
  raw({ id: 'sk_3', name: 'tender-diff', description: '对比两版标书', enabled: false }),
];

function api(extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    [`GET ${B}/skills`]: jsonResponse({ skills: SKILLS }),
    [`GET ${B}/members`]: jsonResponse([{ user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' }]),
    ...extra,
  });
}

const rowOf = async (name: string) => (await screen.findByText(name)).closest('tr') as HTMLElement;

describe('技能管理页', () => {
  it('列出空间技能：已发布、草稿、已停用三种状态，带「共 N 条」', async () => {
    api();
    renderWithApp(<SkillsPage />);
    expect(within(await rowOf('weekly-report')).getByText('已发布')).toBeInTheDocument();
    expect(within(await rowOf('contract-review')).getByText('草稿')).toBeInTheDocument();
    expect(within(await rowOf('tender-diff')).getByText('已停用')).toBeInTheDocument();
    expect(screen.getByText('共 3 条')).toBeInTheDocument();
  });

  it('上传技能包：把 ZIP 原样发给服务端', async () => {
    const calls = api({ [`POST ${B}/skills`]: jsonResponse(raw({ id: 'sk_9', status: 'draft' }), 201) });
    renderWithApp(<SkillsPage />);
    await rowOf('weekly-report');
    const zip = new File([new Uint8Array([80, 75, 3, 4])], 'skill.zip', { type: 'application/zip' });
    fireEvent.change(screen.getAllByLabelText('选择技能包')[0]!, { target: { files: [zip] } });
    await waitFor(() => expect(calls.find('POST', `${B}/skills`)).toHaveLength(1));
    const posted = calls.fetchMock.mock.calls.map(([, i]) => i).find((i) => i?.method === 'POST');
    expect(posted?.body).toBe(zip);
    expect((posted?.headers as Record<string, string>)['Content-Type']).toBe('application/zip');
    expect(await screen.findByText('技能已上传为草稿，发布后成员才能使用')).toBeInTheDocument();
  });

  it('发布草稿：默认填技能包里的版本号，带上草稿修订号', async () => {
    const calls = api({
      [`POST ${B}/skills/sk_2/publish`]: jsonResponse(
        { id: 'rel_1', version: '2.0.0', releasedBy: 'u1' },
        201,
      ),
    });
    renderWithApp(<SkillsPage />);
    await userEvent.click(within(await rowOf('contract-review')).getByRole('button', { name: '发布' }));
    const dialog = await screen.findByRole('dialog');
    const input = within(dialog).getByPlaceholderText('例如 1.0.0');
    expect(input).toHaveValue('1.0.0');
    await userEvent.clear(input);
    await userEvent.type(input, '2.0.0');
    await userEvent.click(within(dialog).getByRole('button', { name: '发布' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/skills/sk_2/publish`)[0]?.body).toEqual({ version: '2.0.0' }),
    );
    const posted = calls.fetchMock.mock.calls.find(([url]) => String(url).endsWith('/sk_2/publish'))?.[1];
    expect((posted?.headers as Record<string, string>)['If-Match']).toBe('3');
  });

  it('已发布的技能可以停用，停用的可以启用', async () => {
    const calls = api({
      [`POST ${B}/skills/sk_1/disable`]: jsonResponse(raw({ enabled: false })),
      [`POST ${B}/skills/sk_3/enable`]: jsonResponse(raw({ id: 'sk_3' })),
    });
    renderWithApp(<SkillsPage />);
    await userEvent.click(within(await rowOf('weekly-report')).getByRole('button', { name: '停用' }));
    await userEvent.click(within(await rowOf('tender-diff')).getByRole('button', { name: '启用' }));
    await waitFor(() => expect(calls.find('POST', `${B}/skills/sk_1/disable`)).toHaveLength(1));
    await waitFor(() => expect(calls.find('POST', `${B}/skills/sk_3/enable`)).toHaveLength(1));
  });

  it('删除要二次确认', async () => {
    const calls = api({ [`DELETE ${B}/skills/sk_1`]: new Response(null, { status: 204 }) });
    renderWithApp(<SkillsPage />);
    await userEvent.click(within(await rowOf('weekly-report')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '删除' }));
    expect((await screen.findAllByText('删除技能「weekly-report」？')).length).toBeGreaterThan(0);
    expect(calls.find('DELETE', `${B}/skills/sk_1`)).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/skills/sk_1`)).toHaveLength(1));
  });

  it('详情：可调用的工具、完成判据、环境变量与发布记录（发布人显示名字）', async () => {
    api({
      [`GET ${B}/skills/sk_1`]: jsonResponse({
        ...raw({}),
        releases: [
          { id: 'rel_1', version: '1.0.0', releasedBy: 'u1', releasedAt: '2026-10-18T08:00:00+00:00' },
        ],
      }),
    });
    renderWithApp(<SkillsPage />);
    await userEvent.click(within(await rowOf('weekly-report')).getByRole('button', { name: '详情' }));
    const drawer = await screen.findByRole('dialog');
    expect(await within(drawer).findByText('REPORT_API_KEY')).toBeInTheDocument();
    expect(within(drawer).getAllByText('present').length).toBeGreaterThan(0);
    expect(within(drawer).getByText('报表系统的密钥')).toBeInTheDocument();
    expect(await within(drawer).findByText('李雷')).toBeInTheDocument();
  });
});

const DETAIL = { ...raw({}), releases: [] };

const VERSIONS = [
  {
    version: '1.1.0',
    runs: 5,
    succeeded: 2,
    failed: 2,
    cancelled: 1,
    avgDurationMs: 2500,
    avgTokens: 1200,
    topFailureStage: 'knowledge',
    lastRunAt: '2026-10-21T08:00:00+00:00',
  },
];

const run = (over: Record<string, unknown>) => ({
  id: 'r1',
  version: '1.1.0',
  selectionMode: 'manual',
  status: 'succeeded',
  failureStage: null,
  failureCode: null,
  userId: 'u1',
  startedAt: '2026-10-21T08:00:00+00:00',
  durationMs: 3200,
  totalTokens: 900,
  cost: '0.01',
  artifactCount: 1,
  steps: 2,
  failedSteps: 0,
  conversationId: 'c_1',
  turn: 3,
  title: '周报',
  ...over,
});

describe('技能详情的最近运行', () => {
  const openDetail = async () => {
    renderWithApp(<SkillsPage />);
    await userEvent.click(within(await rowOf('weekly-report')).getByRole('button', { name: '详情' }));
    return screen.findByRole('dialog');
  };

  it('不是空间管理员只有概览，不出现最近运行', async () => {
    api({
      [`GET ${B}/skills/sk_1`]: jsonResponse(DETAIL),
      [`GET ${B}/me/check`]: jsonResponse({ allowed: false }),
    });
    const drawer = await openDetail();
    expect(await within(drawer).findByText('REPORT_API_KEY')).toBeInTheDocument();
    expect(within(drawer).queryByRole('tab', { name: '最近运行' })).not.toBeInTheDocument();
  });

  it('管理员看按版本汇总与每次运行，可按状态筛选，点开看那一轮的每一步', async () => {
    const calls = api({
      [`GET ${B}/skills/sk_1`]: jsonResponse(DETAIL),
      [`GET ${B}/me/check`]: jsonResponse({ allowed: true }),
      [`GET ${B}/skills/sk_1/runs/summary`]: jsonResponse({ versions: VERSIONS }),
      [`GET ${B}/skills/sk_1/runs`]: (call: RecordedCall) =>
        jsonResponse({
          items:
            call.query.get('status') === 'failed'
              ? [
                  run({
                    id: 'r2',
                    status: 'failed',
                    failureStage: 'tool',
                    failureCode: 'completion_missing',
                    failedSteps: 1,
                  }),
                ]
              : [run({}), run({ id: 'r3', selectionMode: 'workflow', conversationId: null, turn: null })],
          next: null,
        }),
      [`GET ${B}/runs/c_1/turns/3`]: jsonResponse({ detail: '测试里不展开' }, 404),
    });
    const drawer = await openDetail();
    await userEvent.click(await within(drawer).findByRole('tab', { name: '最近运行' }));
    const versionRow = (await within(drawer).findByText('50%')).closest('tr') as HTMLElement;
    expect(within(versionRow).getByText('1.1.0')).toBeInTheDocument();
    expect(within(versionRow).getByText('知识库')).toBeInTheDocument();
    expect(await within(drawer).findByText('用户调用')).toBeInTheDocument();
    expect(within(drawer).getByText('固定工作流')).toBeInTheDocument();
    const since = calls.find('GET', `${B}/skills/sk_1/runs`)[0]!.query.get('since');
    expect(Date.now() - Date.parse(since!)).toBeGreaterThan(29 * 24 * 3600 * 1000);

    await pickOption(drawer, '状态', '失败');
    expect(await within(drawer).findByText('没完成技能要求的步骤')).toBeInTheDocument();
    expect(within(drawer).getByText('1 次出错')).toBeInTheDocument();
    expect(calls.find('GET', `${B}/skills/sk_1/runs`).at(-1)!.query.get('status')).toBe('failed');

    await userEvent.click(within(drawer).getByText('没完成技能要求的步骤'));
    await waitFor(() => expect(calls.find('GET', `${B}/runs/c_1/turns/3`)).toHaveLength(1));
  });
});

describe('技能显示规则', () => {
  const skills = SKILLS.map((s) => s as unknown as Skill);

  it('停用优先于发布状态；草稿或有未发布修改时可以发布', () => {
    expect(skills.map(skillState)).toEqual(['published', 'draft', 'disabled']);
    expect(canPublish(skills[0]!)).toBe(false);
    expect(canPublish({ ...skills[0]!, hasUnpublishedChanges: true })).toBe(true);
    expect(canPublish(skills[1]!)).toBe(true);
  });

  it('运行的失败阶段、原因与成功率：厂商错误码不露出，取消不算进成功率', () => {
    expect([stageLabel('tool'), stageLabel(null), stageLabel('new-stage')]).toEqual(['工具', '—', '其他']);
    expect(reasonLabel('generation', 'RATE_LIMIT')).toBe('模型调用失败');
    expect(reasonLabel('step', 'completion_missing')).toBe('没完成技能要求的步骤');
    expect(reasonLabel('tool', null)).toBe('');
    expect(successRate(VERSIONS[0] as never)).toBe('50%');
    expect(successRate({ ...VERSIONS[0], succeeded: 0, failed: 0 } as never)).toBe('—');
  });

  it('按名称或说明搜索', () => {
    expect(filterSkills(skills, '  ')).toBe(skills);
    expect(filterSkills(skills, '标书').map((s) => s.name)).toEqual(['tender-diff']);
  });

  it('对话里能选的技能：已发布且启用，个人与空间同名时个人优先', async () => {
    routeFetch({
      [`GET ${B}/me/skills`]: jsonResponse({ skills: [raw({ id: 'mine', scope: 'personal' })] }),
      [`GET ${B}/skills`]: jsonResponse({ skills: SKILLS }),
    });
    const usable = await listUsableSkills();
    expect(usable.map((s) => s.id)).toEqual(['mine']);
  });
});
