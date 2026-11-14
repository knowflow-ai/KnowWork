/**
 * 对话页：居中单栏消息列表 + 底部输入框。没有会话 id 时是新对话的欢迎页。
 * 对话都在业务服务里、都在 /chat/<编号>。在哪执行由来源定（sources.ts）：新对话页在输入框下面选（照 ChatGPT、Claude 的桌面端，
 * 默认云端，只有云端可选时不显示）；会话页按这段对话自己的 source 字段——它只决定发消息、停止、审批交给谁，读的都是业务服务。
 */
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import { chipsOf, type SentAttachment } from '../../api/attachments';
import type { Mention } from '../../api/mentions';
import { readableError } from '../../api/http';
import {
  getConversation,
  getConversationScope,
  type AnswerMode,
  type ConversationScope,
} from '../../api/conversations';
import { currentAnswerMode, effectiveAnswerMode, useAnswerMode } from '../../stores/answerMode';
import { NEW_CHAT, useChatModel } from '../../stores/chatModel';
import { slotItems } from '../../slots/registry';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { Composer, DEFAULT_FOOTNOTE } from './components/Composer';
import { MessageList } from './components/MessageList';
import { ConversationView } from './ConversationView';
import { chatReducer, initialChatState } from './events/reducer';
import type { ChatState } from './events/types';
import type { FirstMessage } from './hooks/useConversation';
import { useStartPlace } from './hooks/useStartPlace';
import { SERVER_SOURCE, sourceOf, type ConversationSource } from './sources';

// 对话还在建时那条消息底下没有审批、提问、重新生成可点
const noop = () => undefined;
const idle = async () => undefined;
const unanswered = async () => false;

interface NavigationState {
  readonly firstMessage?: FirstMessage;
  /** 刚建的对话在哪执行（新对话页带过来，省得会话页再问一次才知道操作交给谁） */
  readonly source?: string;
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
  // 按了发送、对话还在建：标题那块换成刚发的这条和「思考中」，建好跳到对话页接着显示；建不成就换回来（输入框放回文字）
  const [pending, setPending] = useState<ChatState | null>(null);
  // 在哪执行（和扩展的新对话页共用）：来源说还不能发（例如还没选工作文件夹）时输入框不可用，占位文字写原因
  const { source, blocker, below } = useStartPlace(pending !== null);
  const sourceDrop = source.dropFiles;
  const dropFiles = useMemo(
    () => (sourceDrop ? (files: readonly File[]) => sourceDrop(files, null) : undefined),
    [sourceDrop],
  );
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
      const { id, firstMessage } = await source.start({
        text,
        attachments,
        mentions,
        answerMode: source.limits?.answerMode?.value ?? currentAnswerMode(),
      });
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      // 新对话页上选的模型带到这段对话：首条消息就用它（在哪执行都一样，本机宿主不认识时桌面端会问业务服务要清单）
      useChatModel.getState().carry(NEW_CHAT, id);
      // 事件流从头回放，进入会话页后能看到回答；首条消息由本页带过去，回显之前先摆上。@ 也带过去，输入框接着用
      navigate(`/chat/${encodeURIComponent(id)}`, {
        state: { firstMessage, source: source.id } satisfies NavigationState,
      });
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
          <h1>{source.welcome ?? '有什么可以帮你？'}</h1>
        )}
        <div className="kw-composer-dock">
          {/* 换一次带进来的 @ 就重新摆一次（同在新对话页时再点「去问」也算）。换执行位置不重新挂载输入框：打的字和附件都留着 */}
          <Composer
            key={location.key}
            chooseModel
            running={false}
            initialMentions={preset?.mentions}
            limits={source.limits}
            disabled={blocker !== null}
            placeholder={blocker ?? undefined}
            footnote={source.footnote}
            below={below}
            dropFiles={dropFiles}
            onSend={(text, _mode, attachments, mentions) => start(text, attachments, mentions)}
          />
        </div>
        {/* 扩展的建议问题这类：点了就照这句、带着它给的 @ 开一个新对话（服务端会话才有） */}
        {(pending || !source.server ? [] : slotItems('chat.welcome')).map(({ Component }, index) => (
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

/** 挂了范围的对话：顶部、开场、输入框下面那行字由扩展画（chat.scope），没登记顶部时只写范围的名字；占位文字写「问 xx…」。 */
function scopeParts(conversationId: string, scope: ConversationScope) {
  const items = slotItems('chat.scope');
  const bars = items.flatMap(({ Bar }) => (Bar ? [Bar] : []));
  const intros = items.flatMap(({ Intro }) => (Intro ? [Intro] : []));
  const notes = items.flatMap(({ Footnote }) => (Footnote ? [Footnote] : []));
  return {
    scoped: true,
    placeholder: `问${scope.label}…`,
    top: bars.length ? (
      bars.map((Bar, index) => <Bar key={index} conversationId={conversationId} label={scope.label} />)
    ) : (
      <div className="kw-chat-scope">{scope.label}</div>
    ),
    intro: intros.length
      ? (ask: (text: string) => void) =>
          intros.map((Intro, index) => <Intro key={index} conversationId={conversationId} ask={ask} />)
      : undefined,
    footnote: notes.length
      ? notes.map((Footnote, index) => (
          <Footnote key={index} conversationId={conversationId} fallback={DEFAULT_FOOTNOTE} />
        ))
      : undefined,
  };
}

/** 还不知道在哪执行时：收发照业务服务处理（见 ExistingChat），文件先不给 */
const PENDING_SOURCE: ConversationSource = { ...SERVER_SOURCE, files: undefined };

function ExistingChat({ conversationId }: { readonly conversationId: string }) {
  const location = useLocation();
  const arrived = location.state as NavigationState | null;
  const firstMessage = arrived?.firstMessage;
  // 这段对话在哪执行：刚建的由新对话页带过来，别的问业务服务。还不知道（或问不到）时按业务服务处理：读的本来就是它，
  // 这一小会儿里真发了消息，在别处执行的对话业务服务会拒绝并说明原因
  const known = useQuery({
    queryKey: ['conversations', conversationId, 'summary'],
    queryFn: () => getConversation(conversationId),
    staleTime: Infinity,
    enabled: arrived?.source === undefined,
  });
  const sourceId = arrived?.source ?? known.data?.source;
  // 文件这类按执行位置走的能力等确认了在哪执行再给：不然在别处执行的对话里，文件链接会先按业务服务点得开
  const sourceKnown = arrived?.source !== undefined || known.isFetched;
  const source = sourceKnown ? sourceOf(sourceId) : PENDING_SOURCE;
  // 扩展给这段对话定的范围（例如挂着专家）：固定了回答方式就按它、不让换；取不到（接口出错）按没有范围，发消息时服务端照样按
  // 范围来。不管在哪执行，范围都在业务服务那边（本机会话每一轮也是业务服务按它准备的）
  const scope = useQuery({
    queryKey: ['conversations', conversationId, 'scope'],
    queryFn: () => getConversationScope(conversationId),
    staleTime: Infinity,
  }).data;
  return (
    <ConversationView
      conversationId={conversationId}
      source={source}
      chooseModel
      firstMessage={firstMessage}
      answerMode={scope?.answerMode ?? undefined}
      {...(scope ? scopeParts(conversationId, scope) : {})}
    />
  );
}

export default function ChatPage() {
  const { conversationId } = useParams();
  return conversationId ? <ExistingChat key={conversationId} conversationId={conversationId} /> : <NewChat />;
}
