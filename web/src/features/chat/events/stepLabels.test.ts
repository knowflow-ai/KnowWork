import { describe, expect, it } from 'vitest';
import { stepLabel, stepTarget } from './stepLabels';
import type { StepView } from './types';

const step = (name: string, args: unknown): StepView => ({
  callId: 'c1',
  kind: 'tool',
  name,
  arguments: JSON.stringify(args),
  status: 'done',
});

describe('官方工具的步骤说明', () => {
  it('bash 用它必带的一句说明，不显示命令本身', () => {
    const s = step('bash', { description: '生成季度报告', command: 'python3 make_report.py' });
    expect(stepLabel(s)).toBe('运行命令');
    expect(stepTarget(s)).toBe('生成季度报告');
  });

  it('文件工具显示路径，去掉内部的会话工作目录', () => {
    expect(stepTarget(step('read', { file_path: '/sessions/conv_1/data/表.xlsx' }))).toBe('data/表.xlsx');
    expect(stepTarget(step('edit', { file_path: 'notes.md', old_string: 'a', new_string: 'b' }))).toBe(
      'notes.md',
    );
    expect(stepLabel(step('edit', {}))).toBe('修改文件');
  });

  it('present 显示交付的文件名', () => {
    const s = step('present', { files: [{ path: '/sessions/conv_1/out/报告.docx' }, { path: '明细.xlsx' }] });
    expect(stepLabel(s)).toBe('交付文件');
    expect(stepTarget(s)).toBe('报告.docx、明细.xlsx');
  });

  it('记下知识库缺口显示缺什么', () => {
    const s = step('knowledge_gap', { missing: '会员续费的退款规则' });
    expect(stepLabel(s)).toBe('记下知识库缺口');
    expect(stepTarget(s)).toBe('会员续费的退款规则');
  });
});

describe('外部工具的步骤说明', () => {
  it('MCP 工具显示成「服务名 · 工具名」，其余不认识的照原名', () => {
    expect(stepLabel(step('mcp__github__create_issue', { title: 'x' }))).toBe('github · create_issue');
    expect(stepLabel(step('custom_tool', {}))).toBe('custom_tool');
  });
});
