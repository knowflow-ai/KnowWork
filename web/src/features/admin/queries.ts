/** 管理页共用的查询键与查询。 */
import { useQuery } from '@tanstack/react-query';
import { fetchOrgDepartmentMembers, fetchOrgDepartments, listMemberRules } from '../../api/org';
import { fetchDepartments, listGroups, listMembers } from '../../api/workspace';

export const KEYS = {
  members: ['admin', 'members'],
  departments: ['admin', 'departments'],
  groups: ['admin', 'groups'],
  grants: ['admin', 'grants'],
  providers: ['admin', 'model-providers'],
  models: ['admin', 'models'],
  usage: ['admin', 'usage'],
  sources: ['knowledge-sources'],
  knowledgeBases: ['knowledge-bases'],
  mcpServers: ['admin', 'mcp-servers'],
  channels: ['admin', 'channels'],
  orgDepartments: ['admin', 'org-departments'],
  memberRules: ['admin', 'member-rules'],
} as const;

export const useMembers = () => useQuery({ queryKey: KEYS.members, queryFn: listMembers });
export const useDepartments = () => useQuery({ queryKey: KEYS.departments, queryFn: fetchDepartments });
export const useGroups = () => useQuery({ queryKey: KEYS.groups, queryFn: listGroups });
/** 组织架构（目录同步来的部门）；没接目录时为空，相关界面不出现。 */
export const useOrgDepartments = () =>
  useQuery({ queryKey: KEYS.orgDepartments, queryFn: fetchOrgDepartments });
export const useMemberRules = () => useQuery({ queryKey: KEYS.memberRules, queryFn: listMemberRules });
/** 组织架构里一个部门的人：选中了才取。 */
export const useOrgDepartmentMembers = (id: string | undefined) =>
  useQuery({
    queryKey: [...KEYS.orgDepartments, id, 'members'],
    queryFn: () => fetchOrgDepartmentMembers(id!),
    enabled: Boolean(id),
  });
