/** 资源授权：谁能以哪一档权限使用哪些技能、工具、知识库与模型分组。不含空间角色自带的默认权限。 */
import { useMemo, useState } from 'react';
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { listKnowledgeBases } from '../../../api/knowledge';
import { listServers } from '../../../api/mcp';
import { listModelGroups } from '../../../api/models';
import { listGrants, revokeGrant, type Grant } from '../../../api/workspace';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { includesText } from '../table';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { KEYS, useDepartments, useGroups, useMembers } from '../queries';
import { GrantDialog } from './GrantDialog';
import { actionLabel, resourceLabel, subjectLabel, type Directory } from './labels';

function useDirectory(): Directory {
  const members = useMembers();
  const groups = useGroups();
  const departments = useDepartments();
  const knowledge = useQuery({ queryKey: KEYS.knowledgeBases, queryFn: listKnowledgeBases });
  const modelGroups = useQuery({ queryKey: KEYS.modelGroups, queryFn: listModelGroups });
  const mcpServers = useQuery({ queryKey: KEYS.mcpServers, queryFn: listServers });
  return useMemo(() => {
    const resources: Record<string, string> = {};
    // 知识库：整个来源（前缀通配，覆盖它以后新开放的知识库）与单个知识库
    for (const s of knowledge.data ?? []) {
      resources[`knowledge_base:${s.sourceId}.*`] = `${s.sourceName} · 全部知识库`;
      for (const b of s.bases) resources[`knowledge_base:${b.id}`] = `${s.sourceName} · ${b.name}`;
    }
    for (const g of modelGroups.data ?? []) resources[`model_group:${g.id}`] = g.name;
    // MCP 工具：按服务授权（前缀通配，覆盖它以后新增的工具）排在前面，再列单个工具
    for (const server of mcpServers.data ?? []) {
      resources[`tool:${server.grantPattern}`] = `${server.title}（全部工具）`;
      for (const tool of server.tools) resources[`tool:${tool.name}`] = `${server.title} · ${tool.rawName}`;
    }
    return {
      members: members.data ?? [],
      groups: groups.data ?? [],
      departments: departments.data ?? [],
      resources,
      knowledge: knowledge.data ?? [],
    };
  }, [members.data, groups.data, departments.data, knowledge.data, modelGroups.data, mcpServers.data]);
}

/** 类型用灰色小标签，名称跟在后面。 */
function Labeled({ kind, name }: { readonly kind: string; readonly name: string }) {
  return (
    <span className="kw-labeled">
      <Badge color="secondary" variant="soft" size="sm">
        {kind}
      </Badge>
      <span>{name}</span>
    </span>
  );
}

export default function GrantsPage() {
  const grants = useQuery({ queryKey: KEYS.grants, queryFn: listGrants });
  const dir = useDirectory();
  const [keyword, setKeyword] = useState('');
  const [adding, setAdding] = useState(false);
  const revoke = useAction((g: Grant) => revokeGrant(g.subject, g.resource), {
    invalidate: [KEYS.grants],
    success: '已撤销授权',
    failure: '撤销没有成功，请稍后重试',
  });
  const subjectOf = (g: Grant) => subjectLabel(g.subject, dir);
  const resourceOf = (g: Grant) => {
    const r = resourceLabel(g.resource, dir);
    return { kind: r.type, name: r.name };
  };
  const rows = useMemo(
    () =>
      (grants.data ?? []).filter((g) => {
        const s = subjectLabel(g.subject, dir);
        const r = resourceLabel(g.resource, dir);
        return includesText([s.kind, s.name, r.type, r.name, actionLabel(g.action)], keyword);
      }),
    [grants.data, dir, keyword],
  );

  const columns: readonly Column<Grant>[] = [
    {
      key: 'subject',
      title: '授权对象',
      ellipsis: true,
      render: (g) => <Labeled {...subjectOf(g)} />,
    },
    {
      key: 'resource',
      title: '资源',
      ellipsis: true,
      render: (g) => <Labeled {...resourceOf(g)} />,
    },
    { key: 'action', title: '权限', width: 110, render: (g) => actionLabel(g.action) },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      render: (g) => (
        <RowActions
          actions={[
            {
              key: 'revoke',
              label: '撤销',
              danger: true,
              confirm: '撤销这条授权？对方将立即失去对应权限。',
              onClick: () => void revoke.run(g),
            },
          ]}
        />
      ),
    },
  ];

  return (
    <AdminPage
      title="资源授权"
      description="把技能、工具、知识库与模型分组授权给成员、部门、用户组或角色。空间角色自带的默认权限不在这里列出。"
    >
      <Toolbar
        left={<SearchInput placeholder="搜索对象、资源或权限" value={keyword} onChange={setKeyword} />}
        right={
          <Button color="primary" onClick={() => setAdding(true)}>
            <Plus />
            新增授权
          </Button>
        }
      />
      {grants.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<Grant>
          rowKey={(g) => `${g.subject}|${g.resource}`}
          columns={columns}
          rows={rows}
          loading={grants.isFetching}
          empty={grants.isError ? '授权加载失败，请刷新重试' : keyword ? '没有找到匹配的授权' : '还没有授权'}
        />
      )}
      {adding ? <GrantDialog dir={dir} onClose={() => setAdding(false)} /> : null}
    </AdminPage>
  );
}
