/**
 * @ 的六种东西各叫什么、用什么图标、选了会怎样。菜单分组、输入框里的小标签、气泡上的小标签都从这里取，
 * 用户分得清的关键是：每一处都写出类型名，并且说清选了之后的效果（只在哪里找，或者按什么做法来做）。
 * 前四种都是「在哪里找」，MCP 是「优先用什么工具」，技能是「怎么做」，排序上也分开。
 */
import type { ReactNode } from 'react';
import { Bolt, BookClosed, FileDocument, Folder, Tag, Tools } from '@openai/apps-sdk-ui/components/Icon';
import type { MentionType } from '../../../api/mentions';

export interface MentionKind {
  /** 类型名，小标签上写在名字前面 */
  readonly label: string;
  readonly icon: ReactNode;
  /** 选了之后的效果，菜单分组与小标签的悬停提示用 */
  readonly effect: string;
}

export const MENTION_KINDS: Record<MentionType, MentionKind> = {
  knowledge_base: { label: '知识库', icon: <BookClosed />, effect: '只在这个知识库里找' },
  folder: { label: '目录', icon: <Folder />, effect: '只在这个目录下的知识库里找' },
  document: { label: '文件', icon: <FileDocument />, effect: '只在这份文件里找' },
  tag: { label: '标签', icon: <Tag />, effect: '只在带这个标签的文件里找' },
  mcp: { label: 'MCP', icon: <Tools />, effect: '这一轮优先用这个服务的工具' },
  skill: { label: '技能', icon: <Bolt />, effect: '按这个技能的做法来完成' },
};

/** 菜单里的分组：目录并在知识库里。 */
export type MentionGroup = 'bases' | 'documents' | 'tags' | 'services' | 'skills';

export const MENTION_GROUPS: readonly {
  readonly group: MentionGroup;
  readonly label: string;
  readonly icon: ReactNode;
  readonly effect: string;
}[] = [
  { group: 'bases', label: '知识库', icon: <BookClosed />, effect: '只在选中的知识库或目录里找' },
  { group: 'documents', label: '文件', icon: <FileDocument />, effect: '只在选中的文件里找' },
  { group: 'tags', label: '标签', icon: <Tag />, effect: '只在带这个标签的文件里找' },
  { group: 'services', label: 'MCP', icon: <Tools />, effect: '这一轮优先用选中服务的工具' },
  { group: 'skills', label: '技能', icon: <Bolt />, effect: '按这个技能的做法来完成' },
];

/** 小标签排序：先「在哪里找」再「怎么做」。 */
export const KIND_ORDER: readonly MentionType[] = ['knowledge_base', 'folder', 'document', 'tag', 'skill'];
