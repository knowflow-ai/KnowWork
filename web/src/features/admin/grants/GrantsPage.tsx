/** 资源授权：谁能以哪一档权限使用哪些技能、工具与知识库。不含空间角色自带的默认权限；模型不授权，空间成员都能用。 */
import { useMemo, useState, type ReactNode } from 'react';
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQueries, useQuery } from '@tanstack/react-query';
import { listKnowledgeBases } from '../../../api/knowledge';
import { listServers } from '../../../api/mcp';
import { listSkills } from '../../../api/skills';
import { listGrants, RESOURCE_TYPES, revokeGrant, type Grant } from '../../../api/workspace';
import { slotItems, type GrantTreeNode } from '../../../slots/registry';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { includesText } from '../table';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { KEYS, useDepartments, useGroups, useMembers } from '../queries';
import { SKILL_KEYS } from '../skills/queries';
import { GrantDialog } from './GrantDialog';
import { actionLabel, resourceLabel, subjectLabel, type Directory } from './labels';
import { resourceIcon } from './icons';

/** 扩展给的树按知识源收好；函数在模块里定义，React Query 只在查询结果变了时重算，引用稳定。 */
function treesOf(
  results: readonly { readonly data?: readonly [string, readonly GrantTreeNode[]] }[],
): Readonly<Record<string, readonly GrantTreeNode[]>> {
  return Object.fromEntries(results.flatMap((result) => (result.data ? [result.data] : [])));
}

/** 树里每个节点的名字用「知识源 · 完整路径」，同名目录、同名知识库在列表里也分得清。 */
function treeNames(
  source: string,
  nodes: readonly GrantTreeNode[],
  path: string,
  into: Record<string, string>,
) {
  for (const node of nodes) {
    const here = path ? `${path} / ${node.name}` : node.name;
    into[node.resource] = `${source} · ${here}`;
    treeNames(source, node.children, here, into);
  }
}

function useDirectory(): Directory {
  const members = useMembers();
  const groups = useGroups();
  const departments = useDepartments();
  const knowledge = useQuery({ queryKey: KEYS.knowledgeBases, queryFn: listKnowledgeBases });
  const mcpServers = useQuery({ queryKey: KEYS.mcpServers, queryFn: listServers });
  // 技能授权记的是技能的内部 id：按名称列出来选，已有授权也显示名称
  const skills = useQuery({ queryKey: SKILL_KEYS.list, queryFn: () => listSkills('workspace') });
  // 扩展给的知识源层级树（插槽 grants.knowledge，例如目录 → 知识库）：各取一次
  const extras = slotItems('grants.knowledge');
  const trees = useQueries({
    queries: extras.map((extra) => ({
      queryKey: [...KEYS.grants, 'tree', extra.sourceId],
      queryFn: async () => [extra.sourceId, await extra.load()] as const,
    })),
    combine: treesOf,
  });
  return useMemo(() => {
    const resources: Record<string, string> = {};
    // 知识库：整个来源（前缀通配，覆盖它以后新开放的知识库）与单个知识库
    for (const s of knowledge.data ?? []) {
      resources[`knowledge_base:${s.sourceId}.*`] = `${s.sourceName} · 全部知识库`;
      for (const b of s.bases) resources[`knowledge_base:${b.id}`] = `${s.sourceName} · ${b.name}`;
    }
    for (const s of skills.data ?? []) resources[`skill:${s.id}`] = s.name;
    // MCP 工具：按服务授权（前缀通配，覆盖它以后新增的工具）排在前面，再列单个工具
    for (const server of mcpServers.data ?? []) {
      resources[`tool:${server.grantPattern}`] = `${server.title}（全部工具）`;
      for (const tool of server.tools) resources[`tool:${tool.name}`] = `${server.title} · ${tool.rawName}`;
    }
    for (const s of knowledge.data ?? []) treeNames(s.sourceName, trees[s.sourceId] ?? [], '', resources);
    return {
      members: members.data ?? [],
      groups: groups.data ?? [],
      departments: departments.data ?? [],
      resources,
      knowledge: knowledge.data ?? [],
      tools: mcpServers.data ?? [],
      typeLabels: Object.fromEntries([
        ...RESOURCE_TYPES.map((t) => [t.value, t.label] as const),
        ...extras.flatMap((extra) => Object.entries(extra.types)),
      ]),
      trees,
    };
  }, [
    members.data,
    groups.data,
    departments.data,
    knowledge.data,
    mcpServers.data,
    skills.data,
    extras,
    trees,
  ]);
}

/** 类型用灰色小标签，名称跟在后面。 */
function Labeled({
  kind,
  name,
  icon,
}: {
  readonly kind: string;
  readonly name: string;
  readonly icon?: ReactNode;
}) {
  return (
    <span className="kw-labeled">
      <Badge color="secondary" variant="soft" size="sm">
        {kind}
      </Badge>
      {icon ? (
        <span className="kw-labeled-icon" aria-hidden>
          {icon}
        </span>
      ) : null}
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
    return { kind: r.type, name: r.name, icon: resourceIcon(g.resource) };
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
      description="把技能、工具与知识库授权给成员、部门、用户组或角色。空间角色自带的默认权限不在这里列出。"
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
