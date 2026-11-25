/**
 * 空间插件：管理员上传的插件。传完默认关闭；定档（默认启用、可选）、选给谁用和适用于以后才对成员生效。
 * 上传的插件不在共享的智能体进程里跑：云端对话用每个人自己的隔离环境，本机对话装在他的桌面端。
 */
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import {
  deleteWorkspacePlugin,
  updateWorkspacePlugin,
  UPLOAD_MODES,
  uploadWorkspacePlugin,
  type PluginMode,
  type UploadedPlugin,
} from '../../../api/plugins';
import { currentWorkspaceId } from '../../../stores/workspace';
import { Table, type Column } from '../../../ui/Table';
import { toast } from '../../../ui/toastStore';
import { Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { UsersDialog } from '../grants/UsersSelect';
import { EVERYONE, usersText } from '../grants/users';
import { useAction } from '../useAction';
import { capabilitiesText, targetsText, trialFacts } from './labels';
import { PluginDetail } from './PluginDetail';
import { TrialNote } from './TrialNote';
import { uploadDetailView } from './detailView';
import { uploadsKey } from './queries';
import { UploadDialog } from './UploadDialog';

const audienceOf = (plugin: UploadedPlugin) => (plugin.narrowed ? plugin.subjects : [EVERYONE]);

export function WorkspacePlugins({
  plugins,
  loading,
  cloudAvailable,
}: {
  readonly plugins: readonly UploadedPlugin[];
  readonly loading: boolean;
  /** 部署开没开个人专属宿主；没开时上传的插件只能给本机对话用 */
  readonly cloudAvailable: boolean;
}) {
  const client = useQueryClient();
  const invalidate = [uploadsKey(currentWorkspaceId())];
  const [uploading, setUploading] = useState(false);
  const [sharing, setSharing] = useState<UploadedPlugin>();
  const [viewing, setViewing] = useState<UploadedPlugin>();
  const change = useAction(
    (input: { id: string; mode?: PluginMode; subjects?: readonly string[] }) =>
      updateWorkspacePlugin(input.id, { mode: input.mode, subjects: input.subjects }),
    { invalidate, success: '已保存，新开的对话生效', failure: '没能保存，请稍后重试' },
  );
  const remove = useAction((id: string) => deleteWorkspacePlugin(id), {
    invalidate,
    success: '已删除',
    failure: '没能删除，请稍后重试',
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
    { key: 'capabilities', title: '用到的能力', width: 220, render: (p) => capabilitiesText(p.capabilities) },
    { key: 'targets', title: '适用于', width: 130, render: (p) => targetsText(p.targets) },
    { key: 'users', title: '给谁用', width: 110, render: (p) => usersText(audienceOf(p)) },
    {
      key: 'mode',
      title: '启用',
      width: 130,
      render: (p) => (
        <Select
          size="sm"
          aria-label={`${p.name}的启用方式`}
          options={UPLOAD_MODES.map((mode) => ({ ...mode }))}
          value={p.mode}
          onChange={(option) => void change.run({ id: p.id, mode: option.value as PluginMode })}
        />
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 190,
      render: (p) => (
        <RowActions
          actions={[
            { key: 'detail', label: '详情', onClick: () => setViewing(p) },
            { key: 'users', label: '给谁用', onClick: () => setSharing(p) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除「${p.name}」？用着它的成员新开的对话里就没有了`,
              onClick: () => void remove.run(p.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <>
      <Toolbar
        left={
          <span className="kw-secondary-text">
            {cloudAvailable
              ? '传完默认关闭，定了启用方式才生效。'
              : '传完默认关闭，定了启用方式才生效。这个部署没有开启云端的插件运行环境，上传的插件只在桌面端的本机对话里生效。'}
          </span>
        }
        right={
          <Button color="primary" onClick={() => setUploading(true)}>
            <Plus />
            上传插件
          </Button>
        }
      />
      <Table<UploadedPlugin>
        rowKey={(p) => p.id}
        columns={columns}
        rows={plugins}
        minWidth={980}
        loading={loading}
        empty="还没有空间插件。把内部做好的插件包传上来，授权给需要的人。"
      />
      {uploading ? (
        <UploadDialog
          title="上传空间插件"
          targets={cloudAvailable ? ['cloud', 'device'] : ['device']}
          onUpload={async (archive, targets) => {
            await uploadWorkspacePlugin(archive, targets);
            await client.invalidateQueries({ queryKey: invalidate[0] });
            toast.success(
              cloudAvailable
                ? '已上传，正在试装。通过并定了启用方式后对成员生效'
                : '已上传。定了启用方式后对成员生效',
            );
          }}
          onClose={() => setUploading(false)}
        />
      ) : null}
      {viewing ? (
        <PluginDetail
          view={uploadDetailView(
            viewing,
            [
              { key: 'source', label: '来源', value: '空间插件（管理员上传）' },
              { key: 'targets', label: '适用于', value: targetsText(viewing.targets) },
              { key: 'users', label: '给谁用', value: usersText(audienceOf(viewing)) },
              { key: 'capabilities', label: '用到的能力', value: capabilitiesText(viewing.capabilities) },
              ...trialFacts(viewing, cloudAvailable),
            ],
            true,
          )}
          onClose={() => setViewing(undefined)}
        />
      ) : null}
      {sharing ? (
        <UsersDialog
          title={`「${sharing.title || sharing.name}」给谁用`}
          initial={audienceOf(sharing)}
          pending={change.pending}
          onSave={(subjects) => change.run({ id: sharing.id, subjects })}
          onClose={() => setSharing(undefined)}
        />
      ) : null}
    </>
  );
}
