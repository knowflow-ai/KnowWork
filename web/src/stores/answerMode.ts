/**
 * 回答方式：用户在输入框里选（智能体、快速问答，或扩展登记的），每条消息都带给服务端，换对话也沿用（选择是全局的）。
 * 只记在本浏览器里，取不到（隐私模式、存储被禁）时按智能体。
 * 可选项从服务端取（AnswerModeSelect 拿到后放进来，之前是内置两种）：记住的选择不在可选项里时（例如扩展没了授权）按智能体发，
 * 不发一个服务端不认的方式。
 */
import { create } from 'zustand';
import { BUILT_IN_ANSWER_MODES, type AnswerMode, type AnswerModeOption } from '../api/conversations';

const KEY = 'kw.answerMode';
const FORMAT = /^[a-z][a-z0-9_]{0,31}$/;

function load(): AnswerMode {
  try {
    const stored = window.localStorage.getItem(KEY);
    return stored && FORMAT.test(stored) ? stored : 'agent';
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
  readonly options: readonly AnswerModeOption[];
  readonly setMode: (mode: AnswerMode) => void;
  readonly setOptions: (options: readonly AnswerModeOption[]) => void;
}

export const useAnswerMode = create<AnswerModeState>((set) => ({
  mode: load(),
  options: BUILT_IN_ANSWER_MODES,
  setMode: (mode) => {
    save(mode);
    set({ mode });
  },
  setOptions: (options) => set({ options }),
}));

/** 选中的方式在可选项里才算数，否则按智能体。 */
export const effectiveAnswerMode = (mode: AnswerMode, options: readonly AnswerModeOption[]): AnswerMode =>
  options.some((option) => option.id === mode) ? mode : 'agent';

/** 发消息的那一刻读取，不订阅。 */
export const currentAnswerMode = (): AnswerMode => {
  const { mode, options } = useAnswerMode.getState();
  return effectiveAnswerMode(mode, options);
};
