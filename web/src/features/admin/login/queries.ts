/** 登录方式用到的查询。 */
import { useQuery } from '@tanstack/react-query';
import { listLoginProviders } from '../../../api/loginProviders';

export const LOGIN_PROVIDERS_KEY = ['admin', 'login-providers'] as const;

export const useLoginProviders = () =>
  useQuery({ queryKey: LOGIN_PROVIDERS_KEY, queryFn: listLoginProviders });
