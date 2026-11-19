import { describe, expect, it } from 'vitest';
import { editRequestText, fileMention, useComposerInbox } from './editRequest';

describe('编辑器里的「AI 修改」交给对话的那句话', () => {
  it('照官方 @file 写文件：有空白加引号，带引号的退回原样', () => {
    expect(fileMention('报告.docx')).toBe('@报告.docx');
    expect(fileMention('三季度 报告.docx')).toBe('@"三季度 报告.docx"');
    expect(fileMention('a "b".docx')).toBe('a "b".docx');
  });

  it('写明在编辑器里修改、选中的是第几块、引用选中的原文、再接要求', () => {
    expect(editRequestText('报告.docx', '第一行\n第二行', '改简洁些', { blocks: { start: 3, end: 4 } })).toBe(
      '在编辑器里修改 @报告.docx，选中的这段（第 3～4 块）：\n> 第一行\n> 第二行\n\n改简洁些',
    );
    expect(editRequestText('报告.docx', '一句', '加个句号', { blocks: { start: 2, end: 2 } })).toBe(
      '在编辑器里修改 @报告.docx，选中的这段（第 2 块）：\n> 一句\n\n加个句号',
    );
    expect(editRequestText('报告.docx', '', '统一成正式的语气')).toBe(
      '在编辑器里修改 @报告.docx：统一成正式的语气',
    );
  });

  it('表格写工作表和区域，演示写第几页（从 1 数）和选中的元素', () => {
    expect(
      editRequestText('营收.xlsx', '', '保留两位小数', { cells: { sheet: '明细', range: 'B3:D6' } }),
    ).toBe('在编辑器里修改 @营收.xlsx，选中的区域（工作表「明细」B3:D6）：保留两位小数');
    expect(
      editRequestText('汇报.pptx', '三季度经营汇报', '换个更有力的标题', {
        slide: { index: 0, elements: ['e_2', 'e_3'] },
      }),
    ).toBe(
      '在编辑器里修改 @汇报.pptx，第 1 页选中的元素（e_2、e_3）：\n> 三季度经营汇报\n\n换个更有力的标题',
    );
    expect(editRequestText('汇报.pptx', '', '逐页检查错别字', { slide: { index: 2, elements: [] } })).toBe(
      '在编辑器里修改 @汇报.pptx，第 3 页：逐页检查错别字',
    );
  });

  it('引用太长就截断（模型照路径自己去读全文）', () => {
    const text = editRequestText('报告.docx', '字'.repeat(3000), '压缩');
    expect(text).toContain(`> ${'字'.repeat(2000)}…`);
    expect(text).not.toContain('字'.repeat(2001));
  });

  it('交给输入框的一句按对话认领，认领过就清掉', () => {
    const inbox = useComposerInbox.getState();
    inbox.offer('c_1', '在原文件上修订 @a.docx：改', true);
    const request = useComposerInbox.getState().request;
    expect(request).toMatchObject({ conversationId: 'c_1', send: true });
    useComposerInbox.getState().take((request?.id ?? 0) + 99);
    expect(useComposerInbox.getState().request).toBe(request);
    useComposerInbox.getState().take(request?.id ?? 0);
    expect(useComposerInbox.getState().request).toBeNull();
  });
});
