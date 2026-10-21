import { afterEach, describe, expect, it, vi } from 'vitest';
import * as ws from './workspace';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

const ok = () => new Response(null, { status: 204 });
const B = '/api/workspaces/default';

describe('成员', () => {
  it('解析成员并补全显示名', async () => {
    routeFetch({
      [`GET ${B}/members`]: jsonResponse([
        { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
        { user_id: 'u2', role: 'viewer' },
        { user_id: 'u3', role: 'god' },
      ]),
    });
    const members = await ws.listMembers();
    expect(members).toEqual([
      { userId: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
      { userId: 'u2', role: 'viewer', name: null, email: null },
    ]);
    expect(members.map(ws.memberLabel)).toEqual(['李雷', 'u2']);
    expect(ws.memberLabel({ userId: 'u', name: null, email: 'e@x.cn' })).toBe('e@x.cn');
    expect(ws.roleLabel('admin')).toBe('管理员');
    expect(ws.roleLabel('x')).toBe('x');
  });

  it('加人、改角色、移出', async () => {
    const api = routeFetch({
      [`POST ${B}/members`]: jsonResponse({}, 201),
      [`PATCH ${B}/members/*`]: jsonResponse({}),
      [`DELETE ${B}/members/*`]: ok(),
    });
    await ws.addMember('u2', 'editor');
    await ws.setMemberRole('u2', 'admin');
    await ws.removeMember('u2');
    expect(api.calls.map((c) => [c.method, c.path, c.body])).toEqual([
      ['POST', `${B}/members`, { user_id: 'u2', role: 'editor' }],
      ['PATCH', `${B}/members/u2`, { role: 'admin' }],
      ['DELETE', `${B}/members/u2`, undefined],
    ]);
  });
});

describe('部门与用户组', () => {
  const tree = [
    {
      id: 'd1',
      name: '总部',
      parent_id: null,
      members: [['u1', true]],
      children: [{ id: 'd2', name: '法务', parent_id: 'd1', members: [], children: [] }],
    },
    { id: '', name: '坏数据' },
  ];

  it('解析部门树、查找与拍平', async () => {
    routeFetch({ [`GET ${B}/departments`]: jsonResponse(tree) });
    const departments = await ws.fetchDepartments();
    expect(departments).toHaveLength(1);
    expect(departments[0]?.members).toEqual([{ userId: 'u1', isPrimary: true }]);
    expect(ws.findDepartment(departments, 'd2')?.name).toBe('法务');
    expect(ws.findDepartment(departments, 'nope')).toBeUndefined();
    expect(ws.flattenDepartments(departments).map((d) => d.id)).toEqual(['d1', 'd2']);
  });

  it('部门与用户组的增删改按约定调用', async () => {
    const api = routeFetch({
      [`POST ${B}/departments`]: jsonResponse({ id: 'd3' }, 201),
      [`PATCH ${B}/departments/*`]: ok(),
      [`DELETE ${B}/departments/*`]: ok(),
      [`POST ${B}/departments/*/members`]: ok(),
      [`DELETE ${B}/departments/*/members/*`]: ok(),
      [`GET ${B}/groups`]: jsonResponse([{ id: 'g1', name: '客服', members: ['u1', 3] }, { name: 'x' }]),
      [`POST ${B}/groups`]: jsonResponse({ id: 'g2' }, 201),
      [`PATCH ${B}/groups/*`]: ok(),
      [`DELETE ${B}/groups/*`]: ok(),
      [`POST ${B}/groups/*/members`]: ok(),
      [`DELETE ${B}/groups/*/members/*`]: ok(),
    });
    await ws.createDepartment(' 研发 ', 'd1');
    await ws.renameDepartment('d3', ' 研发部 ');
    await ws.moveDepartment('d3', null);
    await ws.addDepartmentMember('d3', 'u1', true);
    await ws.removeDepartmentMember('d3', 'u1');
    await ws.deleteDepartment('d3');
    expect(await ws.listGroups()).toEqual([{ id: 'g1', name: '客服', members: ['u1'] }]);
    await ws.createGroup(' 售后 ');
    await ws.renameGroup('g2', '售后组');
    await ws.addGroupMember('g2', 'u1');
    await ws.removeGroupMember('g2', 'u1');
    await ws.deleteGroup('g2');
    const bodies = api.calls.map((c) => c.body);
    expect(bodies[0]).toEqual({ name: '研发', parent_id: 'd1' });
    expect(bodies[1]).toEqual({ name: '研发部' });
    expect(bodies[2]).toEqual({ move: true, parent_id: null });
    expect(bodies[3]).toEqual({ user_id: 'u1', is_primary: true });
    expect(api.calls.at(-1)).toMatchObject({ method: 'DELETE', path: `${B}/groups/g2` });
  });
});

describe('授权与判定', () => {
  it('列出、授权、撤销', async () => {
    const api = routeFetch({
      [`GET ${B}/grants`]: jsonResponse([
        { subject: 'user:u1', resource: 'skill:*', action: 'use' },
        { subject: 'x' },
      ]),
      [`POST ${B}/grants`]: jsonResponse({}, 201),
      [`DELETE ${B}/grants`]: ok(),
    });
    expect(await ws.listGrants()).toEqual([{ subject: 'user:u1', resource: 'skill:*', action: 'use' }]);
    await ws.grant('group:g1', 'knowledge_source:s1', 'edit');
    await ws.revokeGrant('group:g1', 'knowledge_source:s1');
    expect(api.calls[1]?.body).toEqual({
      subject: 'group:g1',
      resource: 'knowledge_source:s1',
      action: 'edit',
    });
    expect(api.calls[2]?.query.get('subject')).toBe('group:g1');
    expect(ws.splitKey('role:admin')).toEqual({ kind: 'role', id: 'admin' });
    expect(ws.splitKey('x')).toEqual({ kind: 'x', id: '' });
  });

  it('管理员判定：查 workspace 的 edit，出错一律为否', async () => {
    const api = routeFetch({ [`GET ${B}/me/check`]: jsonResponse({ allowed: true }) });
    await expect(ws.checkWorkspaceAdmin()).resolves.toBe(true);
    expect(api.calls[0]?.query.toString()).toBe('resource=workspace&action=edit');
    routeFetch({ [`GET ${B}/me/check`]: jsonResponse({}, 503) });
    await expect(ws.checkWorkspaceAdmin()).resolves.toBe(false);
    routeFetch({ [`GET ${B}/me/check`]: jsonResponse({ allowed: 'yes' }) });
    await expect(ws.checkWorkspaceAdmin()).resolves.toBe(false);
  });
});
