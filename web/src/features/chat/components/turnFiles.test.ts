import { describe, expect, it } from 'vitest';
import type { StepView } from '../events/types';
import { turnFileOf, turnFiles } from './turnFiles';

const step = (name: string, args: unknown, status: StepView['status'] = 'done'): StepView => ({
  callId: `${name}-${JSON.stringify(args)}`,
  kind: 'tool',
  name,
  arguments: JSON.stringify(args),
  status,
});

describe('回答里点到的文件（照官方交付插件：只认这一轮改过、交付的）', () => {
  it('这一轮成功写入、修改的文件加上交付的，去重；失败的、读文件的、参数坏的不算', () => {
    const files = turnFiles(
      [
        step('write', { file_path: '/sessions/s1/output/季度汇总.xlsx' }),
        step('edit', { file_path: 'report.docx' }),
        step('edit', { file_path: 'broken.docx' }, 'error'),
        step('read', { file_path: 'input/原件.pdf' }),
        { ...step('write', {}), arguments: '{坏的' },
      ],
      [{ path: 'report.docx' }, { path: '/sessions/s1/合同-修订版.docx' }],
    );
    expect(files).toEqual([
      '/sessions/s1/output/季度汇总.xlsx',
      'report.docx',
      '/sessions/s1/合同-修订版.docx',
    ]);
  });

  it('行内代码整段是路径、和记下的一样或是它结尾的一段才认，交回记下的原样路径', () => {
    const files = [
      '/sessions/s1/output/季度汇总.xlsx',
      '/sessions/s1/合同-修订版.docx',
      'notes/a.md',
      'other/a.md',
    ];
    expect(turnFileOf('output/季度汇总.xlsx', files)).toBe('/sessions/s1/output/季度汇总.xlsx');
    expect(turnFileOf('季度汇总.xlsx', files)).toBe('/sessions/s1/output/季度汇总.xlsx');
    expect(turnFileOf('./合同-修订版.docx', files)).toBe('/sessions/s1/合同-修订版.docx');
    expect(turnFileOf('a.md', files), '同名文件在两个目录：不认').toBeUndefined();
    expect(turnFileOf('pandoc 合同-修订版.docx -o 合同-修订版.pdf', files), '命令不认').toBeUndefined();
    expect(turnFileOf('汇总.xlsx', files), '只是名字的一部分不算').toBeUndefined();
    expect(turnFileOf('SUM', files)).toBeUndefined();
    expect(turnFileOf('report.docx', [])).toBeUndefined();
  });
});
