/**
 * 能力接入 → 插件，三页：
 * - 预置：随版本提供的插件，管理员给每个定档。关闭：谁都没有；全员启用：空间里的对话都带上，成员不能关；
 *   可选：成员在个人设置的「我的插件」里自己决定开不开；
 * - 空间插件：管理员上传的插件包，定启用方式、给谁用、适用于（WorkspacePlugins）；
 * - 成员安装的：成员自己装的个人插件，和管控开关、禁用清单（MemberPlugins）。
 * 改完以后新开的对话和下一次恢复的对话生效。
 */
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Lock, Warning } from '@openai/apps-sdk-ui/components/Icon';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, readableError } from '../../../api/http';
import {
  listWorkspacePlugins,
  listWorkspaceUploads,
  PLUGIN_MODES,
  setPluginMode,
  type PluginMode,
  type WorkspacePlugin,
  type WorkspaceUploads,
} from '../../../api/plugins';
import { currentWorkspaceId } from '../../../stores/workspace';
import { Empty } from '../../../ui/Empty';
import { SettingList, SettingRow } from '../../../ui/SettingRow';
import { Skeleton } from '../../../ui/Skeleton';
import { toast } from '../../../ui/toastStore';
import { Tabs } from '../../../ui/Tabs';
import { AdminPage } from '../AdminPage';
import { MemberPlugins } from './MemberPlugins';
import { PluginDetail } from './PluginDetail';
import { builtinDetailView } from './detailView';
import { anyChecking, CHECKING_REFRESH_MS } from './labels';
import { builtinKey, uploadsKey } from './queries';
import { WorkspacePlugins } from './WorkspacePlugins';

const keyOf = builtinKey;
export const MY_PLUGINS = '/settings?tab=plugins';

function PluginRow({ plugin }: { readonly plugin: WorkspacePlugin }) {
  const client = useQueryClient();
  const key = keyOf(currentWorkspaceId());
  const change = useMutation({
    mutationFn: (mode: PluginMode) => setPluginMode(plugin.key, mode),
    onSuccess: (_, mode) => {
      client.setQueryData<readonly WorkspacePlugin[]>(key, (current) =>
        current?.map((item) => (item.key === plugin.key ? { ...item, mode } : item)),
      );
      toast.success('已保存，新开的对话生效');
    },
    onError: (error) => toast.error(readableError(error, '没能保存，请稍后重试')),
  });
  const [viewing, setViewing] = useState(false);
  const mode = change.isPending && change.variables ? change.variables : plugin.mode;
  const description =
    mode === 'optional' ? (
      <>
        {plugin.description}。成员（包括你自己）要在
        <Link className="kw-link" to={MY_PLUGINS}>
          个人设置的「我的插件」
        </Link>
        里打开，新开的对话才有
      </>
    ) : (
      plugin.description
    );
  return (
    <SettingRow title={plugin.name} description={description}>
      <span className="kw-row-actions">
        <Button color="secondary" variant="ghost" size="sm" onClick={() => setViewing(true)}>
          详情
        </Button>
        <SegmentedControl
          aria-label={`${plugin.name}给谁用`}
          size="sm"
          value={mode}
          onChange={(mode) => change.mutate(mode as PluginMode)}
        >
          {PLUGIN_MODES.map((mode) => (
            <SegmentedControl.Option key={mode.value} value={mode.value}>
              {mode.label}
            </SegmentedControl.Option>
          ))}
        </SegmentedControl>
      </span>
      {viewing ? (
        <PluginDetail
          view={builtinDetailView(plugin, [
            {
              key: 'mode',
              label: '启用方式',
              value: PLUGIN_MODES.find((item) => item.value === mode)?.label ?? mode,
            },
          ])}
          onClose={() => setViewing(false)}
        />
      ) : null}
    </SettingRow>
  );
}

function Builtin() {
  const plugins = useQuery({ queryKey: keyOf(currentWorkspaceId()), queryFn: listWorkspacePlugins });
  if (plugins.isPending) return <Skeleton rows={4} />;
  if (plugins.isError) return <LoadProblem error={plugins.error} />;
  if (plugins.data.length === 0) return <p className="kw-secondary">这一版还没有预置的插件。</p>;
  return (
    <>
      <p className="kw-detail-note">
        随版本提供、我们审过的插件。全员启用的每段对话都带上；可选的由成员在个人设置里自己开关。
      </p>
      <SettingList>
        {plugins.data.map((plugin) => (
          <PluginRow key={plugin.key} plugin={plugin} />
        ))}
      </SettingList>
    </>
  );
}

function LoadProblem({ error }: { readonly error: unknown }) {
  return error instanceof ApiError && error.status === 403 ? (
    <Empty area icon={<Lock />} title="没有权限" description="只有空间所有者和管理员能管理插件" />
  ) : (
    <Empty
      area
      icon={<Warning />}
      title="插件没有加载出来"
      description={readableError(error, '请稍后刷新重试')}
    />
  );
}

export default function PluginsPage() {
  const uploads = useQuery({
    queryKey: uploadsKey(currentWorkspaceId()),
    queryFn: listWorkspaceUploads,
    // 有插件在试装：隔一会儿刷新，直到出结果
    refetchInterval: (query) =>
      anyChecking(query.state.data?.plugins) || anyChecking(query.state.data?.memberPlugins)
        ? CHECKING_REFRESH_MS
        : false,
  });
  // 等试装结果的定时刷新不显示表格的加载状态，不然每两秒闪一下
  const polling = anyChecking(uploads.data?.plugins) || anyChecking(uploads.data?.memberPlugins);
  const members = uploads.data?.memberPlugins ?? [];
  const uploaded = (children: (data: WorkspaceUploads) => ReactNode) => {
    if (uploads.isPending) return <Skeleton rows={4} />;
    if (uploads.isError) return <LoadProblem error={uploads.error} />;
    return children(uploads.data);
  };
  const items = [
    { key: 'builtin', label: '预置', children: <Builtin /> },
    {
      key: 'workspace',
      label: '空间插件',
      children: uploaded((data) => (
        <WorkspacePlugins
          plugins={data.plugins}
          loading={uploads.isFetching && !polling}
          cloudAvailable={data.cloudAvailable}
        />
      )),
    },
    {
      key: 'members',
      label: members.length > 0 ? `成员安装的（${members.length}）` : '成员安装的',
      children: uploaded((data) => (
        <MemberPlugins
          plugins={data.memberPlugins}
          settings={data.settings}
          loading={uploads.isFetching && !polling}
          cloudAvailable={data.cloudAvailable}
        />
      )),
    },
  ];
  return (
    <AdminPage
      title="插件"
      description="让这个空间里的智能体多出一些能力：随版本预置的、空间自己上传的、成员自己装的。"
    >
      <Tabs items={items} />
    </AdminPage>
  );
}
