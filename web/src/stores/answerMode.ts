/**
 * 回答方式：用户在输入框里选「智能体 / 快速问答」，每条消息都带给服务端，换对话也沿用（照 WeKnora，选择是全局的）。
 * 只记在本浏览器里，取不到（隐私模式、存储被禁）或不认识时按智能体。
 */
import { create } from 'zustand';
import type { AnswerMode } from '../api/conversations';

const KEY = 'kw.answerMode';

function load(): AnswerMode {
  try {
    return window.localStorage.getItem(KEY) === 'quick' ? 'quick' : 'agent';
  } catch {
    return 'agent';
  }
}

function save(mode: AnswerMode): void {
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    // 存不下就只在本页有效
  }
}

interface AnswerModeState {
  readonly mode: AnswerMode;
  readonly setMode: (mode: AnswerMode) => void;
}

export const useAnswerMode = create<AnswerModeState>((set) => ({
  mode: load(),
  setMode: (mode) => {
    save(mode);
    set({ mode });
  },
}));

/** 发消息的那一刻读取，不订阅。 */
export const currentAnswerMode = (): AnswerMode => useAnswerMode.getState().mode;
