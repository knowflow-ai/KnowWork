/**
 * 成员安装的个人插件与管控开关：允许成员安装个人插件、允许给本机对话装、禁用清单（按包名，成员装了也不生效）。
 * 个人插件只有本人用，管理员不替他开关；不想让某个插件生效就把包名加进禁用清单。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import {
  adoptMemberPlugin,
  savePluginSettings,
  type PluginSettings,
  type UploadedPlugin,
} from '../../../api/plugins';
import { memberLabel } from '../../../api/workspace';
import { currentWorkspaceId } from '../../../stores/workspace';
import { SettingList, SettingRow } from '../../../ui/SettingRow';
import { Table, type Column } from '../../../ui/Table';
import { RowActions } from '../common';
import { useMembers } from '../queries';
import { useAction } from '../useAction';
import { capabilitiesText, targetsText, trialFacts } from './labels';
import { PluginDetail } from './PluginDetail';
import { TrialNote } from './TrialNote';
import { uploadDetailView } from './detailView';
import { uploadsKey } from './queries';

export function MemberPlugins({
  plugins,
  settings,
  loading,
  cloudAvailable,
}: {
  readonly plugins: readonly UploadedPlugin[];
  readonly settings: PluginSettings;
  readonly loading: boolean;
  /** 部署开没开个人专属宿主；没开时服务端不试装 */
  readonly cloudAvailable: boolean;
}) {
  const members = useMembers();
  const save = useAction((next: PluginSettings) => savePluginSettings(next), {
    invalidate: [uploadsKey(currentWorkspaceId())],
    success: '已保存',
    failure: '没能保存，请稍后重试',
  });
  const adopt = useAction((id: string) => adoptMemberPlugin(id), {
    invalidate: [uploadsKey(currentWorkspaceId())],
    success: '已转成空间插件。到「空间插件」里定启用方式和给谁用',
    failure: '没能转，请稍后重试',
  });
  const [confirming, setConfirming] = useState(false);
  const [viewing, setViewing] = useState<UploadedPlugin>();
  const nameOf = (userId: string) => {
    const member = members.data?.find((m) => m.userId === userId);
    return member ? memberLabel(member) : userId;
  };
  const block = (name: string, on: boolean) =>
    save.run({
      ...settings,
      blocked: on
        ? [...settings.blocked.filter((n) => n !== name), name]
        : settings.blocked.filter((n) => n !== name),
    });
  const columns: readonly Column<UploadedPlugin>[] = [
    {
      key: 'name',
      title: '插件',
      render: (p) => (
        <div className="kw-plugin-name">
          <span>
            {p.title || p.name} <span className="kw-secondary">{p.version}</span>
          </span>
          {p.description ? <span className="kw-secondary">{p.description}</span> : null}
          <TrialNote plugin={p} />
        </div>
      ),
    },
    { key: 'owner', title: '谁装的', width: 160, ellipsis: true, render: (p) => nameOf(p.owner) },
    { key: 'capabilities', title: '用到的能力', width: 240, render: (p) => capabilitiesText(p.capabilities) },
    { key: 'targets', title: '适用于', width: 130, render: (p) => targetsText(p.targets) },
    {
      key: 'state',
      title: '状态',
      width: 90,
      render: (p) =>
        p.blocked ? <span className="kw-secondary-text">已禁用</span> : p.submitted ? '已提交给你' : '可用',
    },
    {
      key: 'actions',
      title: '操作',
      width: 250,
      render: (p) => (
        <RowActions
          actions={[
            { key: 'detail', label: '详情', onClick: () => setViewing(p) },
            {
              key: 'adopt',
              label: '转成空间插件',
              confirm: `把「${p.name}」转成空间插件？转完默认关闭，再定给谁用`,
              disabled: adopt.pending,
              onClick: () => void adopt.run(p.id),
            },
            p.blocked
              ? { key: 'unblock', label: '解除禁用', onClick: () => void block(p.name, false) }
              : {
                  key: 'block',
                  label: '禁用',
                  danger: true,
                  confirm: `禁用「${p.name}」？所有成员装的这个插件都不再生效`,
                  onClick: () => void block(p.name, true),
                },
          ]}
        />
      ),
    },
  ];
  return (
    <>
      {viewing ? (
        <PluginDetail
          view={uploadDetailView(
            viewing,
            [
              { key: 'source', label: '来源', value: `个人插件（${nameOf(viewing.owner)} 装的）` },
              { key: 'targets', label: '适用于', value: targetsText(viewing.targets) },
              { key: 'capabilities', label: '用到的能力', value: capabilitiesText(viewing.capabilities) },
              ...trialFacts(viewing, cloudAvailable),
            ],
            true,
          )}
          onClose={() => setViewing(undefined)}
        />
      ) : null}
      <SettingList className="kw-plugin-settings">
        <SettingRow
          title="允许成员安装个人插件"
          description="成员在个人设置的「我的插件」里上传，只有本人用。关掉后已装的也不再生效，成员只能开关管理员给的。"
        >
          {(id) => (
            <Switch
              id={id}
              checked={settings.allowPersonal}
              disabled={save.pending}
              onCheckedChange={(on) => void save.run({ ...settings, allowPersonal: on })}
            />
          )}
        </SettingRow>
        <SettingRow
          title="允许给本机对话装个人插件"
          description="装到桌面端的插件在员工自己的电脑上运行，能读写本机文件、执行命令，没有容器隔离。"
        >
          {(id) => (
            <Switch
              id={id}
              checked={settings.allowPersonal && settings.allowPersonalDevice}
              disabled={save.pending || !settings.allowPersonal}
              onCheckedChange={(on) => void save.run({ ...settings, allowPersonalDevice: on })}
            />
          )}
        </SettingRow>
      </SettingList>
      <Table<UploadedPlugin>
        rowKey={(p) => p.id}
        columns={columns}
        rows={plugins}
        minWidth={900}
        loading={loading}
        empty="还没有成员安装个人插件"
      />
      {settings.blocked.length > 0 ? (
        <p className="kw-detail-note">
          禁用清单：{settings.blocked.join('、')}
          {confirming ? (
            <>
              {' '}
              <Button
                color="danger"
                variant="ghost"
                size="sm"
                onClick={() => void save.run({ ...settings, blocked: [] }).then(() => setConfirming(false))}
              >
                确认清空
              </Button>
            </>
          ) : (
            <>
              {' '}
              <Button color="secondary" variant="ghost" size="sm" onClick={() => setConfirming(true)}>
                清空
              </Button>
            </>
          )}
        </p>
      ) : null}
    </>
  );
}
