/**
 * 授权要用的全部名录：成员、用户组、部门、组织架构，按知识源分组的知识库（连同扩展给的目录树），技能、MCP 服务与工具，
 * 扩展登记的资源（例如数据源），以及「类型:id → 名称」。授权页、给专家选能查的知识库与数据源都用这一份，选的东西和
 * 显示的名字在各页一致。
 */
import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { listKnowledgeBases } from '../../../api/knowledge';
import { listServers } from '../../../api/mcp';
import { listSkills } from '../../../api/skills';
import { RESOURCE_TYPES } from '../../../api/workspace';
import { slotItems, type GrantTreeNode } from '../../../slots/registry';
import { KEYS, useDepartments, useGroups, useMembers, useOrgDepartments } from '../queries';
import { SOURCE_TEXT } from '../skills/here';
import { SKILL_KEYS, useUpperSkills } from '../skills/queries';
import { useEnterpriseHere } from '../tools/queries';
import type { Directory } from './labels';

/** 扩展给的树按知识源收好；函数在模块里定义，React Query 只在查询结果变了时重算，引用稳定。 */
function treesOf(
  results: readonly { readonly data?: readonly [string, readonly GrantTreeNode[]] }[],
): Readonly<Record<string, readonly GrantTreeNode[]>> {
  return Object.fromEntries(results.flatMap((result) => (result.data ? [result.data] : [])));
}

/** 扩展登记的资源按类型收好；同 treesOf，结果不变时引用稳定。 */
function resourcesOf(
  results: readonly {
    readonly data?: readonly [string, readonly { readonly id: string; readonly name: string }[]];
  }[],
): readonly (readonly [string, readonly { readonly id: string; readonly name: string }[]])[] {
  return results.flatMap((result) => (result.data ? [result.data] : []));
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

export function useDirectory(): Directory {
  const members = useMembers();
  const groups = useGroups();
  const departments = useDepartments();
  const orgDepartments = useOrgDepartments();
  const knowledge = useQuery({ queryKey: KEYS.knowledgeBases, queryFn: listKnowledgeBases });
  const mcpServers = useQuery({ queryKey: KEYS.mcpServers, queryFn: listServers });
  // 技能授权记的是技能的内部 id：按名称列出来选，已有授权也显示名称
  const skills = useQuery({ queryKey: SKILL_KEYS.list, queryFn: () => listSkills('workspace') });
  // 企业技能、内置技能在本空间收窄给谁用时，授权挂在它们自己的资源上
  const upper = useUpperSkills();
  const enterpriseMcp = useEnterpriseHere();
  // 扩展给的知识源层级树（插槽 grants.knowledge，例如目录 → 知识库）：各取一次
  const extras = slotItems('grants.knowledge');
  const trees = useQueries({
    queries: extras.map((extra) => ({
      queryKey: [...KEYS.grants, 'tree', extra.sourceId],
      queryFn: async () => [extra.sourceId, await extra.load()] as const,
    })),
    combine: treesOf,
  });
  // 扩展登记的资源类型（插槽 grants.resources，例如数据源）：各取一次可授权的资源
  const kinds = slotItems('grants.resources');
  const extraResources = useQueries({
    queries: kinds.map((kind) => ({
      queryKey: [...KEYS.grants, 'resources', kind.type],
      queryFn: async () => [kind.type, await kind.load()] as const,
    })),
    combine: resourcesOf,
  });
  return useMemo(() => {
    const resources: Record<string, string> = {};
    // 知识库：整个来源（前缀通配，覆盖它以后新开放的知识库）与单个知识库
    for (const s of knowledge.data ?? []) {
      resources[`knowledge_base:${s.sourceId}.*`] = `${s.sourceName} · 全部知识库`;
      for (const b of s.bases) resources[`knowledge_base:${b.id}`] = `${s.sourceName} · ${b.name}`;
    }
    for (const s of skills.data ?? []) resources[`skill:${s.id}`] = s.name;
    for (const s of upper.data ?? []) resources[s.resource] = `${s.name}（${SOURCE_TEXT[s.source]}）`;
    for (const s of enterpriseMcp.data ?? []) resources[s.resource] = `${s.title}（企业，全部工具）`;
    // MCP 工具：按服务授权（前缀通配，覆盖它以后新增的工具）排在前面，再列单个工具
    for (const server of mcpServers.data ?? []) {
      resources[`tool:${server.grantPattern}`] = `${server.title}（全部工具）`;
      for (const tool of server.tools) resources[`tool:${tool.name}`] = `${server.title} · ${tool.rawName}`;
    }
    for (const s of knowledge.data ?? []) treeNames(s.sourceName, trees[s.sourceId] ?? [], '', resources);
    for (const [type, items] of extraResources)
      for (const item of items) resources[`${type}:${item.id}`] = item.name;
    return {
      members: members.data ?? [],
      groups: groups.data ?? [],
      departments: departments.data ?? [],
      orgDepartments: orgDepartments.data ?? [],
      orgLoaded: orgDepartments.isSuccess,
      resources,
      knowledge: knowledge.data ?? [],
      knowledgeLoaded: knowledge.isSuccess,
      tools: mcpServers.data ?? [],
      typeLabels: Object.fromEntries([
        ...RESOURCE_TYPES.map((t) => [t.value, t.label] as const),
        ...extras.flatMap((extra) => Object.entries(extra.types)),
        ...kinds.map((kind) => [kind.type, kind.label] as const),
      ]),
      resourceTypes: [
        ...RESOURCE_TYPES.map((t) => ({ value: t.value, label: t.label })),
        ...kinds.map((kind) => ({
          value: kind.type,
          label: kind.label,
          help: kind.help,
          actions: kind.actions,
        })),
      ],
      trees,
    };
  }, [
    members.data,
    groups.data,
    departments.data,
    orgDepartments.data,
    orgDepartments.isSuccess,
    knowledge.data,
    knowledge.isSuccess,
    mcpServers.data,
    skills.data,
    upper.data,
    enterpriseMcp.data,
    extras,
    trees,
    kinds,
    extraResources,
  ]);
}
