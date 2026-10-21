/** 模型管理用到的查询。 */
import { useQuery } from '@tanstack/react-query';
import { listModelGroups, listModels, listProviders } from '../../../api/models';
import { KEYS } from '../queries';

export const useProviders = () => useQuery({ queryKey: KEYS.providers, queryFn: listProviders });
export const useModels = () => useQuery({ queryKey: KEYS.models, queryFn: listModels });
export const useModelGroups = () => useQuery({ queryKey: KEYS.modelGroups, queryFn: listModelGroups });
