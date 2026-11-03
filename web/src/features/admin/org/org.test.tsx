import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import OrgPage from './OrgPage';
import { toTreeData } from './helpers';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const ok = () => new Response(null, { status: 204 });
const TREE = [
  {
    id: 'd1',
    name: '总部',
    parent_id: null,
    members: [['u1', true]],
    children: [{ id: 'd2', name: '法务部', parent_id: 'd1', members: [], children: [] }],
  },
];
const MEMBERS = [
  { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
  { user_id: 'u2', role: 'editor', name: '韩梅梅', email: 'han@corp.cn' },
];

function api(extra = {}) {
  return routeFetch({
    [`GET ${B}/departments`]: jsonResponse(TREE),
    [`GET ${B}/org-departments`]: jsonResponse([]),
    [`GET ${B}/members`]: jsonResponse(MEMBERS),
    [`GET ${B}/groups`]: jsonResponse([{ id: 'g1', name: '客服', members: ['u1'] }]),
    [`POST ${B}/departments`]: jsonResponse({ id: 'd3' }, 201),
    [`PATCH ${B}/departments/*`]: ok(),
    [`DELETE ${B}/departments/*`]: ok(),
    [`POST ${B}/departments/*/members`]: ok(),
    [`DELETE ${B}/departments/*/members/*`]: ok(),
    [`POST ${B}/groups`]: jsonResponse({ id: 'g2' }, 201),
    [`PATCH ${B}/groups/*`]: ok(),
    [`DELETE ${B}/groups/*`]: ok(),
    [`POST ${B}/groups/*/members`]: ok(),
    [`DELETE ${B}/groups/*/members/*`]: ok(),
    ...extra,
  });
}

async function nameDialog(value: string, okText: string) {
  const dialog = await screen.findByRole('dialog');
  const input = within(dialog).getByRole('textbox');
  fireEvent.change(input, { target: { value } });
  await userEvent.click(within(dialog).getByRole('button', { name: okText }));
}

describe('部门', () => {
  it('树显示部门与人数，选中后看成员、加人、移出', async () => {
    const calls = api();
    renderWithApp(<OrgPage />);
    await userEvent.click(await screen.findByText('总部（1）'));
    const panel = await screen.findByRole('region', { name: '部门：总部' });
    expect(within(panel).getByText('李雷')).toBeInTheDocument();
    expect(within(panel).getByText('主部门')).toBeInTheDocument();
    await userEvent.click(within(within(panel).getByRole('group', { name: '选择成员' })).getByRole('button'));
    await userEvent.click(await screen.findByRole('option', { name: '韩梅梅（han@corp.cn）' }));
    await userEvent.click(within(panel).getByRole('checkbox', { name: '设为主部门' }));
    await userEvent.click(within(panel).getByRole('button', { name: '加入部门' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/departments/d1/members`)[0]?.body).toEqual({
        user_id: 'u2',
        is_primary: true,
      }),
    );
    await userEvent.click(within(panel).getByRole('button', { name: '移出' }));
    const pop = await screen.findByRole('alertdialog', { name: '把「李雷」移出这个部门？' });
    await userEvent.click(within(pop).getByRole('button', { name: '移出' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/departments/d1/members/u1`)).toHaveLength(1));
  });

  it('新建根部门、子部门，重命名与删除', async () => {
    const calls = api();
    renderWithApp(<OrgPage />);
    await userEvent.click(await screen.findByRole('button', { name: /新建部门/ }));
    await nameDialog('研发部', '新建');
    await waitFor(() =>
      expect(calls.find('POST', `${B}/departments`)[0]?.body).toEqual({ name: '研发部', parent_id: null }),
    );
    await userEvent.click(screen.getByText('法务部（0）'));
    const panel = await screen.findByRole('region', { name: '部门：法务部' });
    expect(within(panel).getByText('这个部门还没有成员')).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole('button', { name: '新建子部门' }));
    await nameDialog('合规组', '新建');
    await waitFor(() =>
      expect(calls.find('POST', `${B}/departments`)[1]?.body).toEqual({ name: '合规组', parent_id: 'd2' }),
    );
    await userEvent.click(within(panel).getByRole('button', { name: '重命名' }));
    await nameDialog('法务合规部', '保存');
    await waitFor(() =>
      expect(calls.find('PATCH', `${B}/departments/d2`)[0]?.body).toEqual({ name: '法务合规部' }),
    );
    await userEvent.click(within(panel).getByRole('button', { name: '删除' }));
    const pop = await screen.findByRole('alertdialog', { name: /删除部门「法务部」/ });
    await userEvent.click(within(pop).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/departments/d2`)).toHaveLength(1));
  });

  it('没有部门时显示空状态', async () => {
    api({ [`GET ${B}/departments`]: jsonResponse([]) });
    renderWithApp(<OrgPage />);
    expect(await screen.findByText('还没有部门')).toBeInTheDocument();
  });

  it('toTreeData 递归转换', () => {
    expect(toTreeData([])).toEqual([]);
  });
});

describe('用户组', () => {
  it('新建、重命名、删除，在抽屉里管理成员', async () => {
    const calls = api();
    renderWithApp(<OrgPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '用户组' }));
    const row = (await screen.findByText('客服')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '成员' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('李雷')).toBeInTheDocument();
    await userEvent.click(
      within(within(drawer).getByRole('group', { name: '选择成员' })).getByRole('button'),
    );
    await userEvent.click(await screen.findByRole('option', { name: '韩梅梅（han@corp.cn）' }));
    await userEvent.click(within(drawer).getByRole('button', { name: '加入' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/groups/g1/members`)[0]?.body).toEqual({ user_id: 'u2' }),
    );
    await userEvent.click(within(drawer).getByRole('button', { name: '移出' }));
    const pop = await screen.findByRole('alertdialog', { name: '把这位成员移出用户组？' });
    await userEvent.click(within(pop).getByRole('button', { name: '移出' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/groups/g1/members/u1`)).toHaveLength(1));
    await userEvent.click(within(drawer).getByRole('button', { name: '关闭' }));

    await userEvent.click(screen.getByRole('button', { name: /新建用户组/ }));
    await nameDialog('售后', '新建');
    await waitFor(() => expect(calls.find('POST', `${B}/groups`)[0]?.body).toEqual({ name: '售后' }));
    await userEvent.click(within(row).getByRole('button', { name: '重命名' }));
    await nameDialog('客服中心', '保存');
    await waitFor(() => expect(calls.find('PATCH', `${B}/groups/g1`)[0]?.body).toEqual({ name: '客服中心' }));
    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    const confirm = await screen.findByRole('alertdialog', { name: /删除用户组「客服」/ });
    await userEvent.click(within(confirm).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(calls.find('DELETE', `${B}/groups/g1`)).toHaveLength(1));
  });

  it('名称为空时不提交', async () => {
    const calls = api();
    renderWithApp(<OrgPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '用户组' }));
    await userEvent.click(await screen.findByRole('button', { name: /新建用户组/ }));
    await nameDialog('  ', '新建');
    expect(await screen.findByText('请输入名称')).toBeInTheDocument();
    expect(calls.find('POST', `${B}/groups`)).toHaveLength(0);
  });
});

describe('组织架构', () => {
  const org = (id: string, name: string, parent: string | null, count: number) => ({
    id,
    source_id: 'ds_1',
    external_id: id,
    parent_id: parent,
    name,
    sort: 0,
    member_count: count,
  });

  it('接了企业目录：最后多一个只读的组织架构，选部门看这一级的人和在不在本空间', async () => {
    api({
      [`GET ${B}/org-departments`]: jsonResponse([org('o1', '集团', null, 1), org('o2', '法务部', 'o1', 2)]),
      [`GET ${B}/org-departments/o2/members`]: jsonResponse([
        { user_id: 'u1', is_primary: true, role: 'owner', name: '李雷' },
        { user_id: 'u9', is_primary: false, role: null, name: '王五' },
      ]),
    });
    renderWithApp(<OrgPage />);
    const orgTab = await screen.findByRole('tab', { name: '组织架构' });
    // 排在最后，默认仍停在部门
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['部门', '用户组', '组织架构']);
    expect(screen.getByRole('tab', { name: '部门', selected: true })).toBeInTheDocument();
    expect(screen.getByText(/组织架构从企业目录同步来/)).toBeInTheDocument();
    await userEvent.click(orgTab);
    const tree = screen.getByRole('tree', { name: '组织架构' });
    await userEvent.click(within(tree).getByRole('button', { name: '法务部（2）' }));
    const panel = await screen.findByRole('region', { name: '组织架构：集团 / 法务部' });
    expect(await within(panel).findByText('王五')).toBeInTheDocument();
    const wang = within(panel).getByText('王五').closest('tr') as HTMLElement;
    expect(within(wang).getByText('不在本空间')).toBeInTheDocument();
    const li = within(panel).getByText('李雷').closest('tr') as HTMLElement;
    expect(within(li).getByText('所有者')).toBeInTheDocument();
    expect(within(li).getByText('主部门')).toBeInTheDocument();
    // 只读：没有新建、移出这些操作
    expect(within(panel).queryByRole('button', { name: /更多|移出|加入/ })).toBeNull();
  });

  it('没接目录时和原来一样，只有部门与用户组', async () => {
    api();
    renderWithApp(<OrgPage />);
    expect(await screen.findByRole('tab', { name: '部门', selected: true })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '组织架构' })).toBeNull();
  });
});
