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
