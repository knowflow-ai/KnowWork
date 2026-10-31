/**
 * OAuth 授权的两头：跳去对方页面授权（useMcpLogin），授权完回到站内页面时读网址上的结果给提示（useMcpLoginResult）。
 * 回调由业务服务处理，成功带 mcpConnected=服务 id、失败带 mcpError=原因跳回发起的页面；提示完把这两个参数从网址上去掉，
 * 刷新页面不会再提示一次。
 */
import { useEffect, useRef, useState } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { readableError } from '../../../api/http';
import { startMcpLogin } from '../../../api/mcp';
import { toast } from '../../../ui/toastStore';

/** 整页跳到对方的授权页（单独拿出来，测试里换掉，jsdom 不能真的跳）。 */
export const browser = { go: (url: string) => window.location.assign(url) };

/** 返回 [跳转中的服务 id, 跳过去]；returnTo 是授权完回到的站内页面。拿地址失败时提示原因、留在原页。 */
export function useMcpLogin(): readonly [
  string | undefined,
  (serverId: string, returnTo: string) => Promise<void>,
] {
  const [going, setGoing] = useState<string>();
  const go = async (serverId: string, returnTo: string) => {
    setGoing(serverId);
    try {
      browser.go(await startMcpLogin(serverId, returnTo));
    } catch (error) {
      toast.error(readableError(error, '没能打开授权页，请稍后重试'));
      setGoing(undefined);
    }
  };
  return [going, go];
}

/** 授权回来：按网址上的结果提示一次并刷新相关数据。 */
export function useMcpLoginResult(invalidate: readonly QueryKey[]): void {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const handled = useRef<string>(undefined);
  const connected = params.get('mcpConnected');
  const failed = params.get('mcpError');
  useEffect(() => {
    if (connected === null && failed === null) return;
    const key = `${connected}|${failed}`;
    if (handled.current === key) return; // 去掉参数之前可能再跑一次，不重复提示
    handled.current = key;
    if (failed !== null) toast.error(failed);
    else toast.success('已连接，新开一个对话就能用');
    invalidate.forEach((queryKey) => void client.invalidateQueries({ queryKey }));
    const rest = new URLSearchParams(params);
    rest.delete('mcpConnected');
    rest.delete('mcpError');
    setParams(rest, { replace: true });
  }, [connected, failed, params, setParams, client, invalidate]);
}
