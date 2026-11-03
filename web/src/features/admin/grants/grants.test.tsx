import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption, pickTreeNode } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { RESOURCE_TYPES } from '../../../api/workspace';
import { register } from '../../../slots/registry';
import GrantsPage from './GrantsPage';
import { resourceKeys, validateGrant, subjectNodes } from './form';
import { resourceLabel, subjectLabel, type Directory } from './labels';
import type { ReactElement } from 'react';
import { BookClosed, BookOpen, Folder, Folders } from '@openai/apps-sdk-ui/components/Icon';
import { knowledgeNodes } from './KnowledgeBasePicker';
import { pathOf, type TreeSelectNode } from '../../../ui/TreeSelect';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const BATCH = `${B}/grants/batch`;

/** 授权对象是树形多选：点开一次、点上要的几个，按 Esc 收起浮层（多选时点了不自动收）。 */
async function pickSubjects(dialog: HTMLElement, ...names: string[]) {
  await userEvent.click(within(within(dialog).getByRole('group', { name: '授权对象' })).getByRole('button'));
  const tree = await screen.findByRole('tree', { name: '授权对象' });
  for (const name of names) await userEvent.click(within(tree).getByRole('button', { name }));
  await userEvent.keyboard('{Escape}');
}

function api(extra = {}) {
  return routeFetch({
    [`GET ${B}/grants`]: jsonResponse([
      { subject: 'user:u1', resource: 'knowledge_base:s1.*', action: 'use' },
      { subject: 'user:u1', resource: 'knowledge_base:s1.gone', action: 'use' },
      { subject: 'user:u2', resource: 'knowledge_base:s1.d1', action: 'use' },
      { subject: 'group:g1', resource: 'skill:*', action: 'edit' },
      { subject: 'user:u2', resource: 'skill:sk_7f3a', action: 'use' },
      { subject: 'role:editor', resource: 'tool:*', action: 'owner' },
    ]),
    [`GET ${B}/members`]: jsonResponse([
      { user_id: 'u1', role: 'editor', name: '李雷', email: 'li@corp.cn' },
      { user_id: 'u2', role: 'editor', name: '韩梅梅', email: 'han@corp.cn' },
    ]),
    [`GET ${B}/groups`]: jsonResponse([{ id: 'g1', name: '客服', members: [] }]),
    [`GET ${B}/departments`]: jsonResponse([
      {
        id: 'd1',
        name: '总部',
        parent_id: null,
        members: [],
        children: [{ id: 'd2', name: '法务部', parent_id: 'd1', members: [], children: [] }],
      },
    ]),
    [`GET ${B}/knowledge-bases`]: jsonResponse([
      {
        source_id: 's1',
        source_name: '制度库',
        auth_mode: 'shared',
        enabled: true,
        error: null,
        bases: [{ id: 's1.d1', kb_id: 'd1', name: '员工手册' }],
      },
    ]),
    [`GET ${B}/skills`]: jsonResponse({
      skills: [
        { id: 'sk_7f3a', name: '周报', status: 'published', enabled: true },
        { id: 'sk_9c1e', name: '合同审查', status: 'draft', enabled: true },
      ],
    }),
    [`GET ${B}/mcp-servers`]: jsonResponse([
      {
        id: 'ms1',
        name: 'deepwiki',
        title: 'DeepWiki',
        grantPattern: 'mcp__deepwiki__*',
        tools: [
          { name: 'mcp__deepwiki__ask_wiki_question', rawName: 'ask_wiki_question' },
          { name: 'mcp__deepwiki__read_wiki_contents', rawName: 'read_wiki_contents' },
        ],
      },
    ]),
    [`POST ${B}/grants/batch`]: jsonResponse({ grants: [] }, 201),
    [`DELETE ${B}/grants`]: new Response(null, { status: 204 }),
    ...extra,
  });
}

describe('GrantsPage', () => {
  it('把授权翻成名称显示，可搜索与撤销', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    expect(await screen.findByText('制度库 · 全部知识库')).toBeInTheDocument();
    expect(screen.getByText('制度库 · 员工手册')).toBeInTheDocument();
    expect(screen.getByText('制度库 · 已失效的知识库')).toHaveAttribute(
      'title',
      '这条授权已不起作用，可以撤销（knowledge_base:s1.gone）',
    );
    expect(screen.getByText('已删除或共享已撤销')).toBeInTheDocument();
    expect(await screen.findAllByText('李雷')).not.toHaveLength(0);
    expect(screen.getByText('全部技能')).toBeInTheDocument();
    // 授给角色「成员」的那条（「成员」两个字页面上别处也有，取任意一处）
    expect(screen.getAllByText('成员').length).toBeGreaterThan(0);
    expect(screen.getByText('全部工具')).toBeInTheDocument();
    expect(screen.getByText('完全管理')).toBeInTheDocument();
    const row = screen.getByText('制度库 · 全部知识库').closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '撤销' }));
    const pop = await screen.findByRole('alertdialog', { name: /撤销这条授权/ });
    await userEvent.click(within(pop).getByRole('button', { name: '撤销' }));
    await waitFor(() =>
      expect(calls.find('DELETE', `${B}/grants`)[0]?.query.toString()).toBe(
        'subject=user%3Au1&resource=knowledge_base%3As1.*',
      ),
    );
    fireEvent.change(screen.getByPlaceholderText('搜索对象、资源或权限'), { target: { value: '客服' } });
    expect(screen.queryByText('制度库 · 全部知识库')).not.toBeInTheDocument();
  });

  it('新增授权：知识库是按来源分组的树，可选单个知识库', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    expect(await within(dialog).findByText('请选择授权对象')).toBeInTheDocument();
    await pickSubjects(dialog, '李雷');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    expect(await within(dialog).findByText('请选择知识库')).toBeInTheDocument();
    await pickTreeNode(dialog, '知识库', '员工手册');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['user:u1'],
        resources: ['knowledge_base:s1.d1'],
        action: 'use',
      }),
    );
  });

  it('同一类对象可以选几个；部门按层级成树', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷', '韩梅梅');
    expect(within(dialog).getByRole('button', { name: '李雷、韩梅梅' })).toBeInTheDocument();
    // 换了对象类型，已选的清掉
    await pickOption(dialog, '对象类型', '部门');
    await userEvent.click(
      within(within(dialog).getByRole('group', { name: '授权对象' })).getByRole('button'),
    );
    const tree = await screen.findByRole('tree', { name: '授权对象' });
    const parent = within(tree).getByRole('button', { name: '总部' }).closest('li') as HTMLElement;
    expect(within(parent).getByRole('button', { name: '法务部' })).toBeInTheDocument();
    await userEvent.click(within(tree).getByRole('button', { name: '法务部' }));
    await userEvent.keyboard('{Escape}');
    await pickTreeNode(dialog, '知识库', '员工手册');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['dept:d2'],
        resources: ['knowledge_base:s1.d1'],
        action: 'use',
      }),
    );
  });

  it('同一类资源可以选几个：几个对象 × 几个资源一次授上', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    expect(await screen.findByText('周报')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷', '韩梅梅');
    await pickOption(dialog, '资源类型', '技能');
    await pickOption(dialog, '技能', '周报');
    await userEvent.click(await screen.findByRole('option', { name: '合同审查' }));
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['user:u1', 'user:u2'],
        resources: ['skill:sk_7f3a', 'skill:sk_9c1e'],
        action: 'use',
      }),
    );
  });

  it('技能按名称显示与选择，不用填技能 id', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    expect(await screen.findByText('周报')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷');
    await pickOption(dialog, '资源类型', '技能');
    expect(within(dialog).queryByLabelText('资源 ID')).toBeNull();
    await pickOption(dialog, '技能', '合同审查');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['user:u1'],
        resources: ['skill:sk_9c1e'],
        action: 'use',
      }),
    );
  });

  it('工具和知识库一样按树选：MCP 服务下面是「全部工具」和各个工具', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷');
    await pickOption(dialog, '资源类型', '工具');
    await pickTreeNode(dialog, '工具', 'ask_wiki_question');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toMatchObject({
        resources: ['tool:mcp__deepwiki__ask_wiki_question'],
      }),
    );
  });

  it('工具：选 MCP 服务下的「全部工具」就是这个服务的全部（含以后新增的）', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷');
    await pickOption(dialog, '资源类型', '工具');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    expect(await within(dialog).findByText('请选择工具')).toBeInTheDocument();
    await pickTreeNode(dialog, '工具', '全部工具');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toMatchObject({ resources: ['tool:mcp__deepwiki__*'] }),
    );
  });

  it('新增授权：选「全部知识库」就是这个来源下的全部', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷');
    await pickTreeNode(dialog, '知识库', '全部知识库');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toMatchObject({ resources: ['knowledge_base:s1.*'] }),
    );
    expect(within(dialog).queryByRole('option', { name: '知识源' })).toBeNull();
  });

  it('授权给全部资源', async () => {
    const calls = api();
    renderWithApp(<GrantsPage />);
    await screen.findByText('制度库 · 全部知识库');
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickOption(dialog, '对象类型', '用户组');
    await pickSubjects(dialog, '客服');
    await pickTreeNode(dialog, '知识库', '员工手册');
    // 写明是全部什么；勾上以后不再显示具体选哪个，之前选的也不算
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: '全部知识库（所有知识源，含以后新增的）' }),
    );
    expect(within(dialog).queryByRole('group', { name: '知识库' })).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['group:g1'],
        resources: ['knowledge_base:*'],
        action: 'use',
      }),
    );
  });
});

describe('扩展给的知识源层级树', () => {
  it('按树选目录或知识库，已有授权显示成完整路径', async () => {
    register('grants.knowledge', {
      sourceId: 's1',
      types: { knowledge_folder: '知识库目录' },
      load: async () => [
        {
          resource: 'knowledge_folder:f1',
          name: '产品资料',
          children: [
            {
              resource: 'knowledge_folder:f2',
              name: '说明书',
              children: [{ resource: 'knowledge_base:s1.d1', name: '员工手册', children: [] }],
            },
          ],
        },
      ],
    });
    const calls = api({
      [`GET ${B}/grants`]: jsonResponse([
        { subject: 'user:u1', resource: 'knowledge_folder:f2', action: 'use' },
        { subject: 'user:u2', resource: 'knowledge_base:s1.d1', action: 'use' },
      ]),
    });
    renderWithApp(<GrantsPage />);
    expect(await screen.findByText('制度库 · 产品资料 / 说明书')).toBeInTheDocument();
    expect(screen.getByText('知识库目录')).toBeInTheDocument();
    expect(screen.getByText('制度库 · 产品资料 / 说明书 / 员工手册')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /新增授权/ }));
    const dialog = await screen.findByRole('dialog');
    await pickSubjects(dialog, '李雷');
    await pickTreeNode(dialog, '知识库', '产品资料');
    expect(
      within(within(dialog).getByRole('group', { name: '知识库' })).getByRole('button'),
    ).toHaveTextContent('产品资料');
    await userEvent.click(within(dialog).getByRole('button', { name: '授权' }));
    await waitFor(() =>
      expect(calls.find('POST', BATCH)[0]?.body).toEqual({
        subjects: ['user:u1'],
        resources: ['knowledge_folder:f1'],
        action: 'use',
      }),
    );
  });
});

describe('授权的纯函数', () => {
  const dir: Directory = {
    members: [],
    groups: [],
    departments: [{ id: 'd1', name: '总部', parentId: null, members: [], children: [] }],
    orgDepartments: [],
    orgLoaded: false,
    resources: {},
    knowledge: [],
    knowledgeLoaded: false,
    tools: [],
    typeLabels: Object.fromEntries(RESOURCE_TYPES.map((t) => [t.value, t.label])),
    trees: {},
  };

  it('组织架构：用完整路径区分同名部门，目录里删掉的标出来', () => {
    const org = (id: string, name: string, parentId: string | null) => ({
      id,
      sourceId: 'ad',
      parentId,
      name,
      sort: 0,
      memberCount: 0,
    });
    const withOrg: Directory = {
      ...dir,
      orgDepartments: [
        org('o1', '集团', null),
        org('o2', '总部', 'o1'),
        org('o3', '研发中心', 'o1'),
        org('o4', '财务部', 'o2'),
        org('o5', '财务部', 'o3'),
      ],
      orgLoaded: true,
    };
    expect(subjectLabel('org:o4', withOrg)).toEqual({ kind: '组织架构', name: '集团 / 总部 / 财务部' });
    expect(subjectLabel('org:gone', withOrg).name).toBe('已从目录移除的部门');
    expect(subjectLabel('org:gone', dir).name, '还没取到时不下结论').toBe('gone');
    expect(pathOf(subjectNodes('org', withOrg), 'org:o5')).toBe('集团 / 研发中心 / 财务部');
  });

  it('名称查不到时显示 id', () => {
    expect(subjectLabel('user:x', dir)).toEqual({ kind: '成员', name: 'x' });
    expect(subjectLabel('dept:d1', dir).name).toBe('总部');
    expect(subjectLabel('group:g', dir).name).toBe('g');
    expect(subjectLabel('alien:a', dir)).toEqual({ kind: 'alien', name: 'a' });
    expect(resourceLabel('tool:t1', dir)).toEqual({ type: '工具', name: 't1' });
    expect(resourceLabel('knowledge_source:s9', dir), '旧授权仍能显示').toEqual({
      type: '知识源',
      name: 's9',
    });
    expect(resourceLabel('knowledge_base:*', dir).name).toBe('全部知识库');
    expect(resourceLabel('knowledge_base:s1.gone', dir), '知识库列表还没取到，不下结论').toEqual({
      type: '知识库',
      name: 's1.gone',
    });
  });

  it('知识库删了或共享撤销了：显示已失效，知识源取不到时照原样显示编号', () => {
    const source = (error: string | null) => ({
      sourceId: 's1',
      sourceName: '制度库',
      authMode: 'builtin',
      enabled: true,
      error,
      bases: [],
    });
    const loaded = { ...dir, knowledgeLoaded: true, knowledge: [source(null)] } as Directory;
    expect(resourceLabel('knowledge_base:s1.gone', loaded)).toEqual({
      type: '知识库',
      name: '制度库 · 已失效的知识库',
      stale: true,
    });
    const broken = { ...loaded, knowledge: [source('连不上')] } as Directory;
    expect(resourceLabel('knowledge_base:s1.gone', broken).name).toBe('s1.gone');
  });

  it('校验与选项', () => {
    const base = {
      subjectKind: 'user' as const,
      subjects: [] as string[],
      resourceType: 'skill',
      resourceIds: ['bad id'],
      all: false,
      action: 'use' as const,
    };
    expect(validateGrant(base)).toBe('请选择授权对象');
    expect(validateGrant({ ...base, subjects: ['user:u'] })).toMatch(/资源 ID/);
    expect(validateGrant({ ...base, subjects: ['user:u'], all: true })).toBeNull();
    const kb = { ...base, subjects: ['user:u'], resourceType: 'knowledge_base' };
    expect(validateGrant({ ...kb, resourceIds: [] })).toBe('请选择知识库');
    // 知识库那一栏是树形选择，值就是完整资源（知识库或目录）
    expect(validateGrant({ ...kb, resourceIds: ['knowledge_base:s1.*'] })).toBeNull();
    expect(validateGrant({ ...kb, resourceIds: ['knowledge_folder:f1'] })).toBeNull();
    expect(validateGrant({ ...kb, resourceIds: ['knowledge_folder:bad id'] })).toMatch(/资源 ID/);
    expect(resourceKeys({ ...kb, resourceIds: ['knowledge_folder:f1'] })).toEqual(['knowledge_folder:f1']);
    // 工具也是树形选择，值就是完整资源（单个工具或一个 MCP 服务的全部工具）
    const tool = { ...base, subjects: ['user:u'], resourceType: 'tool' };
    expect(validateGrant({ ...tool, resourceIds: [] })).toBe('请选择工具');
    expect(validateGrant({ ...tool, resourceIds: ['tool:mcp__a__*'] })).toBeNull();
    expect(resourceKeys({ ...tool, resourceIds: ['tool:mcp__a__*'] })).toEqual(['tool:mcp__a__*']);
    expect(resourceKeys({ ...tool, resourceIds: ['tool:mcp__a__x'], all: true })).toEqual(['tool:*']);
    expect(resourceKeys({ ...kb, resourceIds: ['knowledge_folder:f1'], all: true })).toEqual([
      'knowledge_base:*',
    ]);
    // 同一类资源可以选几个：有一个 id 不对就不让提交
    const skills = { ...base, subjects: ['user:u'], resourceIds: ['sk_1', 'sk_2'] };
    expect(resourceKeys(skills)).toEqual(['skill:sk_1', 'skill:sk_2']);
    expect(validateGrant(skills)).toBeNull();
    expect(validateGrant({ ...skills, resourceIds: ['sk_1', 'bad id'] })).toMatch(/资源 ID/);
    // 部门按层级成树，其余平铺；键是完整的授权对象
    const nested = {
      ...dir,
      departments: [{ ...dir.departments[0]!, children: [{ ...dir.departments[0]!, id: 'd2' }] }],
    };
    expect(subjectNodes('dept', nested).map((n) => [n.key, n.children.map((c) => c.key)])).toEqual([
      ['dept:d1', ['dept:d2']],
    ]);
    expect(subjectNodes('role', dir).map((n) => n.key)).toEqual(['role:owner', 'role:admin', 'role:editor']);
    expect(subjectNodes('group', { ...dir, groups: [{ id: 'g1', name: '客服', members: [] }] })).toEqual([
      { key: 'group:g1', label: '客服', children: [] },
    ]);
  });
});

describe('授权页知识库树的图标', () => {
  it('和知识库首页、@ 一致：知识源是打开的书，「全部」是多个文件夹，目录是文件夹，知识库是书', () => {
    const [source] = knowledgeNodes(
      [
        {
          sourceId: 'builtin',
          sourceName: '扩展知识库',
          authMode: 'shared',
          enabled: true,
          bases: [],
          error: null,
        },
      ],
      {
        builtin: [
          {
            resource: 'knowledge_folder:f1',
            name: '财务',
            children: [{ resource: 'knowledge_base:builtin.kb1', name: '报销', children: [] }],
          },
        ],
      },
    );
    const iconName = (node?: TreeSelectNode) => (node?.icon as ReactElement | undefined)?.type;
    const [all, folder] = source?.children ?? [];
    expect([iconName(source), iconName(all), iconName(folder), iconName(folder?.children[0])]).toEqual([
      BookOpen,
      Folders,
      Folder,
      BookClosed,
    ]);
  });
});
