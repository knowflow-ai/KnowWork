/**
 * 系统设置接口（server/src/knowwork_server/system/api.py）：品牌（名称、Logo）与系统信息。
 * 品牌不用登录就能取（登录页也要显示）；修改与系统信息只给系统管理员。
 */
import { request } from './http';
import { isRecord, str } from './parse';

export interface Brand {
  readonly name: string;
  /** 自定义 Logo 的地址；为空用默认的 KnowWork 叶子 K。 */
  readonly logoUrl: string | null;
}

export const DEFAULT_BRAND: Brand = { name: 'KnowWork', logoUrl: null };
/**
 * 默认素材在 web/public/brand（叶子 K，同 KnowFlow 商标图的做法）：
 * - knowwork-mark.svg：四周留白的方形图标，自定义名称但没传 Logo 时用，也是 favicon；
 * - knowwork-k.svg：贴边的叶子 K，默认字样里直接当「Know」的首字母；
 * - knowwork-logo.svg：转成路径的横版，给文档、README 用；界面里的字样用网页文字渲染，小字号更清楚。
 */
export const DEFAULT_MARK = '/brand/knowwork-mark.svg';
export const DEFAULT_LETTER_K = '/brand/knowwork-k.svg';

function parseBrand(value: unknown): Brand {
  if (!isRecord(value)) return DEFAULT_BRAND;
  const logoUrl = str(value.logo_url);
  return {
    name: str(value.name)?.trim() || DEFAULT_BRAND.name,
    // 只认本站的 Logo 地址，免得被塞进外链
    logoUrl: logoUrl?.startsWith('/api/brand/logo') ? logoUrl : null,
  };
}

export async function getBrand(): Promise<Brand> {
  return parseBrand(await request<unknown>('/api/brand', { quietUnauthorized: true }));
}

export async function checkSystemAdmin(): Promise<boolean> {
  const body = await request<unknown>('/api/system/check');
  return isRecord(body) && body.allowed === true;
}

export interface ExtensionInfo {
  readonly name: string;
  readonly enabled: boolean;
  /** 没启用的原因 */
  readonly reason: string | null;
}

export interface SystemInfo {
  readonly knowwork: string;
  readonly dsh: string | null;
  readonly database: string | null;
  /** 授权状态，一句话 */
  readonly license: string | null;
  /** 装了的扩展模块；开源版为空 */
  readonly extensions: readonly ExtensionInfo[];
}

function extensionOf(value: unknown): ExtensionInfo | undefined {
  if (!isRecord(value)) return undefined;
  const name = str(value.name);
  if (!name) return undefined;
  return { name, enabled: value.enabled === true, reason: str(value.reason) ?? null };
}

export async function getSystemInfo(): Promise<SystemInfo> {
  const body = await request<unknown>('/api/system/info');
  const record = isRecord(body) ? body : {};
  const extensions = Array.isArray(record.extensions) ? record.extensions : [];
  return {
    knowwork: str(record.knowwork) ?? '未知',
    dsh: str(record.dsh) ?? null,
    database: str(record.database) ?? null,
    license: str(record.license) ?? null,
    extensions: extensions.map(extensionOf).filter((item): item is ExtensionInfo => item !== undefined),
  };
}

export const MAX_BRAND_NAME = 40;
export const MAX_LOGO_BYTES = 512 * 1024;
export const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'] as const;

/** 名称留空表示恢复默认；超长或带尖括号由这里先拦一道，服务端还会再校验。 */
export function validateBrandName(name: string): string | null {
  const value = name.trim();
  if (value.length > MAX_BRAND_NAME) return `名称不能超过 ${MAX_BRAND_NAME} 个字`;
  if (/[<>]/.test(value)) return '名称里不能有尖括号';
  return null;
}

export function validateLogo(file: File): string | null {
  if (!LOGO_TYPES.includes(file.type as (typeof LOGO_TYPES)[number]))
    return 'Logo 只支持 PNG、JPEG、WebP 或 SVG';
  if (file.size > MAX_LOGO_BYTES) return 'Logo 不能超过 512 KB';
  return null;
}

export async function renameBrand(name: string): Promise<Brand> {
  const value = name.trim();
  return parseBrand(
    await request<unknown>('/api/system/brand', { method: 'PUT', body: { name: value || null } }),
  );
}

export async function uploadLogo(file: File): Promise<Brand> {
  const form = new FormData();
  form.append('file', file, file.name);
  return parseBrand(await request<unknown>('/api/system/brand/logo', { method: 'PUT', form }));
}

export async function resetLogo(): Promise<Brand> {
  return parseBrand(await request<unknown>('/api/system/brand/logo', { method: 'DELETE' }));
}
