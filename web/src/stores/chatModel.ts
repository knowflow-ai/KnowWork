/**
 * 输入框右下角选的对话模型：按对话记（照 DSH 官方，模型是会话级的选择，从下一步生效）。
 * 键是对话 id，新对话页用 NEW_CHAT；建好对话后把新对话页上的选择带过去（carry），首条消息就用它。
 * 每段对话默认选什么由服务端给（这段对话上次用的；新对话用这个人上次用的；都没有用默认模型，见 api/conversations
 * 的 listChatModels），用户在下拉里选了就以用户选的为准。只在本页内存里，刷新后按服务端给的默认。
 */
import { create } from 'zustand';

export const NEW_CHAT = 'new';

const without = (record: Readonly<Record<string, string>>, key: string) =>
  Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));

interface ChatModelState {
  /** 用户在下拉里选的 */
  readonly chosen: Readonly<Record<string, string>>;
  /** 服务端给的默认 */
  readonly defaults: Readonly<Record<string, string>>;
  readonly choose: (key: string, alias: string) => void;
  readonly setDefault: (key: string, alias: string | null) => void;
  /** 新对话建好了：新对话页上的选择带到这段对话，新对话页下次从头按服务端的默认 */
  readonly carry: (from: string, to: string) => void;
}

export const useChatModel = create<ChatModelState>((set) => ({
  chosen: {},
  defaults: {},
  choose: (key, alias) => set((s) => ({ chosen: { ...s.chosen, [key]: alias } })),
  setDefault: (key, alias) =>
    set((s) => {
      if (alias === null) return { defaults: without(s.defaults, key) };
      return { defaults: { ...s.defaults, [key]: alias } };
    }),
  carry: (from, to) =>
    set((s) => {
      const picked = s.chosen[from] ?? s.defaults[from];
      const rest = without(s.chosen, from);
      return picked ? { chosen: { ...rest, [to]: picked } } : { chosen: rest };
    }),
}));

/** 这段对话现在选的模型：用户选的优先，其次服务端给的默认；都没有（还没取到、没配模型）为 undefined。 */
export const selectedChatModel = (state: Pick<ChatModelState, 'chosen' | 'defaults'>, key: string) =>
  state.chosen[key] ?? state.defaults[key];

/** 发消息的那一刻读取，不订阅。 */
export const currentChatModel = (key: string): string | undefined =>
  selectedChatModel(useChatModel.getState(), key);
