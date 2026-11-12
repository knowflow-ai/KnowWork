/**
 * 渠道：企业微信、飞书、钉钉机器人与网页嵌入。不认人的私聊、群聊都用渠道账号回答，只查选的资料（知识库，或回答方式要的那类资源），
 * 问答记在渠道账号名下，
 * 运行记录里按它筛；认人的私聊按提问人本人回答、记在本人名下，「谁在问」里显示成员姓名，另列没认出的人。
 * 机器人一律长连接，服务器主动连出去，不用开入站端口。连接状态与最近一次错误在表里看。
 * 网页嵌入不用连接：开着就能用，「嵌入代码」里复制悬浮窗、整页与分享链接（EmbedCodeSheet）。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  BUILT_IN_CHANNEL_MODES,
  deleteChannel,
  KNOWLEDGE_RESOURCE,
  listChannelAnswerModes,
  listChannels,
  listChannelSessions,
  listChannelStrangers,
  platformOf,
  setChannelEnabled,
  type Channel,
  type ChannelSession,
  type ChannelStatus,
  type ChannelStranger,
  type StrangerReason,
} from '../../../api/channels';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot, type Tone } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { formatDateTime } from '../../../utils/format';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { KEYS } from '../queries';
import { useAction } from '../useAction';
import { ChannelDialog } from './ChannelDialog';
import { EmbedCodeSheet } from './EmbedCodeSheet';

const STATUS: Readonly<Record<ChannelStatus, { readonly tone: Tone; readonly label: string }>> = {
  connected: { tone: 'success', label: '已连接' },
  connecting: { tone: 'processing', label: '连接中' },
  error: { tone: 'error', label: '出错' },
  stopped: { tone: 'default', label: '已停用' },
};

const READY = { tone: 'success', label: '可用' } as const;

function StatusCell({ channel }: { readonly channel: Channel }) {
  // 网页嵌入不用连接：开着就能用
  const live = channel.platform === 'web' ? READY : STATUS[channel.status];
  const status = channel.enabled ? live : STATUS.stopped;
  return (
    <div className="kw-admin-status">
      <StatusDot tone={status.tone}>{status.label}</StatusDot>
      {channel.enabled && channel.status === 'error' && channel.lastError ? (
        <span className="kw-secondary">{channel.lastError}</span>
      ) : null}
    </div>
  );
}

const REASONS: Readonly<Record<StrangerReason, string>> = {
  unknown: '还没对上：没同步进来，也没用组织来源登录过',
  inactive: '账号已停用或已离职',
  not_member: '不是这个空间的成员',
};

/** 运行记录按渠道账号（和渠道同 id）筛，看最近 30 天的问答。 */
const runsOf = (channel: Channel) =>
  `/admin/runs?${new URLSearchParams({ user: channel.id, days: '30' }).toString()}`;

function SessionsSheet({ channel, onClose }: { readonly channel: Channel; readonly onClose: () => void }) {
  const sessions = useQuery({
    queryKey: [...KEYS.channels, channel.id, 'sessions'],
    queryFn: () => listChannelSessions(channel.id),
  });
  const member = channel.audience === 'member';
  const strangers = useQuery({
    queryKey: [...KEYS.channels, channel.id, 'strangers'],
    queryFn: () => listChannelStrangers(channel.id),
    enabled: member,
  });
  const columns: readonly Column<ChannelSession>[] = [
    {
      key: 'who',
      title: '提问人',
      width: 160,
      ellipsis: true,
      // 认出的人显示成员姓名；渠道账号回答的（群聊、不认人）显示平台上的名字
      render: (s) => s.userName || s.displayName || s.externalId,
    },
    {
      key: 'where',
      title: '在哪',
      width: 160,
      ellipsis: true,
      render: (s) => (s.chatType === 'group' ? `群 ${s.chatId}` : s.userId ? '私聊 · 本人权限' : '私聊'),
    },
    { key: 'title', title: '第一个问题', ellipsis: true, render: (s) => s.title || '未命名' },
    { key: 'at', title: '最近提问', width: 170, render: (s) => formatDateTime(s.updatedAt) },
  ];
  return (
    <Sheet title={`谁在问 · ${channel.name}`} width={760} onClose={onClose}>
      <p className="kw-secondary">
        {channel.platform === 'web'
          ? '网页嵌入里每个访客一个对话，点「新对话」会另起一个。'
          : '每个人在私聊、在每个群里各是一个对话，发「新会话」会另起一个。'}
        每一轮问答的详细过程在
        <Link to={runsOf(channel)}>运行记录</Link>里看。
      </p>
      {sessions.isPending ? (
        <Skeleton rows={4} />
      ) : (
        <Table<ChannelSession>
          rowKey={(s) => `${s.chatId}:${s.externalId}`}
          columns={columns}
          rows={sessions.data ?? []}
          empty={sessions.isError ? '加载失败，请刷新重试' : '还没有人提问'}
        />
      )}
      {member ? <Strangers query={strangers} /> : null}
    </Sheet>
  );
}

function Strangers({
  query,
}: {
  readonly query: {
    readonly isPending: boolean;
    readonly isError: boolean;
    readonly data?: readonly ChannelStranger[];
  };
}) {
  const columns: readonly Column<ChannelStranger>[] = [
    {
      key: 'who',
      title: '平台账号',
      width: 160,
      ellipsis: true,
      render: (s) =>
        s.displayName && s.displayName !== s.externalId
          ? `${s.displayName}（${s.externalId}）`
          : s.externalId,
    },
    { key: 'reason', title: '原因', ellipsis: true, render: (s) => REASONS[s.reason] },
    { key: 'attempts', title: '次数', width: 70, render: (s) => s.attempts },
    { key: 'at', title: '最近一次', width: 170, render: (s) => formatDateTime(s.lastAt) },
  ];
  return (
    <>
      <h3>没认出的人</h3>
      <p className="kw-secondary">私聊了机器人却没认出来，没有回答。认出来以后会从这里消失。</p>
      {query.isPending ? (
        <Skeleton rows={2} />
      ) : (
        <Table<ChannelStranger>
          rowKey={(s) => s.externalId}
          columns={columns}
          rows={query.data ?? []}
          empty={query.isError ? '加载失败，请刷新重试' : '都认出来了'}
        />
      )}
    </>
  );
}

export default function ChannelsPage() {
  // 连接中、出错时状态会变：每 10 秒刷新一次
  const channels = useQuery({ queryKey: KEYS.channels, queryFn: listChannels, refetchInterval: 10_000 });
  const modes = useQuery({ queryKey: [...KEYS.channels, 'answer-modes'], queryFn: listChannelAnswerModes });
  const modeOf = (c: Channel) =>
    (modes.data ?? BUILT_IN_CHANNEL_MODES).find((m) => m.id === c.settings.answerMode);
  const [editing, setEditing] = useState<{ readonly item?: Channel } | null>(null);
  const [people, setPeople] = useState<Channel | null>(null);
  const [code, setCode] = useState<Channel | null>(null);
  const toggle = useAction(
    ({ id, enabled }: { readonly id: string; readonly enabled: boolean }) => setChannelEnabled(id, enabled),
    { invalidate: [KEYS.channels], failure: '没有改成功，请稍后重试' },
  );
  const remove = useAction((id: string) => deleteChannel(id), {
    invalidate: [KEYS.channels],
    success: '已删除渠道',
    failure: '删除没有成功，请稍后重试',
  });
  const columns: readonly Column<Channel>[] = [
    { key: 'name', title: '名称', width: 160, ellipsis: true, render: (c) => c.name },
    {
      key: 'platform',
      title: '平台',
      width: 100,
      render: (c) => platformOf(c.platform).label,
    },
    {
      key: 'bot',
      title: '机器人',
      ellipsis: true,
      render: (c) => (c.platform === 'web' ? c.web?.title || '—' : c.botName || c.botId),
    },
    {
      key: 'audience',
      title: '提问的人',
      width: 110,
      render: (c) =>
        c.platform === 'web'
          ? c.audience === 'member'
            ? '认证访问'
            : '公开访问'
          : c.audience === 'member'
            ? '认出是谁'
            : '不认人',
    },
    { key: 'status', title: '状态', width: 220, render: (c) => <StatusCell channel={c} /> },
    {
      key: 'bases',
      title: '回答方式',
      width: 150,
      render: (c) => {
        // 挂了对象（例如专家）的网页嵌入：照它答，写它是谁
        if (c.target) return `${c.target.label}：${c.target.name ?? '已删除'}`;
        const mode = modeOf(c);
        if (!mode) return c.settings.answerMode;
        const count =
          mode.resourceType === KNOWLEDGE_RESOURCE ? c.settings.bases.length : c.settings.resources.length;
        // 认人的渠道私聊按本人权限，选的只管群聊：一个没选就是只开私聊
        if (c.audience === 'member')
          return count > 0
            ? `${mode.label} · 群聊 ${count} 个${mode.resourceLabel}`
            : `${mode.label} · 只开私聊`;
        return `${mode.label} · ${count} 个${mode.resourceLabel}`;
      },
    },
    { key: 'sessions', title: '对话', width: 90, align: 'right', render: (c) => `${c.sessions} 个` },
    {
      key: 'actions',
      title: '操作',
      width: 300,
      render: (c) => (
        <RowActions
          actions={[
            { key: 'edit', label: '修改', onClick: () => setEditing({ item: c }) },
            ...(c.platform === 'web' ? [{ key: 'code', label: '嵌入代码', onClick: () => setCode(c) }] : []),
            { key: 'people', label: '谁在问', onClick: () => setPeople(c) },
            {
              key: 'toggle',
              label: c.enabled ? '停用' : '启用',
              onClick: () => void toggle.run({ id: c.id, enabled: !c.enabled }),
            },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm:
                c.platform === 'web'
                  ? `删除渠道「${c.name}」？嵌入代码和分享链接都会失效，授给它的知识库、数据这些权限一并收回；运行记录里的问答还留着。`
                  : `删除渠道「${c.name}」？机器人会断开，授给它的知识库、数据这些权限一并收回；运行记录里的问答还留着。`,
              onClick: () => void remove.run(c.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <AdminPage
      title="渠道"
      description="让员工在企业微信、飞书、钉钉里，或在你们自己的网站上直接向智能助手提问，不用登录 KnowWork。它只查给它选的资料；谁能找到机器人由 IM 平台的可见范围决定，网页嵌入只能嵌在允许的网站里。"
    >
      <Toolbar
        right={
          <Button color="primary" onClick={() => setEditing({})}>
            <Plus />
            添加渠道
          </Button>
        }
      />
      {channels.isPending ? (
        <Skeleton rows={4} />
      ) : (
        <Table<Channel>
          rowKey={(c) => c.id}
          columns={columns}
          rows={channels.data ?? []}
          empty={channels.isError ? '渠道加载失败，请刷新重试' : '还没有渠道'}
        />
      )}
      {editing ? <ChannelDialog channel={editing.item} onClose={() => setEditing(null)} /> : null}
      {people ? <SessionsSheet channel={people} onClose={() => setPeople(null)} /> : null}
      {code ? <EmbedCodeSheet channel={code} onClose={() => setCode(null)} /> : null}
    </AdminPage>
  );
}
