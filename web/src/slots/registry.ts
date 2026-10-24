/**
 * 前端插槽：扩展模块的前端包把菜单项、页面、设置分区注册到命名插槽，核心页面按插槽渲染；
 * 开源构建时所有插槽为空，界面上没有任何扩展的入口。与后端的扩展加载（server/src/knowwork_server/extensions）同一思路。
 *
 * 注册入口是别名 @knowwork/extensions：默认指向 ./none.ts；构建带扩展的镜像时设环境变量 KNOWWORK_WEB_EXTENSIONS
 * 指向扩展包的入口文件（见 vite.config.ts），它导出 install(register)。开源代码里不 import 任何扩展；
 * 扩展包反过来经别名 @knowwork/web 用核心的组件、接口与工具（同一份 React 与组件库）。
 * 注册在第一次查询插槽时才发生（不靠入口文件 import 一次），单独渲染组件的测试里也拿得到。
 * 前端插槽只决定显示什么；扩展有没有授权由后端判定，没授权时它的接口不存在。
 */
import type { RouteObject } from 'react-router';
import { install } from '@knowwork/extensions';
import type { PreviewFile } from '../features/preview/FilePreview';
import type { NavItem } from '../layout/nav';
import type { TabItem } from '../ui/Tabs';

export interface AdminNavEntry {
  /** 插进哪个分组（管理后台侧栏的分组名，如「能力接入」）；没有这个分组就新起一组，排在系统设置之前 */
  readonly group: string;
  readonly item: NavItem;
  /** 组内位置：插在第几项之前，不给就放在组尾 */
  readonly before?: number;
}

export interface CitationPreviewer {
  /** 处理哪个知识源的引用（引用里的 sourceId） */
  readonly sourceId: string;
  /** 文档引用 → 能在预览抽屉里打开的文件；文档没了或没有权限时返回 null。点开时才调，按当时的权限判定 */
  readonly resolve: (docRef: string) => Promise<PreviewFile | null>;
}

/** 授权页知识库树里的一个节点：resource 是授权用的完整资源（`类型:id`），name 是这一层的名字。 */
export interface GrantTreeNode {
  readonly resource: string;
  readonly name: string;
  readonly children: readonly GrantTreeNode[];
}

export interface KnowledgeTree {
  /** 哪个知识源（和知识库列表接口的 source_id 一致）：授权页里这个知识源下按这棵树显示，不再平铺 */
  readonly sourceId: string;
  /** 树里出现的、内置以外的资源类型的中文名（授权列表显示类型用），例如按目录授权的那种 */
  readonly types: Readonly<Record<string, string>>;
  /** 这个知识源下当前管理员看得到的层级（目录、知识库）；授权页打开时取 */
  readonly load: () => Promise<readonly GrantTreeNode[]>;
}

export interface ExtensionSlots {
  /** 使用端侧栏「新对话」下面的入口（例如知识库），按注册顺序排 */
  'app.nav': NavItem;
  /** 管理后台侧栏的菜单项 */
  'admin.nav': AdminNavEntry;
  /** 管理后台的页面，path 相对 /admin */
  'admin.routes': RouteObject;
  /** 使用端的页面，path 相对 /（和对话页同一个外壳） */
  'app.routes': RouteObject;
  /** 个人设置页的标签页，排在内置标签页之后 */
  'settings.tabs': TabItem;
  /** 管理后台「模型」页的标签页（例如扩展自己的服务配置），排在内置标签页之后 */
  'admin.models.tabs': TabItem;
  /** 对话里点引用时打开原文预览（按定位高亮）；没有注册的知识源仍跳它给的原文链接 */
  'citation.preview': CitationPreviewer;
  /** 授权页里某个知识源下的层级树（例如目录 → 知识库，可以按目录授权）；没注册的知识源平铺知识库 */
  'grants.knowledge': KnowledgeTree;
}

export type SlotName = keyof ExtensionSlots;
export type Register = <K extends SlotName>(slot: K, item: ExtensionSlots[K]) => void;

const slots = new Map<SlotName, readonly unknown[]>();
const NONE: readonly never[] = []; // 空插槽每次都给同一个数组，页面拿它当依赖时不会每次重算
let installed = false;

export const register: Register = (slot, item) => {
  const items = slots.get(slot) ?? [];
  if (!items.includes(item)) slots.set(slot, [...items, item]);
};

function ensureInstalled(): void {
  if (installed) return;
  installed = true;
  install(register);
}

/** 插槽里注册的全部条目，按注册顺序。 */
export function slotItems<K extends SlotName>(slot: K): readonly ExtensionSlots[K][] {
  ensureInstalled();
  return (slots.get(slot) ?? NONE) as readonly ExtensionSlots[K][];
}
