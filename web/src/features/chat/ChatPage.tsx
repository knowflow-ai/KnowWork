/** 对话页：居中单栏消息列表 + 底部输入框。没有会话 id 时是新对话的欢迎页。 */
import { lazy, Suspense, useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import { chipsOf, uploadForTurn } from '../../api/attachments';
import { createConversation, sendMessage, type QuestionAnswer, type SendMode } from '../../api/conversations';
import { ownRun, turnOfRun } from '../../api/debug';
import { readableError } from '../../api/http';
import { currentReplyLanguage } from '../../stores/replyLanguage';
import { truncate } from '../../utils/format';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { Composer } from './components/Composer';
import { MessageList } from './components/MessageList';
import { selectLastUserText, selectRunState } from './events/selectors';
import { useConversation, type FirstMessage } from './hooks/useConversation';
import { useSkillOptions } from './hooks/useSkillOptions';

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
  const skills = useSkillOptions();

  const start = async (text: string, files: readonly File[]) => {
    try {
      const { id } = await createConversation(truncate(text, 30));
      // 附件要先有会话才能传：建会话 → 把文件放进会话工作区 → 带着附件 id 发首条消息
      const attachments = await uploadForTurn(id, files);
      await sendMessage(id, text, 'followup', currentReplyLanguage(), attachments);
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      // 事件流从头回放，进入会话页后能看到回答；首条消息由本页带过去，回显之前先摆上
      navigate(`/chat/${encodeURIComponent(id)}`, {
        state: { firstMessage: { text, attachments: chipsOf(files) } } satisfies NavigationState,
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
          <Composer running={false} onSend={(text, _mode, files) => start(text, files)} skills={skills} />
        </div>
      </div>
    </div>
  );
}

function ExistingChat({ conversationId }: { readonly conversationId: string }) {
  const skills = useSkillOptions();
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
    async (text: string, mode: SendMode, files: readonly File[] = []) => {
      await send(text, mode, files);
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
    },
    [send, client],
  );
  const onSend = useCallback(
    (text: string, mode: SendMode, files: readonly File[]) =>
      deliver(text, running ? mode : 'followup', files),
    [deliver, running],
  );
  const onRegenerate = useCallback(() => {
    const last = selectLastUserText(state);
    if (!last) return;
    deliver(last, 'followup').catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : '没能重新生成，请稍后重试');
    });
  }, [deliver, state]);
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
          onInspect={onInspect}
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
          skills={skills}
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
