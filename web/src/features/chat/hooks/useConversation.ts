/**
 * 一个会话的界面状态：订阅事件流折叠成状态，拉取会话产物与引用出处，并提供发送、停止、审批、回答提问动作。
 */
import { useCallback, useEffect, useReducer, useState } from 'react';
import {
  eventsUrl,
  turnRequest,
  type AnswerMode,
  type QuestionAnswer,
  type SendMode,
} from '../../../api/conversations';
import { chipsOf, type AttachmentChip, type SentAttachment } from '../../../api/attachments';
import type { Mention } from '../../../api/mentions';
import type { ConversationActions } from '../sources';
import { currentAnswerMode } from '../../../stores/answerMode';
import { currentChatModel } from '../../../stores/chatModel';
import { currentReplyLanguage } from '../../../stores/replyLanguage';
import { ApiError, readableError } from '../../../api/http';
import { openEventStream } from '../../../api/sse';
import { chatReducer, initialChatState } from '../events/reducer';
import type { ChatState } from '../events/types';
import { completedRunCount, knowledgeResults, useCitations } from './useCitations';

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'failed';

export interface ConversationApi {
  readonly state: ChatState;
  readonly connection: ConnectionState;
  readonly connectionMessage?: string;
  readonly send: (text: string, mode: SendMode, extras?: TurnExtras) => Promise<void>;
  readonly cancel: () => Promise<void>;
  readonly decide: (approvalId: string, allow: boolean, remember?: boolean) => Promise<void>;
  readonly answer: (questionId: string, answers: readonly QuestionAnswer[]) => Promise<void>;
}

let localCounter = 0;
export const nextLocalId = (): string => {
  localCounter += 1;
  return `local-${Date.now()}-${localCounter}`;
};

/** 动作失败时抛出带可读提示的 Error，由页面用 message 展示。 */
function rethrow(error: unknown, fallback: string): never {
  throw new Error(readableError(error, fallback));
}

/** 新对话页已经发出的首条消息（跳转到会话页时经导航状态带过来）。 */
export interface FirstMessage {
  readonly text: string;
  readonly attachments?: readonly AttachmentChip[];
  readonly mentions?: readonly Mention[];
  /** 发送接口返回的消息 id：回显到了按它合并 */
  readonly messageId?: string;
}

/** 一轮消息除了文字以外带的东西。 */
export interface TurnExtras {
  /** 输入框里已经传好的附件，发消息时服务端认领进这个会话 */
  readonly attachments?: readonly SentAttachment[];
  /** 回答上方撤掉的检索条件 */
  readonly dropped?: readonly string[];
  /** 输入框里 @ 的东西 */
  readonly mentions?: readonly Mention[];
}

/** 新建会话时首条消息已经发出：事件流回显之前先把它摆上。 */
function initialState(first?: FirstMessage): ChatState {
  if (!first) return initialChatState;
  const localId = nextLocalId();
  const sent = chatReducer(initialChatState, {
    type: 'local/sent',
    localId,
    text: first.text,
    mode: 'followup',
    attachments: first.attachments,
    mentions: first.mentions,
  });
  return chatReducer(sent, { type: 'local/delivered', localId, messageId: first.messageId });
}

/**
 * answerMode：固定的回答方式（嵌在别的页面里的会话）；不给时按输入框上方的回答方式下拉（全局选择）。
 * actions：发消息、停止、审批、回答交给谁（对话来源给的，sources.ts）；在哪执行都一样，事件流、产物、引用都从业务服务来。
 * 发消息交出的是和云端同一个请求（turnRequest）。
 */
export function useConversation(
  conversationId: string | undefined,
  first: FirstMessage | undefined,
  answerMode: AnswerMode | undefined,
  actions: ConversationActions,
): ConversationApi {
  const [state, dispatch] = useReducer(chatReducer, first, initialState);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [connectionMessage, setConnectionMessage] = useState<string>();
  const completedRuns = completedRunCount(state);
  useCitations(conversationId, `${completedRuns}-${knowledgeResults(state)}`, dispatch);

  useEffect(() => {
    // 页面按会话 id 重新挂载（ChatPage 用 key），这里不需要手动重置状态。
    if (!conversationId) return undefined;
    return openEventStream(
      { url: eventsUrl(conversationId) },
      {
        onEvent: (event) => dispatch({ type: 'wire', event }),
        onLive: (live) => dispatch({ type: 'live', live }),
        onStatus: (status, reason) => {
          setConnection(status);
          setConnectionMessage(reason);
        },
        onFatal: (message) => {
          setConnection('failed');
          setConnectionMessage(message);
        },
      },
    );
  }, [conversationId]);

  const send = useCallback(
    async (text: string, mode: SendMode, extras: TurnExtras = {}) => {
      if (!conversationId) return;
      const { attachments = [], dropped = [], mentions = [] } = extras;
      const localId = nextLocalId();
      dispatch({ type: 'local/sent', localId, text, mode, attachments: chipsOf(attachments), mentions });
      const turn = turnRequest(text, mode, {
        language: currentReplyLanguage(),
        answerMode: answerMode ?? currentAnswerMode(),
        model: currentChatModel(conversationId),
        attachments: attachments.map((item) => item.id),
        droppedConditions: dropped,
        mentions,
      });
      try {
        const messageId = await actions.send(conversationId, turn);
        dispatch({ type: 'local/delivered', localId, ...(messageId === undefined ? {} : { messageId }) });
      } catch (error) {
        dispatch({ type: 'local/failed', localId });
        rethrow(error, '消息没有发出去，请稍后重试');
      }
    },
    [conversationId, answerMode, actions],
  );

  const cancel = useCallback(async () => {
    if (!conversationId) return;
    try {
      await actions.cancel(conversationId);
    } catch (error) {
      rethrow(error, '没能停止，请稍后重试');
    }
  }, [conversationId, actions]);

  const decide = useCallback(
    async (approvalId: string, allow: boolean, remember = false) => {
      if (!conversationId) return;
      try {
        await actions.decide(conversationId, approvalId, allow, remember);
      } catch (error) {
        rethrow(error, '审批没有提交成功，请稍后重试');
      }
    },
    [conversationId, actions],
  );

  const answer = useCallback(
    async (questionId: string, answers: readonly QuestionAnswer[]) => {
      if (!conversationId) return;
      try {
        await actions.answer(conversationId, questionId, answers);
      } catch (error) {
        // 问题已结束（宿主重启过、已经答过）：卡片标成失效，提示改在输入框里回复
        if (error instanceof ApiError && error.code === 'question_not_found') {
          dispatch({ type: 'local/question-expired', questionId });
        }
        rethrow(error, '回答没有提交成功，请稍后重试');
      }
    },
    [conversationId, actions],
  );

  return { state, connection, connectionMessage, send, cancel, decide, answer };
}
