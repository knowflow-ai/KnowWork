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
import type { ComponentType, ReactNode } from 'react';
import type { RouteObject } from 'react-router';
import { install } from '@knowwork/extensions';
import type { PreviewFile } from '../features/preview/FilePreview';
import type { NavItem } from '../layout/nav';
import type { TabItem } from '../ui/Tabs';
import type { Mention } from '../api/mentions';

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

/** 问答大盘知识库表里，某个知识源的知识库那一行可以挂的动作（例如扩展自己的改进页）。 */
export interface InsightsBaseAction {
  readonly sourceId: string;
  readonly label: string;
  /** 知识源内的知识库 id → 站内链接 */
  readonly to: (kbId: string) => string;
}

/** 授权页知识库树里的一个节点：resource 是授权用的完整资源（`类型:id`），name 是这一层的名字。 */
export interface GrantTreeNode {
  readonly resource: string;
  readonly name: string;
  readonly children: readonly GrantTreeNode[];
  /** 只是分组、不能授权（例如「共享给本空间」），不给就是能选 */
  readonly selectable?: boolean;
}

export interface KnowledgeTree {
  /** 哪个知识源（和知识库列表接口的 source_id 一致）：授权页里这个知识源下按这棵树显示，不再平铺 */
  readonly sourceId: string;
  /** 树里出现的、内置以外的资源类型的中文名（授权列表显示类型用），例如按目录授权的那种 */
  readonly types: Readonly<Record<string, string>>;
  /** 这个知识源下当前管理员看得到的层级（目录、知识库）；授权页打开时取 */
  readonly load: () => Promise<readonly GrantTreeNode[]>;
}

/** 扩展登记的一类可授权资源（平铺、没有上级，例如数据源）：授权页的「资源类型」里多一项，选资源时按 load 列出。
 * 后端要在同名类型上登记过（AccessCore.add_resource_types），不然授权接口不认。 */
export interface GrantResourceType {
  /** 资源类型，同后端登记的，如 data_source */
  readonly type: string;
  readonly label: string;
  /** 三档在这类资源上各是什么意思（如「使用：问数；编辑：建模」），显示在授权对话框的「权限」下面 */
  readonly help?: string;
  /** 授权列表里这类资源的图标（组件库的图标，和这类资源在别处用的一样） */
  readonly icon?: ReactNode;
  /** 能授权的资源；授权页打开时取 */
  readonly load: () => Promise<readonly { readonly id: string; readonly name: string }[]>;
}

/** 结果卡片（出处那一张）上的一个操作，例如「存为可信查询」：工具在卡片里写上操作名与 ref，前端按名字找这里登记的组件，
 * 组件自己画按钮、弹窗，自己调扩展的接口（接口判权）。question 是这一轮用户的提问。 */
export interface CardActionItem {
  readonly name: string;
  readonly Component: ComponentType<{ readonly reference: string; readonly question: string }>;
}

/** 输入框左上角回答方式后面的一小段（例如问数的范围条「问数范围：销售库 · 第 3 版」）。conversationId 没有时是新对话；
 * running 是这一轮在不在跑（一轮开始、结束时组件可以重取自己的状态，例如 @ 改了范围）。组件自己按回答方式决定显示不显示，
 * 自己调扩展的接口。嵌在别的页面里、固定了回答方式的输入框不显示这一行。 */
/** 新对话页输入框下面的一块（例如按当前回答方式给几个能直接问的问题）；ask 等同在输入框里打了这句、带着这些 @ 发出去 */
export interface ChatWelcomeItem {
  readonly Component: ComponentType<{
    readonly answerMode: string;
    readonly ask: (text: string, mentions: readonly Mention[]) => void;
  }>;
}

export interface ComposerTopItem {
  readonly Component: ComponentType<{
    readonly conversationId?: string;
    readonly answerMode: string;
    readonly running: boolean;
  }>;
}

/** 扩展提供的工具在对话执行过程里怎么显示（同开源内置工具的 stepLabels）：「记住：回答用表格」而不是函数名。 */
export interface ToolPresentation {
  /** 工具名（同后端 ToolSpec.name） */
  readonly name: string;
  /** 给人看的名字，如「记住」 */
  readonly label: string;
  /** 参数里当目标显示的那一项，如 content；不给就用通用的参数摘要 */
  readonly target?: string;
  /**
   * 回答下方的一行说明（例如「已记住：回答用表格 · 管理记忆」），按这一轮里每次做完、没出错的调用算；
   * result 是工具结果的文字（可能截断），返回 null 不显示。执行过程默认收起，用户要知道的副作用放这里。
   */
  readonly notice?: (call: ToolCallDone) => ToolNotice | null;
  /**
   * 批准前给人看的明细（例如要发布的改动清单）：画在审批卡片里，代替通用的参数摘要；arguments 是调用参数（解析后）。
   * 审批照样由宿主发起、按钮照样是批准与拒绝，这里只管把要做的事写清楚
   */
  readonly approval?: ComponentType<{ readonly arguments: Readonly<Record<string, unknown>> }>;
}

export interface ToolCallDone {
  /** 调用参数（JSON 解析后；解析不了是空对象） */
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly result: string;
}

export interface ToolNotice {
  readonly text: string;
  /** 站内链接，例如 /settings?tab=memory */
  readonly link?: { readonly label: string; readonly to: string };
}

export interface ExtensionSlots {
  /** 使用端侧栏「新对话」下面的入口（例如知识库），按注册顺序排 */
  'app.nav': NavItem;
  /** 管理后台侧栏的菜单项 */
  'admin.nav': AdminNavEntry;
  /** 管理后台「系统」分组的菜单项（只有系统管理员看得到，例如空间管理），排在「模型」后面 */
  'system.nav': NavItem;
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
  /** 授权页里扩展自己的资源类型（例如数据源）；没注册时只有内置的几类 */
  'grants.resources': GrantResourceType;
  /** 问答大盘知识库表那一行的动作；没注册的知识源不显示 */
  'insights.base.action': InsightsBaseAction;
  /** 结果卡片上的操作（按操作名）；卡片里写了、这里没登记的不显示 */
  'chat.card.actions': CardActionItem;
  /** 扩展提供的工具在执行过程里的显示名；没注册的照原名显示 */
  'chat.tools': ToolPresentation;
  /** 输入框左上角回答方式后面的一小段；没注册时只有回答方式 */
  'chat.composer.top': ComposerTopItem;
  /** 新对话页输入框下面的一块（建议问题这类）；没注册时什么都没有 */
  'chat.welcome': ChatWelcomeItem;
  /** 技能页「企业」页签里系统管理员看到的内容（管理企业技能本身，连同本空间的设置）；没注册时只有本空间的设置 */
  'admin.skills.enterprise': PanelItem;
  /** 「工具与 MCP」页的「企业」页签：注册了才有这个页签；系统管理员看到这里给的内容（管理企业 MCP 本身，连同本空间的设置），
   * 其他管理员看本空间的停用与给谁用 */
  'admin.tools.enterprise': PanelItem;
}

/** 一块由扩展给出的页面内容。 */
export interface PanelItem {
  readonly children: ReactNode;
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
