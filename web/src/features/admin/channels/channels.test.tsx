import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { register } from '../../../slots/registry';
import ChannelsPage from './ChannelsPage';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/channels';
const CHANNEL = {
  id: 'ch1',
  platform: 'wecom',
  name: '行政助手',
  enabled: true,
  bot_id: 'bot-1',
  bot_name: '行政机器人',
  secret_mask: '3456',
  secret_set: true,
  settings: { answer_mode: 'agent', bases: ['builtin.kb1'] },
  status: 'error',
  last_error: '认证失败，请检查 Bot ID 与 Secret',
  status_at: null,
  sessions: 2,
  audience: 'public',
};
const MEMBER_CHANNEL = {
  ...CHANNEL,
  id: 'ch2',
  name: '数据助手',
  audience: 'member',
  status: 'connected',
  last_error: '',
  settings: { answer_mode: 'agent', bases: [], identity_source: 'src-wecom' },
};
const SOURCES = [{ id: 'src-wecom', name: '示例科技', login: true }];
const KB = { resource_type: 'knowledge_base', resource_label: '知识库' };
const MODES = [
  { id: 'agent', label: '智能体', description: '能用技能', ...KB },
  { id: 'quick', label: '快速问答', description: '只查知识库', ...KB },
  {
    id: 'numbers',
    label: '问数',
    description: '按语义模型的口径查数据库里的数',
    resource_type: 'data_source',
    resource_label: '数据源',
  },
];
const BASES = [
  {
    source_id: 'builtin',
    source_name: '公司知识库',
    auth_mode: 'shared',
    enabled: true,
    bases: [{ id: 'builtin.kb1', kb_id: 'kb1', name: '员工手册' }],
  },
];

function api({ sources = SOURCES }: { readonly sources?: readonly unknown[] } = {}) {
  return routeFetch({
    [`GET ${B}`]: jsonResponse({ items: [CHANNEL, MEMBER_CHANNEL] }),
    [`GET ${B}/identity-sources`]: jsonResponse({ items: sources }),
    [`GET ${B}/answer-modes`]: jsonResponse({ items: MODES }),
    [`GET ${B}/ch2/sessions`]: jsonResponse({
      items: [
        {
          external_id: 'zhangsan',
          display_name: 'zhangsan',
          chat_type: 'direct',
          chat_id: '',
          conversation_id: 'c3',
          title: '我的报销额度',
          updated_at: '2026-11-07T02:00:00Z',
          user_id: 'u-zhang',
          user_name: '张三',
        },
      ],
    }),
    [`GET ${B}/ch2/strangers`]: jsonResponse({
      items: [
        {
          external_id: 'xiaowang',
          display_name: '小王',
          reason: 'unknown',
          attempts: 2,
          last_at: '2026-11-07T03:00:00Z',
        },
      ],
    }),
    [`POST ${B}`]: jsonResponse(CHANNEL, 201),
    [`PUT ${B}/ch1`]: jsonResponse(CHANNEL),
    [`POST ${B}/ch1/enabled`]: jsonResponse({ ...CHANNEL, enabled: false }),
    [`GET ${B}/ch1/sessions`]: jsonResponse({
      items: [
        {
          external_id: 'zhangsan',
          display_name: '张三',
          chat_type: 'direct',
          chat_id: '',
          conversation_id: 'c1',
          title: '报销标准是多少',
          updated_at: '2026-10-27T02:00:00Z',
        },
        {
          external_id: 'lisi',
          display_name: '',
          chat_type: 'group',
          chat_id: 'wrkSFfCgAA',
          conversation_id: 'c2',
          title: '出差住宿',
          updated_at: '2026-10-27T03:00:00Z',
        },
      ],
    }),
    'GET /api/workspaces/default/knowledge-bases': jsonResponse(BASES),
  });
}

const fill = (dialog: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

describe('渠道', () => {
  it('列表显示连接状态、出错原因、知识库与对话数；可以停用', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('行政助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    expect(within(row).getByText('出错')).toBeInTheDocument();
    expect(within(row).getByText('认证失败，请检查 Bot ID 与 Secret')).toBeInTheDocument();
    expect(within(row).getByText('智能体 · 1 个知识库')).toBeInTheDocument();
    expect(within(row).getByText('2 个')).toBeInTheDocument();
    // 四个操作：露两个，停用、删除收在「更多」里
    await userEvent.click(within(row).getByRole('button', { name: /更多/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '停用' }));
    await waitFor(() => expect(calls.find('POST', `${B}/ch1/enabled`)[0]?.body).toEqual({ enabled: false }));
  });

  it('添加机器人：必填 Secret；至少选一个知识库', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    await screen.findAllByText('行政助手');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '财务助手');
    // 企业微信最常见的填错：自建应用的 AgentId
    fill(dialog, 'Bot ID', '1000006');
    expect(
      within(dialog).getByText('这像是自建应用的 AgentId，智能机器人的 Bot ID 不是纯数字'),
    ).toBeInTheDocument();
    fill(dialog, 'Bot ID', 'bot-2');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写 Secret');
    fill(dialog, 'Secret', 'sk-secret-000000');
    expect(within(dialog).getByText(/能找到这个机器人的人都能查到/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText('请至少选一个知识库')).toBeInTheDocument();
    await pickOption(dialog, '可查的知识库', '员工手册');
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toEqual({
        name: '财务助手',
        platform: 'wecom',
        enabled: true,
        bot_id: 'bot-2',
        bot_name: '',
        secret: 'sk-secret-000000',
        audience: 'public',
        settings: { answer_mode: 'agent', bases: ['builtin.kb1'], resources: [], identity_source: null },
      }),
    );
  });

  it('回答方式选查数据源的：可查的资料换成数据源，列表来自授权页登记的那一类', async () => {
    register('grants.resources', {
      type: 'data_source',
      label: '数据源',
      load: async () => [{ id: 'ds_sales', name: '销售库' }],
    });
    const calls = api();
    renderWithApp(<ChannelsPage />);
    await screen.findAllByText('行政助手');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '数据助手');
    fill(dialog, 'Bot ID', 'bot-9');
    fill(dialog, 'Secret', 'sk-secret-999999');
    await userEvent.click(await within(dialog).findByRole('radio', { name: '问数' }));
    expect(within(dialog).getByText(/它追问时直接在 IM 里回答/)).toBeInTheDocument();
    expect(within(dialog).queryByText('可查的知识库')).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText('请至少选一个数据源')).toBeInTheDocument();
    await pickOption(dialog, '可查的数据源', '销售库');
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        settings: { answer_mode: 'numbers', resources: ['ds_sales'] },
      }),
    );
  });

  it('认出是谁：选组织来源，群聊知识库可以不选；没有组织来源时置灰', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('数据助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('认出是谁');
    expect(row).toHaveTextContent('智能体 · 只开私聊');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    fill(dialog, '名称', '数据助手二');
    fill(dialog, 'Bot ID', 'bot-3');
    fill(dialog, 'Secret', 'sk-secret-111111');
    await userEvent.click(await within(dialog).findByRole('radio', { name: '认出是谁' }));
    expect(within(dialog).getByText(/私聊按提问人自己的知识库、数据权限回答/)).toBeInTheDocument();
    expect(within(dialog).getByText('群聊可查的知识库')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByText('请选认人用的组织来源')).toBeInTheDocument();
    await pickOption(dialog, '按哪个组织来源认人', '示例科技');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        audience: 'member',
        settings: { answer_mode: 'agent', bases: [], identity_source: 'src-wecom' },
      }),
    );
  });

  it('没有可选的组织来源时不能切到认出是谁', async () => {
    api({ sources: [] });
    renderWithApp(<ChannelsPage />);
    await screen.findAllByText('行政助手');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/先在「组织架构」里配置同平台的组织来源/)).toBeInTheDocument();
    expect(within(dialog).getByRole('radio', { name: '认出是谁' })).toBeDisabled();
  });

  it('认人的渠道：谁在问里显示成员姓名，另列没认出的人', async () => {
    api();
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('数据助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '谁在问' }));
    const sheet = await screen.findByRole('dialog', { name: /谁在问/ });
    const person = (await within(sheet).findByText('张三')).closest('tr') as HTMLElement;
    expect(person).toHaveTextContent('私聊 · 本人权限');
    const stranger = (await within(sheet).findByText('小王（xiaowang）')).closest('tr') as HTMLElement;
    expect(stranger).toHaveTextContent('还没对上');
    expect(stranger).toHaveTextContent('2');
  });

  it('修改时 Secret 留空表示不改；可以看谁在问', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('行政助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/已配置（末四位 3456）/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', `${B}/ch1`)[0]?.body).toMatchObject({ secret: null }));

    await userEvent.click(within(row).getByRole('button', { name: '谁在问' }));
    const sheet = await screen.findByRole('dialog', { name: /谁在问/ });
    const person = (await within(sheet).findByText('张三')).closest('tr') as HTMLElement;
    expect(person).toHaveTextContent('私聊');
    expect(person).toHaveTextContent('报销标准是多少');
    // 平台没给名字的显示平台账号
    const inGroup = within(sheet).getByText('lisi').closest('tr') as HTMLElement;
    expect(inGroup).toHaveTextContent('群 wrkSFfCgAA');
    expect(within(sheet).getByRole('link', { name: '运行记录' })).toHaveAttribute(
      'href',
      '/admin/runs?user=ch1&days=30',
    );
  });

  it('选飞书：凭据叫 App ID / App Secret，不要机器人名称', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    await screen.findAllByText('行政助手');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('radio', { name: '飞书' }));
    expect(within(dialog).getByText(/使用长连接接收事件/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('机器人名称')).toBeNull();
    fill(dialog, '名称', '飞书助手');
    fill(dialog, 'App ID', 'cli_a1b2');
    fill(dialog, 'App Secret', 'secret-feishu-0000');
    await pickOption(dialog, '可查的知识库', '员工手册');
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        platform: 'feishu',
        bot_id: 'cli_a1b2',
        secret: 'secret-feishu-0000',
      }),
    );
  });
});

const WEB_CHANNEL = {
  ...CHANNEL,
  id: 'ch3',
  platform: 'web',
  name: '官网助手',
  bot_id: '',
  bot_name: '',
  secret_mask: '',
  secret_set: false,
  status: 'stopped',
  last_error: '',
  sessions: 0,
  web: {
    web_key: 'default~abcdefghijklmnopqrstuvwx',
    origins: ['https://bi.example.com'],
    title: 'AI 问数',
    greeting: '',
    starters: [],
    color: '',
    position: 'right',
    per_minute: 10,
    per_day: 1000,
  },
};

describe('网页嵌入', () => {
  it('添加：没有机器人与 Secret；校验允许嵌入的网站；智能体方式提醒访客也能执行命令', async () => {
    const calls = api();
    renderWithApp(<ChannelsPage />);
    await screen.findAllByText('行政助手');
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('radio', { name: '网页嵌入' }));
    expect(within(dialog).queryByLabelText('Bot ID')).toBeNull();
    expect(within(dialog).queryByLabelText('Secret')).toBeNull();
    expect(within(dialog).queryByText('提问的人')).toBeNull();
    expect(within(dialog).getByText(/访客也能让它执行命令、读写文件/)).toBeInTheDocument();
    fill(dialog, '名称', '官网助手');
    fill(dialog, '允许嵌入的网站', 'https://bi.example.com\nbi.example.com/path');
    await pickOption(dialog, '可查的知识库', '员工手册');
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('不带路径');
    fill(dialog, '允许嵌入的网站', 'https://bi.example.com\nhttps://*.example.cn');
    fill(dialog, '快捷提问', '上月销售额\n\n报销标准');
    fill(dialog, '小窗名字', 'AI 问数');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        name: '官网助手',
        platform: 'web',
        bot_id: '',
        secret: null,
        audience: 'public',
        settings: { answer_mode: 'agent', bases: ['builtin.kb1'] },
        web: {
          origins: ['https://bi.example.com', 'https://*.example.cn'],
          title: 'AI 问数',
          starters: ['上月销售额', '报销标准'],
          position: 'right',
          per_minute: 10,
          per_day: 1000,
        },
      }),
    );
  });

  it('列表里开着就是可用；嵌入代码能复制，发布令牌能重置', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const calls = routeFetch({
      [`GET ${B}`]: jsonResponse({ items: [WEB_CHANNEL] }),
      [`GET ${B}/answer-modes`]: jsonResponse({ items: MODES }),
      [`POST ${B}/ch3/web-key`]: jsonResponse(WEB_CHANNEL),
    });
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('官网助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    expect(within(row).getByText('可用')).toBeInTheDocument();
    expect(within(row).getByText('公开访问')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '嵌入代码' }));
    const sheet = await screen.findByRole('dialog');
    const origin = window.location.origin;
    const script = `<script src="${origin}/embed/v1/widget.js" data-channel="default~abcdefghijklmnopqrstuvwx" async></script>`;
    expect(within(sheet).getByText(script)).toBeInTheDocument();
    expect(within(sheet).getByText(`${origin}/s/default~abcdefghijklmnopqrstuvwx`)).toBeInTheDocument();
    expect(within(sheet).getByText(/只能嵌在这些网站里：https:\/\/bi.example.com/)).toBeInTheDocument();
    await userEvent.click(within(sheet).getAllByRole('button', { name: '复制' })[0]!);
    expect(writeText).toHaveBeenCalledWith(script);
    await userEvent.click(within(sheet).getByRole('button', { name: '重置发布令牌' }));
    await userEvent.click(await screen.findByRole('button', { name: '重置' }));
    await waitFor(() => expect(calls.find('POST', `${B}/ch3/web-key`)).toHaveLength(1));
  });
});

describe('网页嵌入的认证访问', () => {
  const GROUPS = [{ id: 'grp_east', name: '华东销售', members: [] }];
  const IDENTIFIED = {
    ...WEB_CHANNEL,
    id: 'ch4',
    name: 'BI 助手',
    audience: 'member',
    secret_set: false,
    secret_mask: '',
    settings: { answer_mode: 'agent', bases: [], identity_source: null, auto_create: true, group_map: {} },
  };

  it('添加：选认证访问，资料可以不选，按组对照进用户组', async () => {
    const calls = routeFetch({
      [`GET ${B}`]: jsonResponse({ items: [] }),
      [`GET ${B}/answer-modes`]: jsonResponse({ items: MODES }),
      [`GET ${B}/identity-sources`]: jsonResponse({ items: SOURCES }),
      'GET /api/workspaces/default/groups': jsonResponse(GROUPS),
      'GET /api/workspaces/default/knowledge-bases': jsonResponse(BASES),
      [`POST ${B}`]: jsonResponse(IDENTIFIED, 201),
    });
    renderWithApp(<ChannelsPage />);
    await userEvent.click(await screen.findByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('radio', { name: '网页嵌入' }));
    await userEvent.click(within(dialog).getByRole('radio', { name: '认证访问' }));
    expect(within(dialog).getByText('限定在这些知识库里')).toBeInTheDocument();
    expect(within(dialog).getByText(/对不上就按下面的设置建外部用户/)).toBeInTheDocument();
    fill(dialog, '名称', 'BI 助手');
    await userEvent.click(within(dialog).getByRole('button', { name: /添加一行/ }));
    fill(dialog, '接入方的组', '华东区域经理');
    await userEvent.click(within(dialog).getByRole('button', { name: /选择用户组/ }));
    await userEvent.click(await screen.findByRole('option', { name: '华东销售' }));
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        platform: 'web',
        audience: 'member',
        settings: {
          answer_mode: 'agent',
          bases: [],
          identity_source: null,
          auto_create: true,
          group_map: { 华东区域经理: 'grp_east' },
        },
      }),
    );
  });

  it('嵌入代码：生成认证密钥只显示一次；悬浮窗带取登录信息的地址，附后端签名示例', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const calls = routeFetch({
      [`GET ${B}`]: jsonResponse({ items: [IDENTIFIED] }),
      [`GET ${B}/answer-modes`]: jsonResponse({ items: MODES }),
      [`POST ${B}/ch4/web-secret`]: jsonResponse({
        channel: { ...IDENTIFIED, secret_set: true, secret_mask: 'wxyz' },
        secret: 'super-secret-0123456789-abcdefghijklmnopqrstuvwxyz',
      }),
    });
    renderWithApp(<ChannelsPage />);
    const row = (await screen.findByText('BI 助手', { selector: 'td *, td' })).closest('tr') as HTMLElement;
    expect(within(row).getByText('认证访问')).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '嵌入代码' }));
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText(/还没有生成/)).toBeInTheDocument();
    expect(within(sheet).getByText(/data-token-url="\/api\/knowwork-token"/)).toBeInTheDocument();
    expect(within(sheet).getByText(/pip install pyjwt/)).toBeInTheDocument();
    expect(within(sheet).queryByText('分享链接')).toBeNull();
    await userEvent.click(within(sheet).getByRole('button', { name: '生成认证密钥' }));
    expect(
      await within(sheet).findByText('super-secret-0123456789-abcdefghijklmnopqrstuvwxyz'),
    ).toBeInTheDocument();
    expect(within(sheet).getByText(/只显示这一次/)).toBeInTheDocument();
    expect(calls.find('POST', `${B}/ch4/web-secret`)).toHaveLength(1);
  });
});

describe('网页嵌入挂专家', () => {
  const TARGETS = [{ ref: 'expert:ex1', label: '专家', name: '经营分析', answer_mode: 'numbers' }];
  const FOLLOWING = {
    ...WEB_CHANNEL,
    id: 'ch5',
    name: 'BI 小窗',
    settings: { answer_mode: 'numbers', bases: [], resources: [], target: 'expert:ex1' },
    target: { ref: 'expert:ex1', label: '专家', name: '经营分析' },
  };
  const ORPHAN = {
    ...FOLLOWING,
    id: 'ch6',
    name: '旧小窗',
    target: { ref: 'expert:gone', label: '专家', name: null },
  };

  it('选了专家就不选回答方式与资料，存的是挂的对象；列表写挂的是谁，删了的写已删除', async () => {
    const calls = routeFetch({
      [`GET ${B}`]: jsonResponse({ items: [FOLLOWING, ORPHAN] }),
      [`GET ${B}/answer-modes`]: jsonResponse({ items: MODES }),
      [`GET ${B}/identity-sources`]: jsonResponse({ items: SOURCES }),
      [`GET ${B}/targets`]: jsonResponse({ items: TARGETS }),
      'GET /api/workspaces/default/knowledge-bases': jsonResponse(BASES),
      [`POST ${B}`]: jsonResponse(FOLLOWING, 201),
    });
    renderWithApp(<ChannelsPage />);
    expect(await screen.findByText('专家：经营分析')).toBeInTheDocument();
    expect(screen.getByText('专家：已删除')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /添加渠道/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('radio', { name: '网页嵌入' }));
    expect(within(dialog).getByRole('group', { name: '回答方式' })).toBeInTheDocument();
    await within(dialog).findByRole('group', { name: '按谁回答' }); // 能挂的对象取回来才出这一项
    await pickOption(dialog, '按谁回答', '专家：经营分析');
    expect(within(dialog).queryByRole('group', { name: '回答方式' })).toBeNull();
    expect(within(dialog).queryByText('可查的知识库')).toBeNull();
    expect(
      within(dialog).getByText(/按专家「经营分析」的回答方式、说明和能查的资料回答/),
    ).toBeInTheDocument();
    fill(dialog, '名称', 'BI 小窗');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('POST', B)[0]?.body).toMatchObject({
        platform: 'web',
        settings: { answer_mode: 'numbers', target: 'expert:ex1' },
      }),
    );
  });
});
