/** 对话页：居中单栏消息列表 + 底部输入框。没有会话 id 时是新对话的欢迎页。 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import { chipsOf, type SentAttachment } from '../../api/attachments';
import type { Mention } from '../../api/mentions';
import { readableError } from '../../api/http';
import type { AnswerMode } from '../../api/conversations';
import { currentAnswerMode, effectiveAnswerMode, useAnswerMode } from '../../stores/answerMode';
import { slotItems } from '../../slots/registry';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { Composer } from './components/Composer';
import { MessageList } from './components/MessageList';
import { ConversationView } from './ConversationView';
import { chatReducer, initialChatState } from './events/reducer';
import type { ChatState } from './events/types';
import type { FirstMessage } from './hooks/useConversation';
import { startConversation } from './startConversation';

// 对话还在建时那条消息底下没有审批、提问、重新生成可点
const noop = () => undefined;
const idle = async () => undefined;
const unanswered = async () => false;

interface NavigationState {
  readonly firstMessage?: FirstMessage;
}

/**
 * 从别的页面带着回答方式和 @ 进新对话（例如数据源页的「去问」：问数 + @ 这个数据源）：
 * navigate('/chat', { state: { answerMode, mentions } })。回答方式照常是全局的选择，进来就换过去。
 */
export interface NewChatState {
  readonly answerMode?: AnswerMode;
  readonly mentions?: readonly Mention[];
}

function NewChat() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const location = useLocation();
  const preset = location.state as NewChatState | null;
  const setMode = useAnswerMode((s) => s.setMode);
  const mode = useAnswerMode((s) => effectiveAnswerMode(s.mode, s.options));
  const presetMode = preset?.answerMode;
  useEffect(() => {
    if (presetMode) setMode(presetMode);
  }, [location.key, presetMode, setMode]);
  // 按了发送、对话还在建：标题那块换成刚发的这条和「思考中」，建好跳到对话页接着显示；建不成就换回来（输入框放回文字）
  const [pending, setPending] = useState<ChatState | null>(null);

  const start = async (
    text: string,
    attachments: readonly SentAttachment[],
    mentions: readonly Mention[],
  ) => {
    setPending(
      chatReducer(initialChatState, {
        type: 'local/sent',
        localId: 'new-chat',
        text,
        mode: 'followup',
        attachments: chipsOf(attachments),
        mentions,
      }),
    );
    try {
      const { id, firstMessage } = await startConversation({
        text,
        attachments,
        mentions,
        answerMode: currentAnswerMode(),
      });
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      // 事件流从头回放，进入会话页后能看到回答；首条消息由本页带过去，回显之前先摆上。@ 也带过去，输入框接着用
      navigate(`/chat/${encodeURIComponent(id)}`, { state: { firstMessage } satisfies NavigationState });
    } catch (error) {
      setPending(null);
      throw new Error(readableError(error, '没能新建对话，请稍后重试'));
    }
  };

  // 按了发送：换成和会话页一样的样子（消息在上、输入框贴底），建好再进会话页。输入框在树里的位置两种样子都一样，
  // 不会被重新挂载：发送失败时它还能把文字和附件放回来
  return (
    <div className="kw-chat">
      <div className={pending ? 'kw-chat-starting' : 'kw-chat-welcome'}>
        {pending ? (
          <div className="kw-chat-scroll">
            <MessageList state={pending} onDecide={idle} onAnswer={unanswered} onRegenerate={noop} />
          </div>
        ) : (
          <h1>有什么可以帮你？</h1>
        )}
        <div className="kw-composer-dock">
          {/* 换一次带进来的 @ 就重新摆一次（同在新对话页时再点「去问」也算） */}
          <Composer
            key={location.key}
            running={false}
            initialMentions={preset?.mentions}
            onSend={(text, _mode, attachments, mentions) => start(text, attachments, mentions)}
          />
        </div>
        {/* 扩展的建议问题这类：点了就照这句、带着它给的 @ 开一个新对话 */}
        {(pending ? [] : slotItems('chat.welcome')).map(({ Component }, index) => (
          <Component
            key={index}
            answerMode={mode}
            ask={(text, mentions) =>
              void start(text, [], mentions).catch((error: unknown) =>
                toast.error(error instanceof Error ? error.message : '没能新建对话，请稍后重试'),
              )
            }
          />
        ))}
      </div>
    </div>
  );
}

function ExistingChat({ conversationId }: { readonly conversationId: string }) {
  const location = useLocation();
  const firstMessage = (location.state as NavigationState | null)?.firstMessage;
  return <ConversationView conversationId={conversationId} firstMessage={firstMessage} />;
}

export default function ChatPage() {
  const { conversationId } = useParams();
  return conversationId ? <ExistingChat key={conversationId} conversationId={conversationId} /> : <NewChat />;
}
