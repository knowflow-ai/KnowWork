/**
 * 步骤的人话说明：执行过程里显示「检索知识库：微短剧 代表作品」，而不是函数名 knowledge_search。
 * MCP 工具（mcp__服务名__工具名）显示成「服务名 · 工具名」；其余不认识的工具照原名显示，
 * 目标取参数里最像目标的那一项。
 */
import { describeArguments } from '../../../utils/format';
import type { StepView } from './types';

const LABELS: Readonly<Record<string, string>> = {
  knowledge_search: '检索知识库',
  knowledge_read: '阅读文档',
  bash: '运行命令',
  read: '读取文件',
  write: '写入文件',
  edit: '修改文件',
  present: '交付文件',
  job_output: '查看后台任务',
  job_kill: '停止后台任务',
  job_list: '查看后台任务',
  kw_now: '获取当前时间',
  skill: '加载技能',
  subagent: '交给子任务',
  send_message: '给子任务发消息',
};

// 这些工具的目标参数是固定的一项，直接取值，不带「键：」前缀
const TARGET_KEY: Readonly<Record<string, string>> = {
  knowledge_search: 'query',
  knowledge_read: 'doc_ref',
  bash: 'description', // 官方 bash 必带一句说明，比命令本身好读
  read: 'file_path',
  write: 'file_path',
  edit: 'file_path',
  skill: 'name',
};

// 会话工作目录（/sessions/<会话 id>）是内部路径，显示时去掉
const SESSION_ROOT = /^\/sessions\/[^/]+\/?/;

function presentedNames(args: Record<string, unknown>): string {
  const files = Array.isArray(args.files) ? args.files : [];
  return files
    .map((file: unknown) =>
      typeof file === 'object' && file !== null ? (file as { path?: unknown }).path : undefined,
    )
    .filter((path): path is string => typeof path === 'string')
    .map((path) => path.split('/').pop() ?? path)
    .join('、');
}

const MAX_TARGET = 80;

const MCP_NAME = /^mcp__([a-z][a-z0-9-]*)__(.+)$/;

/** 工具给人看的名字：执行步骤与审批卡片共用。 */
export function toolLabel(name: string): string {
  const mcp = MCP_NAME.exec(name);
  return LABELS[name] ?? (mcp ? `${mcp[1]} · ${mcp[2]}` : name);
}

export function stepLabel(step: StepView): string {
  if (step.kind === 'subagent') return '子任务';
  return toolLabel(step.name);
}

function clipLine(value: string): string {
  const line = value.trim().split('\n')[0] ?? '';
  return line.length > MAX_TARGET ? `${line.slice(0, MAX_TARGET)}…` : line;
}

export function stepTarget(step: StepView): string {
  const key = TARGET_KEY[step.name];
  if (key || step.name === 'present') {
    try {
      const args = JSON.parse(step.arguments) as Record<string, unknown>;
      if (step.name === 'present') {
        const names = presentedNames(args);
        if (names) return clipLine(names);
      }
      const value: unknown = key ? args[key] : undefined;
      if (typeof value === 'string' && value.trim()) return clipLine(value.replace(SESSION_ROOT, ''));
    } catch {
      // 参数不是 JSON：退回通用描述
    }
  }
  return describeArguments(step.arguments, MAX_TARGET);
}

/** 参数与结果放进代码框：能解析成 JSON 的格式化缩进，不能的原样显示。 */
export function prettyText(raw: string | undefined): string {
  const text = (raw ?? '').trim();
  if (!text) return '';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
