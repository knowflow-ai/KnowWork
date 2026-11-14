/**
 * 云端对话的操作（在业务服务上执行）：发消息、停止、审批、回答都交给业务服务。和本机会话那份（扩展经 chat.sources 给）是同一个
 * 接口，会话页只认来源给的这一份，不判断在哪执行（同 serverFiles.ts 之于对话里的文件）。
 */
import { answerQuestion, cancelRun, decideApproval, sendMessage } from '../../api/conversations';
import type { ConversationActions } from './sources';

export const SERVER_ACTIONS: ConversationActions = {
  // 业务服务上的对话在哪个端都能继续
  check: async () => null,
  send: async (conversationId, turn) => (await sendMessage(conversationId, turn)).messageId,
  cancel: async (conversationId) => {
    await cancelRun(conversationId);
  },
  decide: async (conversationId, approvalId, allow, remember) => {
    await decideApproval(conversationId, approvalId, allow, remember);
  },
  answer: async (conversationId, questionId, answers) => {
    await answerQuestion(conversationId, questionId, answers);
  },
  // 授权记在业务服务，宿主的审批应答者先查它
  remembersApprovals: true,
};
