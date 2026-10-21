/** 知识源：增删改、连通测试。谁能查哪个知识库在「资源授权」里按知识库授权。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { readableError } from '../../../api/http';
import {
  AUTH_MODES,
  deleteSource,
  listSources,
  testSource,
  type KnowledgeSource,
} from '../../../api/knowledge';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { SourceDialog } from './SourceDialog';

function useProbe() {
  const [testing, setTesting] = useState<string | null>(null);
  const run = async (source: KnowledgeSource) => {
    setTesting(source.id);
    try {
      const result = await testSource(source.id);
      if (result.ok) toast.success(`「${source.name}」${result.message || '连接正常'}`);
      else toast.error(`「${source.name}」不通：${result.message || '请检查地址与密钥'}`);
    } catch (error) {
      toast.error(readableError(error, '测试没有完成，请稍后重试'));
    } finally {
      setTesting(null);
    }
  };
  return { testing, run };
}

export default function KnowledgePage() {
  const sources = useQuery({ queryKey: KEYS.sources, queryFn: listSources });
  const [editing, setEditing] = useState<{ readonly item?: KnowledgeSource } | null>(null);
  const probe = useProbe();
  const remove = useAction((id: string) => deleteSource(id), {
    invalidate: [KEYS.sources, KEYS.knowledgeBases, KEYS.grants],
    success: '已删除知识源',
    failure: '删除没有成功，请稍后重试',
  });

  const columns: readonly Column<KnowledgeSource>[] = [
    { key: 'name', title: '名称', width: 180, ellipsis: true, render: (s) => s.name },
    { key: 'url', title: '地址', ellipsis: true, render: (s) => s.detail?.baseUrl ?? '无权查看' },
    {
      key: 'authMode',
      title: '认证方式',
      width: 150,
      render: (s) => AUTH_MODES.find((a) => a.value === s.authMode)?.label ?? s.authMode,
    },
    {
      key: 'enabled',
      title: '状态',
      width: 90,
      render: (s) => (
        <StatusDot tone={s.enabled ? 'success' : 'default'}>{s.enabled ? '启用' : '停用'}</StatusDot>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 170,
      render: (s) =>
        s.detail ? (
          <RowActions
            actions={[
              {
                key: 'test',
                label: probe.testing === s.id ? '测试中' : '测试',
                disabled: probe.testing !== null,
                onClick: () => void probe.run(s),
              },
              { key: 'edit', label: '修改', onClick: () => setEditing({ item: s }) },
              {
                key: 'delete',
                label: '删除',
                danger: true,
                confirm: `删除知识源「${s.name}」？技能与对话将不能再检索它，它下面的知识库授权也一并删除。`,
                onClick: () => void remove.run(s.id),
              },
            ]}
          />
        ) : (
          <span className="kw-secondary">只能使用</span>
        ),
    },
  ];

  return (
    <AdminPage
      title="知识源"
      description="接入 KnowFlow 知识库。平台不自带知识库，检索与权限以 KnowFlow 为准。"
    >
      <Toolbar
        right={
          <Button color="primary" onClick={() => setEditing({})}>
            <Plus />
            添加知识源
          </Button>
        }
      />
      {sources.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<KnowledgeSource>
          rowKey={(s) => s.id}
          columns={columns}
          rows={sources.data ?? []}
          loading={sources.isFetching}
          empty={sources.isError ? '知识源加载失败，请刷新重试' : '还没有知识源'}
        />
      )}
      {editing ? <SourceDialog source={editing.item} onClose={() => setEditing(null)} /> : null}
    </AdminPage>
  );
}
