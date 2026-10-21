/** 品牌与系统管理员判定的查询。品牌取不到时用默认的 KnowWork，界面不因此报错。 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { checkSystemAdmin, DEFAULT_BRAND, getBrand, type Brand } from '../../api/system';

export const BRAND_KEY = ['brand'] as const;
export const SYSTEM_ADMIN_KEY = ['system', 'check'] as const;

export function useBrand(): Brand {
  const query = useQuery({ queryKey: BRAND_KEY, queryFn: getBrand, staleTime: 5 * 60_000, retry: false });
  return query.data ?? DEFAULT_BRAND;
}

export function useIsSystemAdmin() {
  return useQuery({ queryKey: SYSTEM_ADMIN_KEY, queryFn: checkSystemAdmin, staleTime: 60_000, retry: false });
}

/** 浏览器标签页的标题与图标跟着品牌走；自定义了 Logo 就用它当 favicon。 */
export function useBrandHead(): void {
  const brand = useBrand();
  useEffect(() => {
    document.title = brand.name;
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!icon) return;
    icon.href = brand.logoUrl ?? '/favicon.svg';
    if (brand.logoUrl) icon.removeAttribute('type');
    else icon.type = 'image/svg+xml';
  }, [brand.name, brand.logoUrl]);
}
