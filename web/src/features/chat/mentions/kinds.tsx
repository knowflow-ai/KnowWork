/**
 * @ 的各种东西各叫什么、用什么图标、选了会怎样。菜单分组、输入框里的小标签、气泡上的小标签都从这里取，
 * 用户分得清的关键是：每一处都写出类型名，并且说清选了之后的效果（只在哪里找，或者按什么做法来做）。
 * 前四种都是「在哪里找」，MCP 是「优先用什么工具」，技能是「怎么做」，排序上也分开。
 * 扩展登记的类型（例如数据源）名字和效果由扩展给（@ 菜单的接口里带着），图标统一用一个。
 */
import type { ReactNode } from 'react';
import {
  Bolt,
  BookClosed,
  Cube,
  FileDocument,
  Folder,
  Tag,
  Tools,
} from '@openai/apps-sdk-ui/components/Icon';
import type { Mention, MentionExtension, MentionType } from '../../../api/mentions';

export interface MentionKind {
  /** 类型名，小标签上写在名字前面 */
  readonly label: string;
  readonly icon: ReactNode;
  /** 选了之后的效果，菜单分组与小标签的悬停提示用 */
  readonly effect: string;
}

type BuiltInType = Exclude<MentionType, 'extension'>;

export const MENTION_KINDS: Record<BuiltInType, MentionKind> = {
  knowledge_base: { label: '知识库', icon: <BookClosed />, effect: '只在这个知识库里找' },
  folder: { label: '目录', icon: <Folder />, effect: '只在这个目录下的知识库里找' },
  document: { label: '文件', icon: <FileDocument />, effect: '只在这份文件里找' },
  tag: { label: '标签', icon: <Tag />, effect: '只在带这个标签的文件里找' },
  mcp: { label: 'MCP', icon: <Tools />, effect: '这一轮优先用这个服务的工具' },
  skill: { label: '技能', icon: <Bolt />, effect: '按这个技能的做法来完成' },
};

const EXTENSION_ICON = <Cube />;

/** 一个 @ 的类型名、图标与效果；扩展的按 @ 菜单接口里登记的找，找不到（扩展没了）就只写它带的类型名。 */
export function kindOf(mention: Mention, extensions: readonly MentionExtension[] = []): MentionKind {
  if (mention.type !== 'extension') return MENTION_KINDS[mention.type];
  const found = extensions.find((e) => e.kind === mention.kind);
  const label = found?.label ?? mention.label ?? mention.kind ?? '扩展';
  return { label, icon: EXTENSION_ICON, effect: found?.effect || `点名了这个${label}` };
}

/**
 * 菜单里的分组：目录并在知识库里；扩展登记的每种一组，写成 ext:类型。workspace 是这段对话的工作文件夹（只有执行的一方
 * 看得到文件夹时有，例如本机对话），选中的不是小标签，是写进这一问的 @路径（官方 @file 语法）。
 */
export type MentionGroup =
  'workspace' | 'bases' | 'documents' | 'tags' | 'services' | 'skills' | `ext:${string}`;

export const MENTION_GROUPS: readonly {
  readonly group: MentionGroup;
  readonly label: string;
  readonly icon: ReactNode;
  readonly effect: string;
}[] = [
  { group: 'workspace', label: '工作文件夹', icon: <Folder />, effect: '把文件写进这一问，Agent 自己去读' },
  { group: 'bases', label: '知识库', icon: <BookClosed />, effect: '只在选中的知识库或目录里找' },
  { group: 'documents', label: '文件', icon: <FileDocument />, effect: '只在选中的文件里找' },
  { group: 'tags', label: '标签', icon: <Tag />, effect: '只在带这个标签的文件里找' },
  { group: 'services', label: 'MCP', icon: <Tools />, effect: '这一轮优先用选中服务的工具' },
  { group: 'skills', label: '技能', icon: <Bolt />, effect: '按这个技能的做法来完成' },
];

export const extensionGroup = (kind: string): MentionGroup => `ext:${kind}`;

/** 分组标题：类型名、图标、选了会怎样。 */
export function groupMeta(group: MentionGroup, extensions: readonly MentionExtension[]) {
  const builtIn = MENTION_GROUPS.find((g) => g.group === group);
  if (builtIn) return builtIn;
  const found = extensions.find((e) => extensionGroup(e.kind) === group);
  return { group, label: found?.label ?? '扩展', icon: EXTENSION_ICON, effect: found?.effect ?? '' };
}

/** 小标签排序：先「在哪里找」（扩展登记的也算，例如数据源）再「怎么做」。 */
export const KIND_ORDER: readonly MentionType[] = [
  'knowledge_base',
  'folder',
  'document',
  'tag',
  'extension',
  'skill',
];
