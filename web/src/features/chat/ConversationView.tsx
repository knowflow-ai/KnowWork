/**
 * 一个会话的视图：消息列表、运行详情与底部输入框。对话页（ChatPage）和嵌在别的页面里的会话（EmbeddedChat）共用；
 * 给了 answerMode 就按它发消息、不显示回答方式下拉。
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  warmConversation,
  type AnswerMode,
  type QuestionAnswer,
  type SendMode,
} from '../../api/conversations';
import type { SentAttachment } from '../../api/attachments';
import type { Mention } from '../../api/mentions';
import { ownRun, turnOfRun } from '../../api/debug';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { Composer } from './components/Composer';
import { MessageList } from './components/MessageList';
import { selectLastUserMessage, selectRunState } from './events/selectors';
import { useConversation, type FirstMessage, type TurnExtras } from './hooks/useConversation';
import { useFeedback } from './hooks/useFeedback';
import { resolveMentions } from './mentions/resolve';

// 运行详情（运行调试视图）不常用，点开时才加载
const RunInspector = lazy(() => import('../debug/RunInspector'));
const WARM_INTERVAL_MS = 60_000;

const CONNECTION_TEXT = {
  connecting: '正在连接…',
  reconnecting: '连接中断，正在重连…',
  failed: '无法加载这个会话',
} as const;

interface Props {
  readonly conversationId: string;
  /** 刚发出的第一条（新建会话的那一页带过来），回显之前先摆上 */
  readonly firstMessage?: FirstMessage;
  readonly answerMode?: AnswerMode;
  readonly placeholder?: string;
  /** 输入框下面那行小字（见 Composer） */
  readonly footnote?: string;
  /** 一轮跑完（运行中变回空闲）时调：嵌入的页面据此刷新自己的数据，例如 Agent 刚改了页面上显示的东西 */
  readonly onTurnEnd?: () => void;
  /** 输入框正上方的内容（例如嵌入的页面写「正在看：订单」） */
  readonly above?: ReactNode;
  /** 用户发的每一条（不含插话）发出前过一遍，例如嵌入的页面在开头带上正在看的对象 */
  readonly decorate?: (text: string) => string;
}

function ConnectionNotice({ state, message }: { readonly state: string; readonly message?: string }) {
  if (state === 'connected') return null;
  const base = CONNECTION_TEXT[state as keyof typeof CONNECTION_TEXT];
  if (!base) return null;
  return (
    <div className="kw-connection" role="status">
      {state === 'failed' && message ? message : base}
    </div>
  );
}

export function ConversationView({
  conversationId,
  firstMessage,
  answerMode,
  placeholder,
  footnote,
  onTurnEnd,
  above,
  decorate,
}: Props) {
  const feedback = useFeedback(conversationId);
  const client = useQueryClient();
  const { state, connection, connectionMessage, send, cancel, decide, answer } = useConversation(
    conversationId,
    firstMessage,
    answerMode,
  );
  const running = selectRunState(state) !== 'idle';
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) onTurnEnd?.();
    wasRunning.current = running;
  }, [running, onTurnEnd]);
  const [inspecting, setInspecting] = useState<number>();
  // 加了附件就预热（宿主上的对话、沙箱容器空闲半小时会被回收），一分钟内只预热一次；结果不用管
  const warmedAt = useRef(0);
  const warm = useCallback(() => {
    const now = Date.now();
    if (now - warmedAt.current < WARM_INTERVAL_MS) return;
    warmedAt.current = now;
    void warmConversation(conversationId).catch(() => undefined);
  }, [conversationId]);
  const onInspect = useCallback((runId: string) => setInspecting(turnOfRun(runId)), []);

  const deliver = useCallback(
    async (text: string, mode: SendMode, extras: TurnExtras = {}) => {
      await send(text, mode, extras);
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
    [send, client],
  );
  const onSend = useCallback(
    (text: string, mode: SendMode, attachments: readonly SentAttachment[], mentions: readonly Mention[]) =>
      running
        ? deliver(text, mode, { attachments, mentions })
        : deliver(decorate ? decorate(text) : text, 'followup', { attachments, mentions }),
    [deliver, running, decorate],
  );
  // 用同一个问题重新回答（重新生成、撤掉检索条件）：那一问 @ 的东西照原样带上，不然就悄悄放宽成全部知识库
  const answerAgain = useCallback(
    async (dropped: readonly string[] = []) => {
      const last = selectLastUserMessage(state);
      if (!last) return;
      const mentions = await resolveMentions(client, last.mentions);
      await deliver(last.text, 'followup', { mentions, dropped });
    },
    [client, deliver, state],
  );
  const onRegenerate = useCallback(() => {
    answerAgain().catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : '没能重新生成，请稍后重试');
    });
  }, [answerAgain]);
  // 回答上方撤掉检索条件：同一个问题重新回答，这一轮检索时去掉这些条件
  const onDropConditions = useCallback(
    (dropped: readonly string[]) => {
      answerAgain(dropped).catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : '没能重新回答，请稍后重试');
      });
    },
    [answerAgain],
  );
  const onDecide = useCallback(
    async (approvalId: string, allow: boolean, remember?: boolean) => {
      try {
        await decide(approvalId, allow, remember);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '审批没有提交成功，请稍后重试');
      }
    },
    [decide],
  );
  const onAnswer = useCallback(
    async (questionId: string, answers: readonly QuestionAnswer[]) => {
      try {
        await answer(questionId, answers);
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '回答没有提交成功，请稍后重试');
        return false;
      }
    },
    [answer],
  );

  return (
    <div className="kw-chat">
      <div className="kw-chat-scroll">
        <ConnectionNotice state={connection} message={connectionMessage} />
        <MessageList
          state={state}
          onDecide={onDecide}
          onAnswer={onAnswer}
          onRegenerate={onRegenerate}
          onDropConditions={onDropConditions}
          onInspect={onInspect}
          feedback={feedback}
          onAsk={(text) => void onSend(text, 'followup', [], [])}
        />
      </div>
      {inspecting !== undefined ? (
        <Suspense fallback={null}>
          <RunInspector
            source={ownRun(conversationId)}
            turn={inspecting}
            onClose={() => setInspecting(undefined)}
          />
        </Suspense>
      ) : null}
      <div className="kw-composer-dock">
        {above}
        <Composer
          running={running}
          onSend={onSend}
          onStop={cancel}
          initialMentions={firstMessage?.mentions}
          disabled={connection === 'failed'}
          answerMode={answerMode}
          placeholder={placeholder}
          footnote={footnote}
          conversationId={conversationId}
          onAttach={warm}
        />
      </div>
    </div>
  );
}
