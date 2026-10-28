/** 管理页共用的查询键与查询。 */
import { useQuery } from '@tanstack/react-query';
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
} as const;

export const useMembers = () => useQuery({ queryKey: KEYS.members, queryFn: listMembers });
export const useDepartments = () => useQuery({ queryKey: KEYS.departments, queryFn: fetchDepartments });
export const useGroups = () => useQuery({ queryKey: KEYS.groups, queryFn: listGroups });
