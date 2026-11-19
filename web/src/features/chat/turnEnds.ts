/**
 * 一段对话的一轮结束了（对话页看到运行状态从在跑变回空闲，ConversationView 发出）：按对话通知扩展的来源。
 * 例如云端在线编辑：一轮结束去问编辑器里开着的文件这一轮变没变（同桌面端在本机日志里看 turn/end）。
 * 只在页面开着、看得到这段对话时才有；页面不在，编辑器也不在眼前，不用通知。
 */

type Listener = () => void;

let listeners: ReadonlyMap<string, readonly Listener[]> = new Map();

/** 这段对话每一轮结束时调 listener；返回取消订阅 */
export function onTurnEnd(conversationId: string, listener: Listener): () => void {
  listeners = new Map(listeners).set(conversationId, [...(listeners.get(conversationId) ?? []), listener]);
  return () => {
    const rest = (listeners.get(conversationId) ?? []).filter((item) => item !== listener);
    const next = new Map(listeners);
    if (rest.length > 0) next.set(conversationId, rest);
    else next.delete(conversationId);
    listeners = next;
  };
}

/** 这段对话的一轮结束了；一个订阅者出错不影响别的（各自管自己的提示） */
export function turnEnded(conversationId: string): void {
  for (const listener of listeners.get(conversationId) ?? []) {
    try {
      listener();
    } catch {
      // 订阅者自己负责报错；这里只保证别的照常收到
    }
  }
}
