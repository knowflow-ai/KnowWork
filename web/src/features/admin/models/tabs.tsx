/** 模型管理的两个标签页：厂商、模型。 */
import { useState } from 'react';
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { readableError } from '../../../api/http';
import {
  capabilityLabel,
  deleteModel,
  deleteProvider,
  testModel,
  type Model,
  type Provider,
} from '../../../api/models';
import { Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { ModelDialog } from './ModelDialog';
import { ProviderDialog } from './ProviderDialog';
import { useModels, useProviders } from './queries';

const enabledBadge = (enabled: boolean) => (
  <StatusDot tone={enabled ? 'success' : 'default'}>{enabled ? '启用' : '停用'}</StatusDot>
);

function AddButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <Button color="primary" disabled={disabled} onClick={onClick}>
      <Plus />
      {label}
    </Button>
  );
}

type Editing<T> = { readonly item?: T } | null;

export function ProvidersTab() {
  const providers = useProviders();
  const [editing, setEditing] = useState<Editing<Provider>>(null);
  const remove = useAction((id: string) => deleteProvider(id), {
    invalidate: [KEYS.providers, KEYS.models],
    success: '已删除厂商',
    failure: '删除没有成功，请稍后重试',
  });
  const columns: readonly Column<Provider>[] = [
    { key: 'name', title: '名称', width: 160, ellipsis: true, render: (p) => p.name },
    { key: 'baseUrl', title: 'API 地址', ellipsis: true, render: (p) => p.baseUrl },
    {
      key: 'key',
      title: '密钥',
      width: 120,
      render: (p) => (p.apiKeyConfigured ? `已配置 ··${p.apiKeyLast4}` : '未配置'),
    },
    { key: 'enabled', title: '状态', width: 90, render: (p) => enabledBadge(p.enabled) },
    {
      key: 'actions',
      title: '操作',
      width: 130,
      render: (p) => (
        <RowActions
          actions={[
            { key: 'edit', label: '修改', onClick: () => setEditing({ item: p }) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除厂商「${p.name}」？还有模型挂在它下面时不能删除。`,
              onClick: () => void remove.run(p.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <>
      <Toolbar right={<AddButton label="添加厂商" onClick={() => setEditing({})} />} />
      {providers.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<Provider>
          rowKey={(p) => p.id}
          columns={columns}
          rows={providers.data ?? []}
          loading={providers.isFetching}
          empty={providers.isError ? '厂商加载失败，请刷新重试' : '还没有厂商'}
        />
      )}
      {editing ? <ProviderDialog provider={editing.item} onClose={() => setEditing(null)} /> : null}
    </>
  );
}

function useModelTest() {
  const [testing, setTesting] = useState<string | null>(null);
  const run = async (model: Model) => {
    setTesting(model.id);
    try {
      const result = await testModel(model.id);
      const latency = result.latencyMs !== null ? `，耗时 ${result.latencyMs} 毫秒` : '';
      if (result.ok) toast.success(`「${model.alias}」连接正常${latency}`);
      else toast.error(`「${model.alias}」不通：${result.message || '请检查地址、密钥与模型名'}`);
    } catch (error) {
      toast.error(readableError(error, '测试没有完成，请稍后重试'));
    } finally {
      setTesting(null);
    }
  };
  return { testing, run };
}

const price = (value: number | null, currency: string) => (value === null ? '—' : `${value} ${currency}`);

export function ModelsTab() {
  const models = useModels();
  const providers = useProviders();
  const [editing, setEditing] = useState<Editing<Model>>(null);
  const test = useModelTest();
  const remove = useAction((id: string) => deleteModel(id), {
    invalidate: [KEYS.models],
    success: '已删除模型',
    failure: '删除没有成功，请稍后重试',
  });
  const providerName = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? '—';
  const columns: readonly Column<Model>[] = [
    { key: 'alias', title: '别名', width: 150, ellipsis: true, render: (m) => m.alias },
    { key: 'name', title: '上游模型', width: 160, ellipsis: true, render: (m) => m.name },
    { key: 'provider', title: '厂商', width: 120, ellipsis: true, render: (m) => providerName(m.providerId) },
    {
      key: 'capabilities',
      title: '能力',
      width: 160,
      render: (m) => (
        <span className="kw-tags">
          {m.capabilities.map((c) => (
            <Badge key={c} color="secondary" variant="soft" size="sm">
              {capabilityLabel(c)}
            </Badge>
          ))}
        </span>
      ),
    },
    {
      key: 'context',
      title: '上下文',
      width: 100,
      align: 'right',
      render: (m) => (
        <span className="kw-num">
          {m.contextLength === null ? '—' : m.contextLength.toLocaleString('zh-CN')}
        </span>
      ),
    },
    {
      key: 'price',
      title: '输入 / 输出单价',
      width: 180,
      align: 'right',
      render: (m) => (
        <span className="kw-num">{`${price(m.inputPrice, m.currency)} / ${price(m.outputPrice, m.currency)}`}</span>
      ),
    },
    { key: 'enabled', title: '状态', width: 80, render: (m) => enabledBadge(m.enabled) },
    {
      key: 'actions',
      title: '操作',
      width: 170,
      render: (m) => (
        <RowActions
          actions={[
            {
              key: 'test',
              label: test.testing === m.id ? '测试中' : '测试',
              disabled: test.testing !== null,
              onClick: () => void test.run(m),
            },
            { key: 'edit', label: '修改', onClick: () => setEditing({ item: m }) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除模型「${m.alias}」？引用它的技能将不能再用它。`,
              onClick: () => void remove.run(m.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <>
      <Toolbar
        right={
          <AddButton
            label="添加模型"
            disabled={(providers.data ?? []).length === 0}
            onClick={() => setEditing({})}
          />
        }
      />
      {models.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<Model>
          rowKey={(m) => m.id}
          columns={columns}
          rows={models.data ?? []}
          minWidth={1100}
          loading={models.isFetching}
          empty={models.isError ? '模型加载失败，请刷新重试' : '还没有模型，先添加厂商再添加模型'}
        />
      )}
      {editing ? (
        <ModelDialog model={editing.item} providers={providers.data ?? []} onClose={() => setEditing(null)} />
      ) : null}
    </>
  );
}
