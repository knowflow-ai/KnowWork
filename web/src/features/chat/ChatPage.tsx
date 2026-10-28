/** 对话页：居中单栏消息列表 + 底部输入框。没有会话 id 时是新对话的欢迎页。 */
import { lazy, Suspense, useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import { chipsOf, uploadForTurn } from '../../api/attachments';
import { createConversation, sendMessage, type QuestionAnswer, type SendMode } from '../../api/conversations';
import type { Mention } from '../../api/mentions';
import { ownRun, turnOfRun } from '../../api/debug';
import { readableError } from '../../api/http';
import { currentAnswerMode } from '../../stores/answerMode';
import { currentReplyLanguage } from '../../stores/replyLanguage';
import { truncate } from '../../utils/format';
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

const CONNECTION_TEXT = {
  connecting: '正在连接…',
  reconnecting: '连接中断，正在重连…',
  failed: '无法加载这个会话',
} as const;

interface NavigationState {
  readonly firstMessage?: FirstMessage;
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

function NewChat() {
  const navigate = useNavigate();
  const client = useQueryClient();

  const start = async (text: string, files: readonly File[], mentions: readonly Mention[]) => {
    try {
      const answerMode = currentAnswerMode();
      // @ 一起带给建会话：第一条就 @ 时不用先开再重开宿主会话
      const { id } = await createConversation(truncate(text, 30), { answerMode, mentions });
      // 附件要先有会话才能传：建会话 → 把文件放进会话工作区 → 带着附件 id 发首条消息
      const attachments = await uploadForTurn(id, files);
      await sendMessage(id, text, 'followup', {
        language: currentReplyLanguage(),
        answerMode,
        attachments,
        mentions,
      });
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      // 事件流从头回放，进入会话页后能看到回答；首条消息由本页带过去，回显之前先摆上。@ 也带过去，输入框接着用
      navigate(`/chat/${encodeURIComponent(id)}`, {
        state: { firstMessage: { text, attachments: chipsOf(files), mentions } } satisfies NavigationState,
      });
    } catch (error) {
      throw new Error(readableError(error, '没能新建对话，请稍后重试'));
    }
  };

  return (
    <div className="kw-chat">
      <div className="kw-chat-welcome">
        <h1>有什么可以帮你？</h1>
        <div className="kw-composer-dock">
          <Composer running={false} onSend={(text, _mode, files, mentions) => start(text, files, mentions)} />
        </div>
      </div>
    </div>
  );
}

function ExistingChat({ conversationId }: { readonly conversationId: string }) {
  const feedback = useFeedback(conversationId);
  const location = useLocation();
  const client = useQueryClient();
  const firstMessage = (location.state as NavigationState | null)?.firstMessage;
  const { state, connection, connectionMessage, send, cancel, decide, answer } = useConversation(
    conversationId,
    firstMessage,
  );
  const running = selectRunState(state) !== 'idle';
  const [inspecting, setInspecting] = useState<number>();
  const onInspect = useCallback((runId: string) => setInspecting(turnOfRun(runId)), []);

  const deliver = useCallback(
    async (text: string, mode: SendMode, extras: TurnExtras = {}) => {
      await send(text, mode, extras);
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
    [send, client],
  );
  const onSend = useCallback(
    (text: string, mode: SendMode, files: readonly File[], mentions: readonly Mention[]) =>
      deliver(text, running ? mode : 'followup', { files, mentions }),
    [deliver, running],
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
        <Composer
          running={running}
          onSend={onSend}
          onStop={cancel}
          initialMentions={firstMessage?.mentions}
          disabled={connection === 'failed'}
        />
      </div>
    </div>
  );
}

export default function ChatPage() {
  const { conversationId } = useParams();
  return conversationId ? <ExistingChat key={conversationId} conversationId={conversationId} /> : <NewChat />;
}
