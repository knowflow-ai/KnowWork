import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { MyPluginsTab } from '../../settings/MyPluginsTab';
import PluginsPage from './PluginsPage';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default';
const GOAL = {
  key: 'goal',
  name: '长任务目标',
  description: '把一件要做很久的事记成目标',
  source: 'builtin',
  detail: '适合要做很久的事。\n\n![示意图](docs/flow.png)\n\n**怎么用**',
  components: [
    {
      module: '@deepseek-ai/dsh-tool-goal',
      title: '目标管理工具',
      description: '让 AI 设置、查看和更新当前会话目标。',
    },
  ],
};
const SETTINGS = { allow_personal: true, allow_personal_device: true, blocked: [] };
const NO_UPLOADS = { plugins: [], member_plugins: [], settings: SETTINGS, cloud_available: true };
const SHARED = {
  id: 'pi_' + 'a'.repeat(32),
  name: 'expense-check',
  version: '1.2.0',
  description: '报销预审',
  scope: 'workspace',
  owner: '',
  mode: 'off',
  targets: ['cloud', 'device'],
  capabilities: ['network'],
  status: 'ready',
  reason: '',
  narrowed: false,
  subjects: [],
  blocked: false,
  submitted: false,
};
const PERSONAL = {
  ...SHARED,
  id: 'pi_' + 'b'.repeat(32),
  name: 'my-weekly',
  version: '0.1.0',
  scope: 'personal',
  owner: 'u-alice',
  mode: 'optional',
  targets: ['cloud'],
  capabilities: ['fs', 'process'],
};
/** 管理页另外会拉的：成员名单（成员安装的那一页显示谁装的）、「给谁用」的选项 */
const ADMIN_EXTRA = {
  [`GET ${B}/members`]: jsonResponse({ members: [] }),
  [`GET ${B}/departments`]: jsonResponse({ departments: [] }),
  [`GET ${B}/groups`]: jsonResponse({ groups: [] }),
  [`GET ${B}/org/departments`]: jsonResponse({ departments: [] }),
};
const archive = () =>
  new File([new Uint8Array([31, 139, 8, 0])], 'expense-check-1.2.0.tgz', { type: 'application/gzip' });

describe('插件详情（照官方插件页：说明文档、组件清单、信息）', () => {
  it('预置：说明文档和组件随版本带着，图片只留说明文字', async () => {
    routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [{ ...GOAL, mode: 'on' }] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse(NO_UPLOADS),
    });
    renderWithApp(<PluginsPage />);
    const row = await screen.findByRole('group', { name: '长任务目标' });
    await userEvent.click(within(row).getByRole('button', { name: '详情' }));
    const sheet = await screen.findByRole('dialog', { name: '长任务目标' });
    expect(within(sheet).getByText('适合要做很久的事。')).toBeInTheDocument();
    expect(within(sheet).getByText('示意图')).toBeInTheDocument();
    expect(within(sheet).queryByRole('img')).not.toBeInTheDocument();
    expect(within(sheet).getByText('目标管理工具')).toBeInTheDocument();
    expect(within(sheet).getByText('@deepseek-ai/dsh-tool-goal')).toBeInTheDocument();
    expect(within(sheet).getByText('全员启用')).toBeInTheDocument();
  });

  it('上传的：标题用作者写的，标题下写包名和版本，说明文档打开时再取', async () => {
    const calls = routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [
          {
            id: PERSONAL.id,
            name: 'my-weekly',
            version: '0.1.0',
            scope: 'personal',
            targets: ['cloud'],
            capabilities: ['fs'],
            enabled: true,
            title: '周报智能体',
            description: '把零散记录整理成周报',
            components: [
              { id: 'main', module: 'my-weekly', title: '周报智能体', description: '把零散记录整理成周报' },
            ],
          },
        ],
        can_install: true,
        can_install_device: true,
        cloud_available: true,
      }),
      [`GET ${B}/me/plugins/uploads/*/detail`]: jsonResponse({ detail: '# 用法\n\n写一句话就行' }),
    });
    renderWithApp(<MyPluginsTab />);
    // 带开关的设置行没有 group 角色（标题是开关的 label），按标题找到这一行
    const row = (await screen.findByText('周报智能体 0.1.0')).closest<HTMLElement>('.kw-setting-row')!;
    expect(within(row).getByText(/把零散记录整理成周报/)).toBeInTheDocument();
    expect(calls.find('GET', `${B}/me/plugins/uploads/${PERSONAL.id}/detail`)).toHaveLength(0);
    await userEvent.click(within(row).getByRole('button', { name: '详情' }));
    const sheet = await screen.findByRole('dialog', { name: '周报智能体' });
    expect(within(sheet).getByText('my-weekly 0.1.0')).toBeInTheDocument();
    expect(await within(sheet).findByRole('heading', { name: '用法' })).toBeInTheDocument();
    expect(within(sheet).getByText('个人插件（你自己装的，只有你能用）')).toBeInTheDocument();
  });
});

describe('管理后台的插件页', () => {
  it('预置：列出插件和各自的档，点一下就改', async () => {
    const calls = routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [{ ...GOAL, mode: 'off' }] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse(NO_UPLOADS),
      [`PUT ${B}/plugins/goal`]: jsonResponse({ ...GOAL, mode: 'optional' }),
    });
    renderWithApp(<PluginsPage />);
    const row = await screen.findByRole('group', { name: '长任务目标' });
    expect(within(row).getByText('把一件要做很久的事记成目标')).toBeInTheDocument();
    expect(within(row).getByRole('radio', { name: '关闭' })).toBeChecked();
    await userEvent.click(within(row).getByRole('radio', { name: '可选' }));
    await waitFor(() => expect(calls.find('PUT', `${B}/plugins/goal`)).toHaveLength(1));
    expect(calls.find('PUT', `${B}/plugins/goal`)[0]?.body).toEqual({ mode: 'optional' });
    expect(await screen.findByText('已保存，新开的对话生效')).toBeInTheDocument();
    await waitFor(() => expect(within(row).getByRole('radio', { name: '可选' })).toBeChecked());
    // 可选的写明成员（包括管理员自己）去哪儿打开
    expect(within(row).getByRole('link', { name: '个人设置的「我的插件」' })).toHaveAttribute(
      'href',
      '/settings?tab=plugins',
    );
  });

  it('没有权限、没有预置插件时各有说明', async () => {
    routeFetch({
      [`GET ${B}/plugins`]: jsonResponse({ detail: '只有空间所有者和管理员能管理插件' }, 403),
      [`GET ${B}/plugins/uploads`]: jsonResponse({ detail: '只有空间所有者和管理员能管理插件' }, 403),
    });
    const first = renderWithApp(<PluginsPage />);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();
    routeFetch({
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse(NO_UPLOADS),
    });
    renderWithApp(<PluginsPage />);
    expect(await screen.findByText('这一版还没有预置的插件。')).toBeInTheDocument();
  });

  it('空间插件：上传时带上适用于，列表里看得到用到的能力，能删', async () => {
    const calls = routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse({ ...NO_UPLOADS, plugins: [SHARED] }),
      [`POST ${B}/plugins/uploads`]: jsonResponse(SHARED, 201),
      [`DELETE ${B}/plugins/uploads/*`]: new Response(null, { status: 204 }),
    });
    renderWithApp(<PluginsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '空间插件' }));
    const row = (await screen.findByText('expense-check')).closest('tr')!;
    expect(within(row).getByText('联网')).toBeInTheDocument();
    expect(within(row).getByText('云端与本机对话')).toBeInTheDocument();
    expect(within(row).getByText('全体成员')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '上传插件' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/没有容器隔离/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '上传' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('请先选一个 .tgz 压缩包');
    fireEvent.change(within(dialog).getByLabelText('选择插件压缩包'), { target: { files: [archive()] } });
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /本机对话/ }));
    expect(within(dialog).queryByText(/没有容器隔离/)).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '上传' }));
    await waitFor(() => expect(calls.find('POST', `${B}/plugins/uploads`)).toHaveLength(1));
    expect(calls.find('POST', `${B}/plugins/uploads`)[0]?.query.get('targets')).toBe('cloud');

    await userEvent.click(within(row).getByRole('button', { name: '删除' }));
    await userEvent.click(screen.getAllByRole('button', { name: '删除' }).at(-1)!);
    await waitFor(() => expect(calls.find('DELETE', `${B}/plugins/uploads/${SHARED.id}`)).toHaveLength(1));
  });

  it('上传被拒时把原因留在对话框里', async () => {
    routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse(NO_UPLOADS),
      [`POST ${B}/plugins/uploads`]: jsonResponse(
        { detail: '这不是 DSH 的插件包：package.json 里没有 dsh.bundle.patch' },
        422,
      ),
    });
    renderWithApp(<PluginsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '空间插件' }));
    await userEvent.click(await screen.findByRole('button', { name: '上传插件' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('选择插件压缩包'), { target: { files: [archive()] } });
    await userEvent.click(within(dialog).getByRole('button', { name: '上传' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('这不是 DSH 的插件包');
  });

  it('成员安装的：看得到谁装了什么，能关掉个人安装、按包名禁用', async () => {
    const calls = routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/members`]: jsonResponse({ members: [{ user_id: 'u-alice', name: '王芳', role: 'editor' }] }),
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/plugins/uploads`]: jsonResponse({ ...NO_UPLOADS, member_plugins: [PERSONAL] }),
      [`PUT ${B}/plugins/settings`]: jsonResponse(SETTINGS),
      [`POST ${B}/plugins/uploads/*/adopt`]: jsonResponse(SHARED, 201),
    });
    renderWithApp(<PluginsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '成员安装的（1）' }));
    const row = (await screen.findByText('my-weekly')).closest('tr')!;
    expect(within(row).getByText('读写文件、执行命令')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: '允许成员安装个人插件' }));
    await waitFor(() => expect(calls.find('PUT', `${B}/plugins/settings`)).toHaveLength(1));
    expect(calls.find('PUT', `${B}/plugins/settings`)[0]?.body).toEqual({
      allow_personal: false,
      allow_personal_device: true,
      blocked: [],
    });
    // 成员提交上来的：转成空间插件
    await userEvent.click(within(row).getByRole('button', { name: '转成空间插件' }));
    await userEvent.click(screen.getAllByRole('button', { name: '转成空间插件' }).at(-1)!);
    await waitFor(() =>
      expect(calls.find('POST', `${B}/plugins/uploads/${PERSONAL.id}/adopt`)).toHaveLength(1),
    );
    await userEvent.click(within(row).getByRole('button', { name: '禁用' }));
    await userEvent.click(screen.getAllByRole('button', { name: '禁用' }).at(-1)!);
    await waitFor(() => expect(calls.find('PUT', `${B}/plugins/settings`)).toHaveLength(2));
    expect(calls.find('PUT', `${B}/plugins/settings`)[1]?.body).toMatchObject({ blocked: ['my-weekly'] });
  });
});

describe('个人设置里的我的插件', () => {
  const MINE = {
    id: PERSONAL.id,
    name: 'my-weekly',
    version: '0.1.0',
    description: '',
    scope: 'personal',
    targets: ['cloud'],
    capabilities: ['fs'],
    status: 'ready',
    reason: '',
    enabled: true,
    locked: false,
    blocked: false,
  };

  it('全员启用的不能关，可选的自己开关', async () => {
    const calls = routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({
        plugins: [
          { ...GOAL, enabled: true, locked: true },
          {
            key: 'plan',
            name: '规划模式',
            description: '先出计划',
            source: 'builtin',
            enabled: false,
            locked: false,
          },
        ],
      }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [],
        can_install: true,
        can_install_device: true,
      }),
      [`PUT ${B}/me/plugins/plan`]: jsonResponse({ key: 'plan', enabled: true, locked: false }),
    });
    renderWithApp(<MyPluginsTab />);
    const locked = await screen.findByRole('switch', { name: '长任务目标' });
    expect(locked).toBeChecked();
    expect(locked).toBeDisabled();
    expect(screen.getByText(/管理员已为全员启用/)).toBeInTheDocument();
    const optional = screen.getByRole('switch', { name: '规划模式' });
    expect(optional).not.toBeChecked();
    await userEvent.click(optional);
    await waitFor(() => expect(calls.find('PUT', `${B}/me/plugins/plan`)).toHaveLength(1));
    expect(calls.find('PUT', `${B}/me/plugins/plan`)[0]?.body).toEqual({ enabled: true });
  });

  it('自己装的：能开关、能删、能再传；空间的默认启用不能关', async () => {
    const calls = routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [
          MINE,
          {
            ...MINE,
            id: SHARED.id,
            name: 'expense-check',
            version: '1.2.0',
            scope: 'workspace',
            locked: true,
          },
        ],
        can_install: true,
        can_install_device: false,
        cloud_available: true,
      }),
      [`PUT ${B}/me/plugins/uploads/*`]: jsonResponse({ ...MINE, enabled: false }),
      [`POST ${B}/me/plugins/uploads`]: jsonResponse(MINE, 201),
      [`POST ${B}/me/plugins/uploads/*/submit`]: jsonResponse({ ...MINE, submitted: true }),
    });
    renderWithApp(<MyPluginsTab />);
    expect(await screen.findByRole('switch', { name: 'expense-check 1.2.0' })).toBeDisabled();
    await userEvent.click(screen.getByRole('switch', { name: 'my-weekly 0.1.0' }));
    await waitFor(() => expect(calls.find('PUT', `${B}/me/plugins/uploads/${MINE.id}`)).toHaveLength(1));
    expect(calls.find('PUT', `${B}/me/plugins/uploads/${MINE.id}`)[0]?.body).toEqual({ enabled: false });

    await userEvent.click(screen.getByRole('button', { name: '提交给管理员' }));
    await waitFor(() =>
      expect(calls.find('POST', `${B}/me/plugins/uploads/${MINE.id}/submit`)).toHaveLength(1),
    );

    await userEvent.click(screen.getByRole('button', { name: '上传插件' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('checkbox', { name: /本机对话/ })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('选择插件压缩包'), { target: { files: [archive()] } });
    await userEvent.click(within(dialog).getByRole('button', { name: '上传' }));
    await waitFor(() => expect(calls.find('POST', `${B}/me/plugins/uploads`)).toHaveLength(1));
    expect(calls.find('POST', `${B}/me/plugins/uploads`)[0]?.query.get('targets')).toBe('cloud');
  });

  it('部署没开云端的插件运行环境：只能装给本机对话；本机也不许装时没有上传入口', async () => {
    routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [],
        can_install: true,
        can_install_device: true,
      }),
    });
    const first = renderWithApp(<MyPluginsTab />);
    await userEvent.click(await screen.findByRole('button', { name: '上传插件' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('checkbox', { name: /云端对话/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('checkbox', { name: /本机对话/ })).toBeChecked();
    first.unmount();
    vi.unstubAllGlobals();
    routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [],
        can_install: true,
        can_install_device: false,
      }),
    });
    renderWithApp(<MyPluginsTab />);
    expect(await screen.findByText(/没有开放成员自己安装/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '上传插件' })).not.toBeInTheDocument();
  });

  it('空间没开放个人安装、管理员也没给插件：说明原因，没有上传入口', async () => {
    routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [],
        can_install: false,
        can_install_device: false,
      }),
    });
    renderWithApp(<MyPluginsTab />);
    expect(await screen.findByText('管理员还没有给这个空间启用插件。')).toBeInTheDocument();
    expect(screen.getByText(/没有开放成员自己安装/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('上传的插件的试装结果', () => {
  it('管理页：正在试装的、没通过的在名字下面写明，详情里有试装结果和新增的工具；试装中隔一会儿刷新', async () => {
    const passed = { ...SHARED, id: 'pi_' + 'c'.repeat(32), name: 'stamp', tools: ['kw_stamp'] };
    const failed = {
      ...SHARED,
      id: 'pi_' + 'd'.repeat(32),
      name: 'broken',
      status: 'failed',
      reason: '激活失败：缺依赖',
    };
    const checking = { ...SHARED, status: 'checking' };
    const calls = routeFetch({
      ...ADMIN_EXTRA,
      [`GET ${B}/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/plugins/uploads`]: () =>
        jsonResponse({ ...NO_UPLOADS, plugins: [checking, passed, failed] }),
      [`GET ${B}/plugins/uploads/*/detail`]: jsonResponse({ detail: '' }),
    });
    renderWithApp(<PluginsPage />);
    await userEvent.click(await screen.findByRole('tab', { name: '空间插件' }));
    const row = (name: string) => screen.getByText(name).closest('tr')!;
    expect(
      await within(await waitFor(() => row('expense-check'))).findByText(/正在试装/),
    ).toBeInTheDocument();
    expect(within(row('broken')).getByText('没通过试装，不会生效：激活失败：缺依赖')).toBeInTheDocument();
    expect(within(row('stamp')).queryByText(/试装/)).not.toBeInTheDocument();
    await waitFor(() => expect(calls.find('GET', `${B}/plugins/uploads`).length).toBeGreaterThan(1), {
      timeout: 4000,
    });

    await userEvent.click(within(row('stamp')).getByRole('button', { name: '详情' }));
    const sheet = await screen.findByRole('dialog', { name: 'stamp' });
    expect(within(sheet).getByText('通过')).toBeInTheDocument();
    expect(within(sheet).getByText('kw_stamp')).toBeInTheDocument();
  });

  it('我的插件：还在试装的开关不能动；没开云端插件运行环境时详情写明没在服务端试装', async () => {
    const mine = {
      id: PERSONAL.id,
      name: 'my-weekly',
      version: '0.1.0',
      description: '',
      scope: 'personal',
      targets: ['device'],
      capabilities: [],
      reason: '',
      enabled: true,
      locked: false,
      blocked: false,
    };
    routeFetch({
      [`GET ${B}/me/plugins`]: jsonResponse({ plugins: [] }),
      [`GET ${B}/me/plugins/uploads`]: jsonResponse({
        plugins: [
          { ...mine, status: 'checking' },
          { ...mine, id: 'pi_' + 'e'.repeat(32), name: 'offline', status: 'ready' },
        ],
        can_install: true,
        can_install_device: true,
        cloud_available: false,
      }),
      [`GET ${B}/me/plugins/uploads/*/detail`]: jsonResponse({ detail: '' }),
    });
    renderWithApp(<MyPluginsTab />);
    expect(await screen.findByRole('switch', { name: 'my-weekly 0.1.0' })).toBeDisabled();
    expect(screen.getByText(/正在试装/)).toBeInTheDocument();
    const offline = screen.getByText('offline 0.1.0').closest<HTMLElement>('.kw-setting-row')!;
    await userEvent.click(within(offline).getByRole('button', { name: '详情' }));
    const sheet = await screen.findByRole('dialog', { name: 'offline' });
    expect(within(sheet).getByText(/没有在服务端试装/)).toBeInTheDocument();
  });
});
