/**
 * 渠道：企业微信、飞书、钉钉机器人。机器人不认人，私聊、群聊都用渠道账号回答，只查选的知识库；问答记在
 * 渠道账号名下，运行记录里按它筛。一律长连接，服务器主动连出去，不用开入站端口。连接状态与最近一次错误在表里看。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  deleteChannel,
  listChannels,
  listChannelSessions,
  platformOf,
  setChannelEnabled,
  type Channel,
  type ChannelSession,
  type ChannelStatus,
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

const STATUS: Readonly<Record<ChannelStatus, { readonly tone: Tone; readonly label: string }>> = {
  connected: { tone: 'success', label: '已连接' },
  connecting: { tone: 'processing', label: '连接中' },
  error: { tone: 'error', label: '出错' },
  stopped: { tone: 'default', label: '已停用' },
};

function StatusCell({ channel }: { readonly channel: Channel }) {
  const status = channel.enabled ? STATUS[channel.status] : STATUS.stopped;
  return (
    <div className="kw-admin-status">
      <StatusDot tone={status.tone}>{status.label}</StatusDot>
      {channel.enabled && channel.status === 'error' && channel.lastError ? (
        <span className="kw-secondary">{channel.lastError}</span>
      ) : null}
    </div>
  );
}

/** 运行记录按渠道账号（和渠道同 id）筛，看最近 30 天的问答。 */
const runsOf = (channel: Channel) =>
  `/admin/runs?${new URLSearchParams({ user: channel.id, days: '30' }).toString()}`;

function SessionsSheet({ channel, onClose }: { readonly channel: Channel; readonly onClose: () => void }) {
  const sessions = useQuery({
    queryKey: [...KEYS.channels, channel.id, 'sessions'],
    queryFn: () => listChannelSessions(channel.id),
  });
  const columns: readonly Column<ChannelSession>[] = [
    {
      key: 'who',
      title: '提问人',
      width: 160,
      ellipsis: true,
      render: (s) => s.displayName || s.externalId,
    },
    {
      key: 'where',
      title: '在哪',
      width: 160,
      ellipsis: true,
      render: (s) => (s.chatType === 'group' ? `群 ${s.chatId}` : '私聊'),
    },
    { key: 'title', title: '第一个问题', ellipsis: true, render: (s) => s.title || '未命名' },
    { key: 'at', title: '最近提问', width: 170, render: (s) => formatDateTime(s.updatedAt) },
  ];
  return (
    <Sheet title={`谁在问 · ${channel.name}`} width={760} onClose={onClose}>
      <p className="kw-secondary">
        每个人在私聊、在每个群里各是一个对话，发「新会话」会另起一个。每一轮问答的详细过程在
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
    </Sheet>
  );
}

export default function ChannelsPage() {
  // 连接中、出错时状态会变：每 10 秒刷新一次
  const channels = useQuery({ queryKey: KEYS.channels, queryFn: listChannels, refetchInterval: 10_000 });
  const [editing, setEditing] = useState<{ readonly item?: Channel } | null>(null);
  const [people, setPeople] = useState<Channel | null>(null);
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
    { key: 'bot', title: '机器人', ellipsis: true, render: (c) => c.botName || c.botId },
    { key: 'status', title: '状态', width: 220, render: (c) => <StatusCell channel={c} /> },
    {
      key: 'bases',
      title: '知识库',
      width: 90,
      align: 'right',
      render: (c) => `${c.settings.bases.length} 个`,
    },
    { key: 'sessions', title: '对话', width: 90, align: 'right', render: (c) => `${c.sessions} 个` },
    {
      key: 'actions',
      title: '操作',
      width: 230,
      render: (c) => (
        <RowActions
          actions={[
            { key: 'edit', label: '修改', onClick: () => setEditing({ item: c }) },
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
              confirm: `删除渠道「${c.name}」？机器人会断开，授给它的知识库一并收回；运行记录里的问答还留着。`,
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
      description="让员工在企业微信、飞书、钉钉里直接向智能助手提问，不用登录 KnowWork。机器人只查给它选的知识库，谁能找到机器人由 IM 平台的可见范围决定。"
    >
      <Toolbar
        right={
          <Button color="primary" onClick={() => setEditing({})}>
            <Plus />
            添加机器人
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
    </AdminPage>
  );
}
