import { beforeEach, describe, expect, it, vi } from 'vitest';

// 选择在模块加载时从本浏览器读出，每个用例重新加载模块
const load = () => import('./answerMode');

describe('回答方式', () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  it('默认智能体；选了快速问答记在本浏览器，换对话、刷新后还是它', async () => {
    const first = await load();
    expect(first.currentAnswerMode()).toBe('agent');
    first.useAnswerMode.getState().setMode('quick');
    expect(first.currentAnswerMode()).toBe('quick');
    expect(window.localStorage.getItem('kw.answerMode')).toBe('quick');
    vi.resetModules();
    expect((await load()).currentAnswerMode()).toBe('quick');
  });

  it('存的值不认识时按智能体', async () => {
    window.localStorage.setItem('kw.answerMode', 'fast');
    expect((await load()).currentAnswerMode()).toBe('agent');
  });
});

describe('回答方式的可选项', () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  it('记住的方式在可选项里才发它，不在时按智能体', async () => {
    window.localStorage.setItem('kw.answerMode', 'custom');
    const store = await load();
    expect(store.currentAnswerMode()).toBe('agent');
    store.useAnswerMode.getState().setOptions([
      { id: 'agent', label: '智能体', description: '' },
      { id: 'custom', label: '扩展方式', description: '' },
    ]);
    expect(store.currentAnswerMode()).toBe('custom');
  });

  it('格式不对的存值不读', async () => {
    window.localStorage.setItem('kw.answerMode', 'Bad mode!');
    expect((await load()).useAnswerMode.getState().mode).toBe('agent');
  });
});
