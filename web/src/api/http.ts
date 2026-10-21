/**
 * 业务服务请求封装：浏览器用 Cookie 会话（kw_session，HttpOnly），前端不存令牌；
 * 把各种失败翻译成给用户看的话，不暴露错误码。会话过期（401）时通知登录状态。
 */
import { useSessionStore } from '../stores/session';

export class ApiError extends Error {
  readonly status: number;
  /** 后端 detail 里的错误码，只给程序区分情况用，不给用户看。 */
  readonly code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const STATUS_MESSAGES: Record<number, string> = {
  400: '提交的内容有误，请检查后重试',
  401: '登录已过期，请重新登录',
  403: '没有权限执行这个操作',
  404: '要找的内容不存在或已删除',
  409: '操作冲突，请刷新后重试',
  413: '内容太大，请精简后重试',
  422: '提交的内容有误，请检查后重试',
  429: '操作太频繁，请稍后再试',
};

/** 后端 detail 可能是字符串或 {code, message}；只取给人看的 message。字段校验数组不给人看。 */
function detailMessage(body: unknown): string | undefined {
  if (typeof body !== 'object' || body === null) return undefined;
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (typeof detail === 'object' && detail !== null && !Array.isArray(detail)) {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return undefined;
}

function detailCode(body: unknown): string | undefined {
  const detail =
    typeof body === 'object' && body !== null ? (body as Record<string, unknown>).detail : undefined;
  const code =
    typeof detail === 'object' && detail !== null ? (detail as Record<string, unknown>).code : undefined;
  return typeof code === 'string' && code ? code : undefined;
}

export function messageForStatus(status: number, body?: unknown): string {
  const fromServer = detailMessage(body);
  if (fromServer) return fromServer;
  if (STATUS_MESSAGES[status]) return STATUS_MESSAGES[status];
  return status >= 500 ? '服务暂时不可用，请稍后重试' : '请求没有成功，请稍后重试';
}

export const NETWORK_MESSAGE = '无法连接服务，请检查网络后重试';

export type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  readonly method?: Method;
  readonly body?: unknown;
  readonly query?: Readonly<Record<string, QueryValue>>;
  readonly signal?: AbortSignal;
  /** 为真时 401 只当作普通失败（登录、取当前用户），不触发「会话已过期」。 */
  readonly quietUnauthorized?: boolean;
  /** 原样发送的请求体（例如技能包 ZIP），与 body 二选一；contentType 给它的类型。 */
  readonly raw?: Blob | ArrayBuffer;
  /** multipart 表单（例如附件上传）：Content-Type 与分隔符由浏览器生成，不能手写。 */
  readonly form?: FormData;
  readonly contentType?: string;
  readonly headers?: Readonly<Record<string, string>>;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export function withQuery(path: string, query?: RequestOptions['query']): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

/** 路径片段一律转义，防止 id 里带斜杠等字符拼出别的接口。 */
export const seg = (value: string): string => encodeURIComponent(value);

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.raw !== undefined) headers['Content-Type'] = options.contentType ?? 'application/octet-stream';
  let response: Response;
  try {
    response = await fetch(withQuery(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      credentials: 'same-origin',
      body:
        options.form ??
        options.raw ??
        (options.body === undefined ? undefined : JSON.stringify(options.body)),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  const body = await readJson(response);
  if (!response.ok) {
    if (response.status === 401 && !options.quietUnauthorized) useSessionStore.getState().expire();
    throw new ApiError(response.status, messageForStatus(response.status, body), detailCode(body));
  }
  return body as T;
}

/** 取二进制内容（预览用）：鉴权与错误翻译同 request。 */
export async function requestBytes(path: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: 'same-origin', signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  if (!response.ok) {
    if (response.status === 401) useSessionStore.getState().expire();
    throw new ApiError(response.status, messageForStatus(response.status, await readJson(response)));
  }
  return response.arrayBuffer();
}

/** 把任意异常变成一句可读的话。 */
export function readableError(error: unknown, fallback = '操作没有成功，请稍后重试'): string {
  if (error instanceof ApiError) return error.message;
  return fallback;
}
