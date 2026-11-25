/** 上传的插件在列表里名字下面的一行试装结果（管理页的两张表和「我的插件」共用）。 */
import type { Trialed } from './labels';

/** 列表里插件名下面的一行：检查中、没通过和原因；可用的不写 */
export function TrialNote({ plugin }: { readonly plugin: Trialed }) {
  if (plugin.status === 'checking') return <span className="kw-secondary">正在试装，通过以后才生效…</span>;
  if (plugin.status === 'failed')
    return <span className="kw-plugin-failed">没通过试装，不会生效：{plugin.reason || '没有给出原因'}</span>;
  return null;
}
