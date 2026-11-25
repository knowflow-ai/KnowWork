/**
 * 个人设置 → 我的插件：管理员给了我的（预置的、空间插件）和我自己装的个人插件，一张列表。
 * 管理员设为全员或默认启用的只显示、不能关；可选的和自己装的自己开关，只影响我自己的对话。
 * 空间允许时可以自己上传插件。改完以后新开的对话和下一次恢复的对话生效。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { readableError } from '../../api/http';
import {
  deleteMyPlugin,
  submitMyPlugin,
  listMyPlugins,
  listMyUploads,
  switchMyPlugin,
  switchMyUpload,
  uploadMyPlugin,
  type PluginTarget,
} from '../../api/plugins';
import { currentWorkspaceId } from '../../stores/workspace';
import { ConfirmPopover } from '../../ui/confirm';
import { SettingList, SettingRow } from '../../ui/SettingRow';
import { Skeleton } from '../../ui/Skeleton';
import { toast } from '../../ui/toastStore';
import {
  anyChecking,
  capabilitiesText,
  CHECKING_REFRESH_MS,
  targetsText,
  trialFacts,
  type Trialed,
} from '../admin/plugins/labels';
import { PluginDetail } from '../admin/plugins/PluginDetail';
import { builtinDetailView, uploadDetailView, type PluginDetailView } from '../admin/plugins/detailView';
import { myBuiltinKey, myUploadsKey } from '../admin/plugins/queries';
import { TrialNote } from '../admin/plugins/TrialNote';
import { UploadDialog } from '../admin/plugins/UploadDialog';

/** 列表里的一行：预置的和上传的归成同一种 */
interface Row {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly enabled: boolean;
  /** 开关不能动的原因；没有就是能动 */
  readonly fixed?: string;
  readonly personal: boolean;
  /** 自己装的：已经提交给管理员 */
  readonly submitted: boolean;
  readonly toggle: (enabled: boolean) => Promise<void>;
  readonly view: PluginDetailView;
  /** 上传的插件的试装结果（名字下面那一行） */
  readonly trial?: Trialed;
}

const KIND = { builtin: '预置', workspace: '空间', personal: '个人' } as const;

function PluginRow({ row, onChanged }: { readonly row: Row; readonly onChanged: () => Promise<unknown> }) {
  const [viewing, setViewing] = useState(false);
  const toggle = useMutation({
    mutationFn: row.toggle,
    onSuccess: async (_, enabled) => {
      await onChanged();
      toast.success(enabled ? '已开启，新开的对话生效' : '已关闭，新开的对话生效');
    },
    onError: (error) => toast.error(readableError(error, '没能保存，请稍后重试')),
  });
  const remove = useMutation({
    mutationFn: () => deleteMyPlugin(row.id),
    onSuccess: async () => {
      await onChanged();
      toast.success('已删除');
    },
    onError: (error) => toast.error(readableError(error, '没能删除，请稍后重试')),
  });
  const submit = useMutation({
    mutationFn: () => submitMyPlugin(row.id),
    onSuccess: async () => {
      await onChanged();
      toast.success('已提交。管理员通过后会变成空间插件，可以授权给别人');
    },
    onError: (error) => toast.error(readableError(error, '没能提交，请稍后重试')),
  });
  return (
    <SettingRow
      title={row.name}
      description={
        row.trial && row.trial.status !== 'ready' ? (
          <>
            {row.description}
            <br />
            <TrialNote plugin={row.trial} />
          </>
        ) : (
          row.description
        )
      }
    >
      {(id) => (
        <span className="kw-row-actions">
          <Button color="secondary" variant="ghost" size="sm" onClick={() => setViewing(true)}>
            详情
          </Button>
          {viewing ? <PluginDetail view={row.view} onClose={() => setViewing(false)} /> : null}
          {row.personal ? (
            <Button
              color="secondary"
              variant="ghost"
              size="sm"
              disabled={row.submitted || submit.isPending}
              onClick={() => submit.mutate()}
            >
              {row.submitted ? '已提交' : '提交给管理员'}
            </Button>
          ) : null}
          {row.personal ? (
            <ConfirmPopover
              title={`删除「${row.name}」？`}
              okText="删除"
              danger
              onConfirm={() => remove.mutate()}
            >
              <Button color="danger" variant="ghost" size="sm" disabled={remove.isPending}>
                删除
              </Button>
            </ConfirmPopover>
          ) : null}
          <Switch
            id={id}
            checked={row.enabled}
            disabled={row.fixed !== undefined || toggle.isPending}
            onCheckedChange={(enabled) => toggle.mutate(enabled)}
          />
        </span>
      )}
    </SettingRow>
  );
}

const sentence = (...parts: readonly (string | undefined)[]) => parts.filter(Boolean).join(' · ');

export function MyPluginsTab() {
  const client = useQueryClient();
  const workspaceId = currentWorkspaceId();
  const builtin = useQuery({ queryKey: myBuiltinKey(workspaceId), queryFn: listMyPlugins });
  const uploads = useQuery({
    queryKey: myUploadsKey(workspaceId),
    queryFn: listMyUploads,
    // 自己刚传的还在试装：隔一会儿刷新，直到出结果
    refetchInterval: (query) => (anyChecking(query.state.data?.plugins) ? CHECKING_REFRESH_MS : false),
  });
  const [uploading, setUploading] = useState(false);
  if (builtin.isPending || uploads.isPending) return <Skeleton rows={3} />;
  if (builtin.isError || uploads.isError)
    return (
      <p className="kw-secondary-text">
        {readableError(builtin.error ?? uploads.error, '插件没有加载出来，请稍后刷新')}
      </p>
    );
  const refresh = () => client.invalidateQueries({ queryKey: myBuiltinKey(workspaceId) });
  const rows: readonly Row[] = [
    ...builtin.data.map((p) => ({
      id: p.key,
      name: p.name,
      description: sentence(KIND.builtin, p.locked ? '管理员已为全员启用' : undefined, p.description),
      enabled: p.enabled,
      fixed: p.locked ? 'locked' : undefined,
      personal: false,
      submitted: false,
      toggle: (enabled: boolean) => switchMyPlugin(p.key, enabled),
      view: builtinDetailView(p, [
        { key: 'state', label: '启用', value: p.locked ? '管理员已为全员启用' : '可选，自己决定开不开' },
      ]),
    })),
    ...uploads.data.plugins.map((p) => ({
      id: p.id,
      // 和管理页的表一样带版本：自己装的会一版一版地再传
      name: p.version ? `${p.title || p.name} ${p.version}` : p.title || p.name,
      description: sentence(
        KIND[p.scope],
        p.blocked ? '管理员已禁用，开着也不生效' : p.locked ? '管理员已设为默认启用' : undefined,
        `适用于${targetsText(p.targets)}`,
        p.description,
      ),
      enabled: p.enabled && !p.blocked,
      // 试装中、没通过的不生效，开关先不让动
      fixed: p.blocked ? 'blocked' : p.locked ? 'locked' : p.status !== 'ready' ? 'trial' : undefined,
      trial: p,
      personal: p.scope === 'personal',
      submitted: p.submitted,
      toggle: (enabled: boolean) => switchMyUpload(p.id, enabled),
      view: uploadDetailView(
        p,
        [
          {
            key: 'source',
            label: '来源',
            value: p.scope === 'personal' ? '个人插件（你自己装的，只有你能用）' : '空间插件（管理员给你的）',
          },
          { key: 'targets', label: '适用于', value: targetsText(p.targets) },
          { key: 'capabilities', label: '用到的能力', value: capabilitiesText(p.capabilities) },
          ...trialFacts(p, uploads.data.cloudAvailable),
        ],
        false,
      ),
    })),
  ];
  // 能装到哪：云端要部署开了插件运行环境，本机要空间允许；两个都不行就没有上传入口
  const offered: readonly PluginTarget[] = [
    ...(uploads.data.cloudAvailable ? (['cloud'] as const) : []),
    ...(uploads.data.canInstallDevice ? (['device'] as const) : []),
  ];
  const canInstall = uploads.data.canInstall && offered.length > 0;
  return (
    <>
      <div className="kw-my-plugins-bar">
        <span className="kw-secondary-text">
          {canInstall
            ? '管理员给你的插件，和你自己装的。自己装的只有你能用。'
            : '管理员给你的插件。这个空间没有开放成员自己安装。'}
        </span>
        {canInstall ? (
          <Button color="secondary" variant="outline" onClick={() => setUploading(true)}>
            <Plus />
            上传插件
          </Button>
        ) : null}
      </div>
      {rows.length === 0 ? (
        <p className="kw-secondary-text">管理员还没有给这个空间启用插件。</p>
      ) : (
        <SettingList>
          {rows.map((row) => (
            <PluginRow key={row.id} row={row} onChanged={refresh} />
          ))}
        </SettingList>
      )}
      {uploading ? (
        <UploadDialog
          title="上传个人插件"
          targets={offered}
          onUpload={async (archive, targets) => {
            await uploadMyPlugin(archive, targets);
            await refresh();
            toast.success(
              uploads.data.cloudAvailable
                ? '已上传，正在试装。通过以后新开的对话生效'
                : '已上传并开启，新开的对话生效',
            );
          }}
          onClose={() => setUploading(false)}
        />
      ) : null}
    </>
  );
}
