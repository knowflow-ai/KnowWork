import { describe, expect, it } from 'vitest';
import { busyMessage, busyOf, editorMessageOf, IDLE, isIdle } from './editorBridge';

const aiEdit = (overrides: Record<string, unknown> = {}) => ({
  type: 'genoffice:ai-edit',
  instruction: '改简洁些',
  send: true,
  selection: { startBlockIndex: 2, endBlockIndex: 3, isRange: true, text: '原来的两段' },
  ...overrides,
});

describe('编辑器发来的消息', () => {
  it('选中一段的「AI 修改」：要求、原文、块范围', () => {
    expect(editorMessageOf(aiEdit())).toEqual({
      instruction: '改简洁些',
      send: true,
      selection: '原来的两段',
      blocks: { start: 2, end: 3 },
    });
  });

  it('没选中（只有光标）就不带原文和块范围；先放进输入框的 send 为假', () => {
    expect(
      editorMessageOf(
        aiEdit({
          send: false,
          selection: { startBlockIndex: 4, endBlockIndex: 4, isRange: false, text: '' },
        }),
      ),
    ).toEqual({
      instruction: '改简洁些',
      send: false,
      selection: '',
    });
  });

  it('编辑器起好了、断线了；不认识的、没写要求的、块号不对的丢掉', () => {
    expect(editorMessageOf({ type: 'genoffice:ready' })).toBe('ready');
    expect(editorMessageOf({ type: 'genoffice:disconnected' })).toBe('disconnected');
    expect(editorMessageOf({ type: 'genoffice:saved', path: '/work/c1/a.docx' })).toBe('saved');
    expect(editorMessageOf({ type: 'genoffice:agent-edited' })).toBe('agent-edited');
    expect(editorMessageOf({ type: 'other' })).toBeNull();
    expect(editorMessageOf('genoffice:ai-edit')).toBeNull();
    expect(editorMessageOf(aiEdit({ instruction: '  ' }))).toBeNull();
    expect(
      editorMessageOf(
        aiEdit({ selection: { startBlockIndex: 3, endBlockIndex: 1, isRange: true, text: 'x' } }),
      ),
    ).toEqual({
      instruction: '改简洁些',
      send: true,
      selection: '',
    });
  });

  it('表格的选区：工作表名和区域；区域写法不对就当没选', () => {
    expect(
      editorMessageOf(aiEdit({ selection: { isRange: true, text: '', sheet: '明细', range: 'b3:d6' } })),
    ).toEqual({
      instruction: '改简洁些',
      send: true,
      selection: '',
      cells: { sheet: '明细', range: 'B3:D6' },
    });
    expect(
      editorMessageOf(aiEdit({ selection: { isRange: true, text: '', sheet: '明细', range: 'B3;DROP' } })),
    ).toEqual({ instruction: '改简洁些', send: true, selection: '' });
  });

  it('演示的选区：第几页、选中的元素编号和它们的文字；没选元素只给页', () => {
    expect(
      editorMessageOf(
        aiEdit({
          selection: {
            isRange: true,
            text: '三季度经营汇报',
            slide: 0,
            elements: [{ id: 'e_2', type: 'text' }, { id: '' }, 'x'],
          },
        }),
      ),
    ).toEqual({
      instruction: '改简洁些',
      send: true,
      selection: '三季度经营汇报',
      slide: { index: 0, elements: ['e_2'] },
    });
    expect(
      editorMessageOf(aiEdit({ selection: { isRange: false, text: '', slide: 2, elements: [] } })),
    ).toEqual({ instruction: '改简洁些', send: true, selection: '', slide: { index: 2, elements: [] } });
  });

  it('「AI 正在改」的标记：Word 标几块、表格标区域、演示标选中的元素，什么都没有就收掉', () => {
    expect(busyMessage({ ranges: [{ start: 2, end: 3, label: 'AI 正在改' }] })).toEqual({
      type: 'genoffice:ai-busy',
      ranges: [{ start: 2, end: 3, label: 'AI 正在改' }],
    });
    expect(busyMessage({ cells: { sheet: '明细', range: 'B3:D6' } }, 'AI 正在改')).toEqual({
      type: 'genoffice:ai-busy',
      ranges: [],
      cells: { sheet: '明细', range: 'B3:D6', label: 'AI 正在改' },
    });
    expect(busyMessage({ slide: { index: 1, elements: ['e_5'] } }, 'AI 正在改')).toEqual({
      type: 'genoffice:ai-busy',
      ranges: [],
      slide: { index: 1, elements: ['e_5'] },
      label: 'AI 正在改',
    });
    expect(busyMessage(IDLE)).toEqual({ type: 'genoffice:ai-busy', ranges: [] });
    const edit = { instruction: '改', send: true, selection: '' };
    expect(busyOf({ ...edit, blocks: { start: 1, end: 2 } }, '标')).toEqual({
      ranges: [{ start: 1, end: 2, label: '标' }],
    });
    expect(busyOf({ ...edit, cells: { sheet: 'S', range: 'A1' } }, '标')).toEqual({
      cells: { sheet: 'S', range: 'A1' },
    });
    expect(busyOf({ ...edit, slide: { index: 0, elements: [] } }, '标')).toBe(IDLE);
    expect(isIdle(busyOf(edit, '标'))).toBe(true);
  });
});
