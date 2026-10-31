import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import SkillsPage from './SkillsPage';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';

const mine = (over: Record<string, unknown>) => ({
  id: 'p1',
  scope: 'personal',
  name: 'my-notes',
  description: '整理会议纪要',
  status: 'published',
  enabled: true,
  draftRevision: 1,
  hasUnpublishedChanges: false,
  publishedVersion: '1.0.0',
  draft: null,
  published: null,
  updatedAt: '2026-10-29T08:00:00+00:00',
  submission: null,
  inactive: null,
  ...over,
});

const submission = (over: Record<string, unknown>) => ({
  id: 'sub1',
  skillId: 'p1',
  name: 'my-notes',
  version: '1.0.0',
  note: '',
  submittedBy: 'u1',
  status: 'pending',
  reviewNote: '',
  createdAt: '2026-10-29T08:00:00+00:00',
  ...over,
});

const MINE = [
  mine({}),
  mine({ id: 'p2', name: 'summary', inactive: 'name_taken' }),
  mine({
    id: 'p3',
    name: 'tender-diff',
    submission: submission({ skillId: 'p3', status: 'rejected', reviewNote: '和已有技能重复' }),
  }),
  mine({ id: 'p4', name: 'weekly', submission: submission({ skillId: 'p4' }) }),
];

function api(extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    [`GET ${B}/me/skills/available`]: jsonResponse({
      skills: [
        { name: 'contract-review', description: '逐条核对合同', source: 'workspace', version: '2.0.0' },
        { name: 'office-docx', description: '处理 Word 文档', source: 'bundled', version: '' },
        { name: 'my-notes', description: '整理会议纪要', source: 'personal', version: '1.0.0' },
      ],
    }),
    [`GET ${B}/me/skills`]: jsonResponse({ skills: MINE }),
    ...extra,
  });
}

const rowOf = async (name: string) => (await screen.findByText(name)).closest('tr') as HTMLElement;

describe('技能页（使用端）', () => {
  it('「可以用的」列出空间、自带、我的技能，来源只写文字', async () => {
    api();
    renderWithApp(<SkillsPage />, { route: '/skills' });
    expect(within(await rowOf('contract-review')).getByText('空间')).toBeInTheDocument();
    expect(within(await rowOf('office-docx')).getByText('内置')).toBeInTheDocument();
    expect(within(await rowOf('my-notes')).getByText('我的')).toBeInTheDocument();
  });

  it('「我的技能」写明没生效的原因与提交进度', async () => {
    api();
    renderWithApp(<SkillsPage />, { route: '/skills?tab=mine' });
    const shadowed = await rowOf('summary');
    expect(within(shadowed).getByText('没生效')).toBeInTheDocument();
    expect(within(shadowed).getByText('有同名的空间技能或企业技能，对话里用的是那一份')).toBeInTheDocument();
    expect(within(await rowOf('tender-diff')).getByText('和已有技能重复')).toBeInTheDocument();
    expect(within(await rowOf('weekly')).getByText('待审核')).toBeInTheDocument();
  });

  it('上传技能包：发到个人技能，提示上传即可用', async () => {
    const calls = api({ [`POST ${B}/me/skills`]: jsonResponse(mine({ id: 'p9' }), 201) });
    renderWithApp(<SkillsPage />, { route: '/skills?tab=mine' });
    await rowOf('my-notes');
    const zip = new File([new Uint8Array([80, 75, 3, 4])], 'skill.zip', { type: 'application/zip' });
    fireEvent.change(screen.getAllByLabelText('选择技能包')[0]!, { target: { files: [zip] } });
    await waitFor(() => expect(calls.find('POST', `${B}/me/skills`)).toHaveLength(1));
    expect(await screen.findByText('已上传，新对话里就能用了')).toBeInTheDocument();
  });

  it('提交到空间带上附言；待审的可以撤回', async () => {
    const calls = api({
      [`POST ${B}/me/skills/p1/submission`]: jsonResponse(submission({}), 201),
      [`DELETE ${B}/me/skills/p4/submission`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<SkillsPage />, { route: '/skills?tab=mine' });
    await userEvent.click(within(await rowOf('my-notes')).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '提交到空间' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('给审核人的话'), '部门都用得上');
    await userEvent.click(within(dialog).getByRole('button', { name: '提交' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/me/skills/p1/submission`)[0]?.body).toEqual({ note: '部门都用得上' }),
    );

    await userEvent.click(within(await rowOf('weekly')).getByRole('button', { name: /更多/ }));
    expect(screen.queryByRole('menuitem', { name: '提交到空间' })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole('menuitem', { name: '撤回提交' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/me/skills/p4/submission`)).toHaveLength(1));
  });
});
