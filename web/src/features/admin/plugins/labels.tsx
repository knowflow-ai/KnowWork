/** 上传的插件在列表里的几种说法：适用于、用到的能力、试装结果。管理页和「我的插件」共用。 */
import {
  CAPABILITY_LABELS,
  PLUGIN_TARGETS,
  type PluginTarget,
  type UploadStatus,
} from '../../../api/plugins';
import type { DescriptionItem } from '../../../ui/DescriptionList';

export function targetsText(targets: readonly PluginTarget[]): string {
  if (targets.length === PLUGIN_TARGETS.length) return '云端与本机对话';
  return PLUGIN_TARGETS.find((target) => target.value === targets[0])?.label ?? '—';
}

/** 静态扫描出的能力，一句话；没扫出来就说没有 */
export function capabilitiesText(capabilities: readonly string[]): string {
  if (capabilities.length === 0) return '没有发现读写文件、执行命令、联网这类用法';
  return capabilities.map((key) => CAPABILITY_LABELS[key] ?? key).join('、');
}

export interface Trialed {
  readonly status: UploadStatus;
  readonly reason: string;
  readonly tools: readonly string[];
}

/** 还有在试装的：列表每隔一会儿刷新一次，直到出结果 */
export const anyChecking = (plugins: readonly Trialed[] | undefined) =>
  (plugins ?? []).some((plugin) => plugin.status === 'checking');
export const CHECKING_REFRESH_MS = 2000;

/** 详情里的试装结果和新增的工具。没开云端插件运行环境时服务端不试装，只在桌面端跑 */
export function trialFacts(plugin: Trialed, cloudAvailable: boolean): readonly DescriptionItem[] {
  if (!cloudAvailable)
    return [{ key: 'trial', label: '试装', value: '没有在服务端试装：这个部署没有开启云端的插件运行环境' }];
  if (plugin.status === 'checking') return [{ key: 'trial', label: '试装', value: '正在试装' }];
  if (plugin.status === 'failed')
    return [{ key: 'trial', label: '试装', value: `没通过：${plugin.reason || '没有给出原因'}` }];
  return [
    { key: 'trial', label: '试装', value: '通过' },
    {
      key: 'tools',
      label: '新增的工具',
      value:
        plugin.tools.length > 0
          ? plugin.tools.join('、')
          : '没有全局登记的工具（开对话时才登记的这里看不到）',
    },
  ];
}
