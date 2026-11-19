import { describe, expect, it, vi } from 'vitest';
import { register } from '../../slots/registry';
import { SERVER_SOURCE } from './sources';
import { SERVER_FILES } from './serverFiles';

describe('云端对话文件上扩展补的编辑（插槽 chat.files.server）', () => {
  it('没注册时只能预览；注册了就带上编辑与存回，业务服务那几样照旧，合出来的是同一个对象', async () => {
    const before = SERVER_SOURCE.files;
    expect(before).toBe(SERVER_FILES);
    expect(before?.edit).toBeUndefined();

    const edit = vi.fn(async () => ({ url: 'https://office.example.com/__knowwork/enter?ticket=t' }));
    const saved = vi.fn(async () => undefined);
    register('chat.files.server', { edit, saved });
    const files = SERVER_SOURCE.files;
    expect(files).toBe(SERVER_SOURCE.files);
    expect(files?.read).toBe(SERVER_FILES.read);
    expect(await files?.edit?.('c1', 'output/报告.docx')).toEqual({
      url: 'https://office.example.com/__knowwork/enter?ticket=t',
    });
    await files?.saved?.('c1', 'output/报告.docx');
    expect(saved).toHaveBeenCalledWith('c1', 'output/报告.docx');
  });
});
