import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickTreeNode } from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { orgPath, orgTree, type OrgDepartment } from '../../../api/org';
import MembersPage from './MembersPage';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const MEMBERS = [
  { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn', origin: 'manual' },
  { user_id: 'u2', role: 'editor', name: '孙七', email: null, origin: 'rule' },
];
const ORG = [
  { id: 'o1', source_id: 'ad', external_id: 'corp', parent_id: null, name: '集团', sort: 0, member_count: 0 },
  {
    id: 'o2',
    source_id: 'ad',
    external_id: 'rnd',
    parent_id: 'o1',
    name: '研发中心',
    sort: 0,
    member_count: 3,
  },
];
const RULE = {
  id: 'mr_1',
  department_id: 'o2',
  department_name: '研发中心',
  source_id: 'ad',
  department_active: true,
  include_children: true,
  role: 'editor',
  created_at: null,
};

function api(org: readonly object[] = ORG, rules: readonly object[] = [], extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    [`GET ${B}/members`]: jsonResponse(MEMBERS),
    [`GET ${B}/org-departments`]: jsonResponse(org),
    [`GET ${B}/member-rules`]: jsonResponse(rules),
    ...extra,
  });
}

describe('成员规则', () => {
  it('没接组织目录时页面和原来一样，没有页签', async () => {
    api([]);
    renderWithApp(<MembersPage />);
    expect(await screen.findByText('李雷')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '成员规则' })).not.toBeInTheDocument();
  });

  it('规则加进来的人标出来、不能手工移出；没有邮箱的人不写「没有取到邮箱」', async () => {
    api();
    renderWithApp(<MembersPage />);
    const row = (await screen.findByText('孙七')).closest('tr') as HTMLElement;
    expect(within(row).getByText('按成员规则加入')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: '移出' })).toBeDisabled();
    expect(screen.queryByText('没有取到邮箱')).not.toBeInTheDocument();
  });

  it('添加规则：从组织架构里选部门与角色', async () => {
    const calls = api(ORG, [], { [`POST ${B}/member-rules`]: jsonResponse(RULE, 201) });
    renderWithApp(<MembersPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '成员规则' }));
    await userEvent.click(await screen.findByRole('button', { name: '添加规则' }));
    const dialog = await screen.findByRole('dialog', { name: '添加成员规则' });
    await userEvent.click(within(dialog).getByRole('button', { name: '添加' }));
    expect(await within(dialog).findByText('请从组织架构里选部门')).toBeInTheDocument();
    await pickTreeNode(dialog, '部门', '研发中心（3 人）');
    await userEvent.click(within(dialog).getByRole('radio', { name: '管理员' }));
    await userEvent.click(within(dialog).getByRole('button', { name: '添加' }));
    await waitFor(() => expect(calls.find('POST', `${B}/member-rules`)).toHaveLength(1));
    expect(calls.find('POST', `${B}/member-rules`)[0]?.body).toEqual({
      department_id: 'o2',
      include_children: true,
      role: 'admin',
    });
  });

  it('列出规则：部门写完整路径，目录里删了的标出来；删除要确认', async () => {
    const gone = {
      ...RULE,
      id: 'mr_2',
      department_id: 'o9',
      department_name: '旧部门',
      department_active: false,
    };
    const calls = api(ORG, [RULE, gone], {
      [`DELETE ${B}/member-rules/mr_1`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<MembersPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '成员规则' }));
    const row = (await screen.findByText('集团 / 研发中心')).closest('tr') as HTMLElement;
    expect(screen.getByText('已从目录移除，不再加人')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除「研发中心」这条规则/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/member-rules/mr_1`)).toHaveLength(1));
  });
});

describe('组织架构的树', () => {
  const dept = (id: string, name: string, parentId: string | null, sort = 0): OrgDepartment => ({
    id,
    sourceId: 'ad',
    parentId,
    name,
    sort,
    memberCount: 0,
  });

  it('按上级成树，上级不在的当顶层，同级按顺序排', () => {
    const list = [
      dept('a', '集团', null),
      dept('c', '研发', 'a', 2),
      dept('b', '总部', 'a', 1),
      dept('x', '孤儿', 'gone'),
    ];
    const tree = orgTree(list);
    expect(tree.map((n) => n.department.name)).toEqual(['孤儿', '集团']);
    expect(tree[1]?.children.map((n) => n.department.name)).toEqual(['总部', '研发']);
    expect(orgPath('c', list)).toBe('集团 / 研发');
  });
});
