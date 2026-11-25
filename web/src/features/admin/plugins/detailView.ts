/** 插件详情抽屉（PluginDetail.tsx）要显示的东西，和预置、上传两种插件拼成它的办法。 */
import type { PluginComponent } from '../../../api/plugins';
import type { DescriptionItem } from '../../../ui/DescriptionList';

export interface PluginDetailView {
  readonly title: string;
  /** 标题下面的一行：上传的写包名和版本 */
  readonly subtitle?: string;
  readonly description: string;
  readonly components: readonly PluginComponent[];
  /** 来源、版本、适用于这些 */
  readonly facts: readonly DescriptionItem[];
  /** 说明文档：预置的直接给文字；上传的给 id，打开时再取（admin：从插件管理页打开） */
  readonly detail: { readonly text: string } | { readonly uploadId: string; readonly admin: boolean };
}

/** 图片换成它的说明文字 */
export function withoutImages(markdown: string): string {
  return markdown.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
}

/** 预置插件的详情：说明文档、组件清单随版本写好的 */
export function builtinDetailView(
  plugin: {
    readonly name: string;
    readonly description: string;
    readonly detail: string;
    readonly components: readonly PluginComponent[];
  },
  facts: readonly DescriptionItem[],
): PluginDetailView {
  return {
    title: plugin.name,
    description: plugin.description,
    components: plugin.components,
    facts: [{ key: 'source', label: '来源', value: '预置（随版本提供，我们审过）' }, ...facts],
    detail: { text: plugin.detail },
  };
}

/** 上传的插件（空间插件、个人插件）的详情：标题用作者写的，没写用包名；标题下写包名和版本 */
export function uploadDetailView(
  plugin: {
    readonly id: string;
    readonly name: string;
    readonly title: string;
    readonly version: string;
    readonly description: string;
    readonly components: readonly PluginComponent[];
  },
  facts: readonly DescriptionItem[],
  admin: boolean,
): PluginDetailView {
  return {
    title: plugin.title || plugin.name,
    subtitle: plugin.version ? `${plugin.name} ${plugin.version}` : plugin.name,
    description: plugin.description,
    components: plugin.components,
    facts,
    detail: { uploadId: plugin.id, admin },
  };
}
