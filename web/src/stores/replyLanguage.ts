/**
 * 回答语言：用户在输入框里选「中文 / English」，每条消息都带给服务端（明确指定优先）。
 * 只记在本浏览器里，取不到（隐私模式、存储被禁）时默认中文。
 */
import { create } from 'zustand';
import type { ReplyLanguage } from '../api/conversations';

const KEY = 'kw.replyLanguage';

function load(): ReplyLanguage {
  try {
    return window.localStorage.getItem(KEY) === 'en' ? 'en' : 'zh';
  } catch {
    return 'zh';
  }
}

function save(language: ReplyLanguage): void {
  try {
    window.localStorage.setItem(KEY, language);
  } catch {
    // 存不下就只在本页有效
  }
}

interface ReplyLanguageState {
  readonly language: ReplyLanguage;
  readonly setLanguage: (language: ReplyLanguage) => void;
}

export const useReplyLanguage = create<ReplyLanguageState>((set) => ({
  language: load(),
  setLanguage: (language) => {
    save(language);
    set({ language });
  },
}));

/** 发消息的那一刻读取，不订阅。 */
export const currentReplyLanguage = (): ReplyLanguage => useReplyLanguage.getState().language;
