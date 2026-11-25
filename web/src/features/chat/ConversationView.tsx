/**
 * 一个会话的视图：消息列表、运行详情与底部输入框。对话页（ChatPage）和嵌在别的页面里的会话（EmbeddedChat）共用；
 * 给了 answerMode 就按它发消息、不显示回答方式下拉。
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  warmConversation,
  type AnswerMode,
  type QuestionAnswer,
  type SendMode,
} from '../../api/conversations';
import type { SentAttachment } from '../../api/attachments';
import type { Mention } from '../../api/mentions';
import { ownRun, turnOfRun } from '../../api/debug';
import { readableError } from '../../api/http';
import { useEmbedMode } from '../embed/embedMode';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { GoalPanel } from './components/GoalPanel';
import { Composer, type ComposerDraft } from './components/Composer';
import { FileLinks } from './components/Markdown';
import { JumpToLatest } from './components/JumpToLatest';
import { MessageList } from './components/MessageList';
import { selectFileNames, selectLastUserMessage, selectRunState } from './events/selectors';
import { useConversation, type FirstMessage, type TurnExtras } from './hooks/useConversation';
import { useDiscardedBack } from './hooks/useDiscardedBack';
import { useFileLinks } from './hooks/useFileLinks';
import { useFollowLatest } from './hooks/useFollowLatest';
import { useDraftsEditable } from './hooks/useDraftsEditable';
import { PlaceLabel } from './components/PlaceSelect';
import { useFeedback } from './hooks/useFeedback';
import { resolveMentions } from './mentions/resolve';
import { SERVER_SOURCE, type ConversationSource } from './sources';
import { useGoalArmed } from './useGoalArmed';
import { turnEnded } from './turnEnds';
import { cx } from '../../ui/cx';

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
  /** 输入框的占位文字；正在跑时照样提示「补充要求或下一条消息」 */
  readonly placeholder?: string;
  /** 输入框下面那行小字（见 Composer） */
  readonly footnote?: ReactNode;
  /** 一轮跑完（运行中变回空闲）时调：嵌入的页面据此刷新自己的数据，例如 Agent 刚改了页面上显示的东西 */
  readonly onTurnEnd?: () => void;
  /** 输入框正上方的内容（例如嵌入的页面写「正在看：订单」） */
  readonly above?: ReactNode;
  /** 用户发的每一条（不含插话）发出前过一遍，例如嵌入的页面在开头带上正在看的对象 */
  readonly decorate?: (text: string) => string;
  /** 最上面一行（挂了范围的对话顶部，例如专家的头像与名字），一直在 */
  readonly top?: ReactNode;
  /** 还没问过时摆在输入框上面的一块（开场白、快捷提问），这时它和输入框一起在页面中间；ask 等同在输入框里打了这句发出去 */
  readonly intro?: (ask: (text: string) => void) => ReactNode;
  /** 挂了范围的对话：@ 候选按它的范围取（服务端收窄） */
  readonly scoped?: boolean;
  /** 输入框里显示对话模型下拉（聊天页给，嵌在别的页面里的会话不给） */
  readonly chooseModel?: boolean;
  /** 这段对话在哪执行（sources.ts）：默认业务服务。别处执行的，发消息、停止、审批交给来源，输入框按来源的 limits 置灰，
   *  输入框下面写明在哪执行；读的都是业务服务，反馈、运行详情照常有 */
  readonly source?: ConversationSource;
  /** 输入框接着用的草稿（新对话页点了输入框里的附件、为它建好对话后带过来） */
  readonly initialDraft?: ComposerDraft;
  /** 进来就在编辑器里打开的附件（输入框里还没发出去的，暂存时给的 id） */
  readonly openOnArrival?: string;
}

/**
 * 第一次连接等这么久还没连上才写「正在连接…」：一般几百毫秒就连上（照 ChatGPT，进对话时不提示连接）。
 * 原来一进会话页就先插一行、连上再拿掉，新对话发完第一句进来，刚发的那条会被顶下去再弹回来（2026-11-24 实测下移 26 像素）
 */
const CONNECTING_GRACE_MS = 1000;

/** 连接提示浮在消息区顶上（高度为 0 的贴顶层），出现、消失都不挪动下面的消息；中途断了、连不上马上写 */
function ConnectionNotice({ state, message }: { readonly state: string; readonly message?: string }) {
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (state !== 'connecting') return undefined;
    const timer = setTimeout(() => setLate(true), CONNECTING_GRACE_MS);
    return () => clearTimeout(timer);
  }, [state]);
  if (state === 'connected' || (state === 'connecting' && !late)) return null;
  const base = CONNECTION_TEXT[state as keyof typeof CONNECTION_TEXT];
  if (!base) return null;
  return (
    <div className="kw-connection-float">
      <div className="kw-connection" role="status">
        {state === 'failed' && message ? message : base}
      </div>
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
  top,
  intro,
  scoped = false,
  chooseModel = false,
  source = SERVER_SOURCE,
  initialDraft,
  openOnArrival,
}: Props) {
  const server = source.server;
  const feedback = useFeedback(conversationId);
  const client = useQueryClient();
  // 操作交给来源（云端交业务服务，本机交这台电脑），能不能在这里继续也问它；读都走业务服务
  const actions = source.actions;
  // @ 工作文件夹里的文件：只有执行的一方看得到文件夹时有（本机对话）
  const listReferences = source.files?.references;
  const references = useMemo(
    () => (listReferences ? (query: string) => listReferences(conversationId, query) : undefined),
    [listReferences, conversationId],
  );
  const sourceDrop = source.dropFiles;
  const dropFiles = useMemo(
    () => (sourceDrop ? (files: readonly File[]) => sourceDrop(files, conversationId) : undefined),
    [sourceDrop, conversationId],
  );
  const continuing = useQuery({
    queryKey: ['conversations', conversationId, 'continue', source.id],
    queryFn: () => actions.check(conversationId),
    staleTime: Infinity,
  });
  // 问不到能不能在这里继续（例如这台电脑上的本机执行环境没起来）：照不能继续处理，写出原因，不让人发出去才报错
  const blocked = continuing.isError
    ? readableError(continuing.error, '这里暂时不能继续这段对话，请稍后再试')
    : (continuing.data ?? null);
  // 回答里的文件链接：在业务服务上执行的直接读服务端；别处执行的，确认了这里能继续（别的电脑上看本机会话不行）才能点开
  const here = server || (continuing.isSuccess && continuing.data === null);
  const fileLinks = useFileLinks(conversationId, here ? source.files : undefined);
  // 输入框里还没发出去的附件：这里开着在线编辑才点得开，点了建副本、在编辑器里打开，不用先发一句话
  const draftsEditable = useDraftsEditable(here ? source.files : undefined);
  const openAttachment = fileLinks.opener?.openAttachment;
  const onOpenDraft = useMemo(
    () =>
      draftsEditable && openAttachment
        ? (attachmentId: string) => openAttachment({ attachmentId })
        : undefined,
    [draftsEditable, openAttachment],
  );
  // 新对话页为输入框里的附件建好对话带过来的：进来就打开，只打开一次
  const arrivalOpened = useRef(false);
  useEffect(() => {
    if (openOnArrival === undefined || !openAttachment || arrivalOpened.current) return;
    arrivalOpened.current = true;
    openAttachment({ attachmentId: openOnArrival });
  }, [openOnArrival, openAttachment]);
  // 消息区跟着最新的；往上翻了就不自动滚、给「回到最新」
  const { ref: scrollRef, atLatest, onScroll: onMessagesScroll, toLatest } = useFollowLatest();
  const { state, connection, connectionMessage, send, cancel, decide, answer, runCommand } = useConversation(
    conversationId,
    firstMessage,
    answerMode,
    actions,
  );
  // / 命令：执行的一方能列能执行、这里能继续这段对话时才给输入框（来源给的，云端问业务服务、本机问桌面端）
  const listCommands = actions.commands;
  const commands = useMemo(
    () =>
      listCommands && actions.runCommand && blocked === null
        ? { key: `${source.id}:${conversationId}`, list: () => listCommands(conversationId), run: runCommand }
        : undefined,
    [listCommands, actions.runCommand, blocked, source.id, conversationId, runCommand],
  );
  // 排着队随停止一起取消的消息：原文放回输入框
  useDiscardedBack(conversationId, state);
  const running = selectRunState(state) !== 'idle';
  const goalArmed = useGoalArmed(source.id, conversationId, state.goal, actions.goalArmed, running);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) {
      onTurnEnd?.();
      // 对话标题以官方为准：官方按第一句话、再用模型起标题，业务服务同步过来，一轮结束时一般已经到了，刷新列表与标题
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      // 也按对话通知扩展的来源（例如云端在线编辑一轮结束查改动，turnEnds.ts）
      turnEnded(conversationId);
    }
    wasRunning.current = running;
  }, [running, onTurnEnd, conversationId, client]);
  const [inspecting, setInspecting] = useState<number>();
  // 加了附件就预热（宿主上的对话、沙箱容器空闲半小时会被回收），一分钟内只预热一次；结果不用管
  const warmedAt = useRef(0);
  const warm = useCallback(() => {
    if (!server) return;
    const now = Date.now();
    if (now - warmedAt.current < WARM_INTERVAL_MS) return;
    warmedAt.current = now;
    void warmConversation(conversationId).catch(() => undefined);
  }, [conversationId, server]);
  // 页面上出现了文件就提前起编辑器（来源给了的话），点开时不用等它起来：对话里的附件、交付的文件，输入框里刚加的附件
  const prewarm = source.files?.prewarm;
  const onAttach = useCallback(
    (names: readonly string[]) => {
      warm();
      prewarm?.(names);
    },
    [warm, prewarm],
  );
  const fileNames = selectFileNames(state).join('\n');
  useEffect(() => {
    if (fileNames) prewarm?.(fileNames.split('\n'));
  }, [fileNames, prewarm]);
  const onInspect = useCallback((runId: string) => setInspecting(turnOfRun(runId)), []);
  // 网页嵌入页不看运行详情（embedMode.ts）
  const embedMode = useEmbedMode();
  const embedInspect = embedMode.inspect;
  const canInspect = embedInspect;

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
        toast.error(error instanceof Error ? error.message : '审批没能提交，请稍后重试');
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
        toast.error(error instanceof Error ? error.message : '回答没能提交，请稍后重试');
        return false;
      }
    },
    [answer],
  );

  const empty = connection === 'connected' && !running && selectLastUserMessage(state) === undefined;
  const ask = (text: string) => void onSend(text, 'followup', [], []);
  const opening = empty && intro !== undefined;
  // 有开场时开场和输入框一起居中（kw-chat-intro）；问过以后照常消息在上、输入框贴底，输入框在树里的位置不变、不会重新挂载
  // 外面一层左右两栏：能编辑的文件打开时编辑区并排在右边、只开文件面板时右边是一栏目录树（useFileLinks 的 pane）。这层一直在，对话这一栏在树里的位置不变，
  // 开关编辑区时输入框不重新挂载（草稿不丢）
  return (
    <div
      className={cx(
        'kw-chat-split',
        fileLinks.paneMode === 'file' && 'is-open',
        fileLinks.paneMode === 'files' && 'is-files',
      )}
    >
      <div className={opening ? 'kw-chat kw-chat-intro' : 'kw-chat'}>
        {fileLinks.tools ? <div className="kw-chat-tools">{fileLinks.tools}</div> : null}
        {top}
        <div className="kw-chat-scroll" ref={scrollRef} onScroll={onMessagesScroll}>
          <ConnectionNotice state={connection} message={connectionMessage} />
          {opening ? intro(ask) : null}
          <FileLinks opener={fileLinks.opener}>
            <MessageList
              state={state}
              onDecide={onDecide}
              rememberable={actions.remembersApprovals === true}
              onAnswer={onAnswer}
              // 这里不能继续这段对话（在别的电脑上执行）：只能看，不给重新回答
              onRegenerate={blocked === null ? onRegenerate : undefined}
              onDropConditions={blocked === null ? onDropConditions : undefined}
              onInspect={canInspect ? onInspect : undefined}
              feedback={feedback}
              onAsk={ask}
              follow={atLatest}
            />
          </FileLinks>
        </div>
        {fileLinks.preview}
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
          {atLatest ? null : <JumpToLatest onClick={toLatest} />}
          {above}
          {/* 长任务目标的进度：按钮执行的是 /goal 命令，能在这里继续这段对话时才给 */}
          {state.goal ? (
            <GoalPanel
              goal={goalArmed === undefined ? state.goal : { ...state.goal, armed: goalArmed }}
              run={commands?.run}
            />
          ) : null}
          <Composer
            running={running}
            onSend={onSend}
            onStop={cancel}
            initialMentions={initialDraft?.mentions ?? firstMessage?.mentions}
            initialDraft={initialDraft}
            onOpenDraft={blocked === null ? onOpenDraft : undefined}
            disabled={connection === 'failed' || blocked !== null}
            answerMode={answerMode}
            limits={source.limits}
            below={
              server ? undefined : (
                <div className="kw-place-row">
                  <PlaceLabel source={source} />
                  {source.Controls && blocked === null ? (
                    <source.Controls conversationId={conversationId} />
                  ) : null}
                </div>
              )
            }
            placeholder={blocked ?? (running ? undefined : placeholder)}
            footnote={footnote ?? source.footnote}
            conversationId={conversationId}
            mentionScope={scoped ? { conversationId } : undefined}
            fileReferences={references}
            dropFiles={blocked === null ? dropFiles : undefined}
            commands={commands}
            onAttach={onAttach}
            chooseModel={chooseModel}
          />
        </div>
      </div>
      {fileLinks.pane}
    </div>
  );
}
