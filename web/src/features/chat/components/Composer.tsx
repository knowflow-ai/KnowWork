/**
 * 底部输入框，照 ChatGPT：大圆角白框，上面是待发的附件和 @ 的小标签，中间是会随内容长高的输入区，
 * 底部一行左边是附件与 @，右边是对话模型下拉（ModelSelect，聊天页才有）、停止与发送（深色圆形按钮）。附件可以点回形针选，
 * 也可以拖进来。
 * 运行中可以插话：回车 = 完成后发送（followup），Ctrl/⌘ + 回车 = 补充到当前任务（steer）；显示停止按钮。
 * 打 @（或点 @ 按钮）弹出点名菜单（mentions/），选中的变成小标签；发出去以后小标签留着，这个对话后面每一问都带上，
 * 直到手动去掉。
 * 框外左上角是回答方式（智能体 / 快速问答，见 AnswerModeSelect）；左下角的「+」添加文件。快速问答只查知识库（检索与阅读），
 * 不能加文件、@ 菜单不列技能，@ 过的技能先不显示也不发，切回智能体还在。
 * 附件一加进来就传上去、开始解析（useDraftAttachments），打字的工夫多半就好了；点发送时还没好的先等着（卡片上显示
 * 上传中、解析中），好了自动发出去，这一轮模型就拿得到解析出的全文。等不及解析可以点「直接发送」。
 */
import { useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ArrowUp, AtSign, PlusComposer, StopSm } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { validateMessage, type AnswerMode, type SendMode } from '../../../api/conversations';
import type { AttachmentChip, SentAttachment } from '../../../api/attachments';
import { mentionKey, type Mention } from '../../../api/mentions';
import { effectiveAnswerMode, useAnswerMode } from '../../../stores/answerMode';
import { slotItems } from '../../../slots/registry';
import { cx } from '../../../ui/cx';
import { toast } from '../../../ui/toastStore';
import { sendable, useDraftAttachments, type DraftAttachment } from '../hooks/useDraftAttachments';
import { activeTrigger, withoutTrigger } from '../mentions/candidates';
import { MentionChips } from '../mentions/MentionChips';
import { MentionMenu } from '../mentions/MentionMenu';
import { suggestMode } from '../mentions/suggest';
import {
  activeOption,
  MENTION_MENU_ID,
  MentionScopeContext,
  useMentionMenu,
  useMentionOptions,
  type MentionScope,
} from '../mentions/useMentionMenu';
import { AnswerModeSelect } from './AnswerModeSelect';
import { ModelSelect } from './ModelSelect';
import { AttachmentChips } from './AttachmentChips';
import { modeForKey } from './keys';

interface Props {
  readonly running: boolean;
  readonly onSend: (
    text: string,
    mode: SendMode,
    attachments: readonly SentAttachment[],
    mentions: readonly Mention[],
  ) => Promise<void>;
  readonly onStop?: () => Promise<void>;
  /** 进入会话页时接着用新对话页上选的 @ */
  readonly initialMentions?: readonly Mention[];
  readonly disabled?: boolean;
  readonly placeholder?: string;
  /** 输入框下面那行小字；不给就是 DEFAULT_FOOTNOTE */
  readonly footnote?: ReactNode;
  /** 固定的回答方式（嵌在别的页面里的会话，例如扩展的建模面板）：给了就不显示回答方式下拉，按它判断能不能带附件 */
  readonly answerMode?: AnswerMode;
  /** 已有会话的 id（新对话没有）：给左上角那一行里扩展的组件用 */
  readonly conversationId?: string;
  /** 加了附件（快要发消息了）：已有会话借此预热，发送时不用现开宿主上的对话、现建沙箱容器 */
  readonly onAttach?: () => void;
  /** @ 候选按谁的范围取（见 MentionScope）：挂了范围的对话给对话 id，还没建对话的专家新对话页给收窄；不给按本人能用的全部 */
  readonly mentionScope?: MentionScope;
  /** 在发送按钮左边显示对话模型下拉（ModelSelect）：聊天页给，嵌在别的页面里的会话不给（沿用默认模型） */
  readonly chooseModel?: boolean;
}

const NO_SCOPE: MentionScope = {};
export const DEFAULT_FOOTNOTE = '回答可能有误，重要结论请点开出处核对';
const NO_FILES_IN_QUICK = '快速问答不能带附件，去掉附件或切换到智能体再发';
const UNPARSED = '有文件没能解析，已按原文件交给模型';
const SETTLE_POLL_MS = 200;

const NOTES: Partial<Record<DraftAttachment['state'], Pick<AttachmentChip, 'note' | 'busy'>>> = {
  uploading: { note: '上传中', busy: true },
  parsing: { note: '解析中', busy: true },
  failed: { note: '没能解析' },
};

/** 长文档解析完还在生成导读：写到第几段 */
const digestNote = (progress?: readonly [number, number]) =>
  progress ? `生成导读 ${progress[0]}/${progress[1]}` : '生成导读中';

const chipOf = (item: DraftAttachment): AttachmentChip => ({
  name: item.name,
  size: item.size,
  ...(item.state === 'digesting' ? { note: digestNote(item.progress), busy: true } : NOTES[item.state]),
});

const waitsOn = (item: DraftAttachment) => item.state === 'parsing' || item.state === 'digesting';

const WAITING_TEXT = {
  parsing: '解析完自动发送',
  digesting: '导读生成完自动发送',
  uploading: '上传完自动发送',
} as const;

/** 点了发送以后在等什么：解析、导读（点了「直接发送」就不等这两样）、上传。 */
function waitingOn(items: readonly DraftAttachment[], skipped: boolean): keyof typeof WAITING_TEXT | null {
  if (!skipped && items.some((item) => item.state === 'parsing')) return 'parsing';
  if (!skipped && items.some((item) => item.state === 'digesting')) return 'digesting';
  if (items.some((item) => item.state === 'uploading')) return 'uploading';
  return null;
}

/**
 * 等附件传完、解析完、长文档的导读做完（点了「直接发送」就不等解析和导读，传还是要等完，不然没有 id）；
 * 返回那一刻的列表。
 */
async function settle(
  latest: () => readonly DraftAttachment[],
  skip: AbortSignal,
): Promise<readonly DraftAttachment[]> {
  for (;;) {
    const items = latest();
    const busy = items.some((item) => item.state === 'uploading' || (waitsOn(item) && !skip.aborted));
    if (!busy) return items;
    await new Promise((resolve) => setTimeout(resolve, SETTLE_POLL_MS));
  }
}

/** 输入区最多长到 8 行（行高 24），再多就在框内滚动。 */
const MAX_HEIGHT = 24 * 8;

/** 输入区随内容长高：先清掉高度量出内容高，再设回去。 */
function useAutoHeight(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);
  return ref;
}

export function Composer({
  running,
  onSend,
  onStop,
  initialMentions = [],
  disabled,
  placeholder,
  footnote = DEFAULT_FOOTNOTE,
  answerMode,
  conversationId,
  onAttach,
  mentionScope = NO_SCOPE,
  chooseModel = false,
}: Props) {
  const [value, setValue] = useState('');
  const [caret, setCaret] = useState(0);
  const [mentions, setMentions] = useState<readonly Mention[]>(initialMentions);
  // Esc 关掉菜单后，同一个 @ 不再弹出，直到打下一个
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  // 点了发送、正等附件传完解析完时，点「直接发送」就不等解析了
  const skipWaiting = useRef<AbortController | null>(null);
  const [skipped, setSkipped] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [dragging, setDragging] = useState(false);
  const draft = useDraftAttachments();
  const chosen = useAnswerMode((s) => s.mode);
  const modes = useAnswerMode((s) => s.options);
  const setMode = useAnswerMode((s) => s.setMode);
  const scope = mentionScope;
  const { options: offered } = useMentionOptions(scope);
  const quick = (answerMode ?? chosen) === 'quick';
  // 这一问算数的 @：快速问答用不了技能
  const scoped = quick ? mentions.filter((m) => m.type !== 'skill') : mentions;
  // @ 了在别的回答方式里效果更好的东西（例如数据源）：提示切过去；固定了回答方式的不提示
  const suggestion = answerMode
    ? null
    : suggestMode(scoped, offered.extensions, effectiveAnswerMode(chosen, modes), modes);
  const picker = useRef<HTMLInputElement>(null);
  const input = useAutoHeight(value);
  const found = activeTrigger(value, caret);
  const trigger = found && found.start !== dismissed ? found : null;
  const placeCaret = (position: number) => {
    setCaret(position);
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(position, position);
    });
  };
  const menu = useMentionMenu(
    trigger,
    (mention) => {
      if (!trigger) return;
      setMentions((current) =>
        current.some((m) => mentionKey(m) === mentionKey(mention)) ? current : [...current, mention],
      );
      setValue(withoutTrigger(value, trigger, caret));
      placeCaret(trigger.start);
    },
    () => setDismissed(found?.start ?? null),
    !quick,
    scope,
  );
  const openMention = () => {
    // 点 @ 按钮：在光标处补一个 @（前面不是空白就先空一格），菜单随之弹出
    const at = input.current?.selectionStart ?? value.length;
    const gap = at > 0 && !/\s/.test(value[at - 1] ?? '') ? ' ' : '';
    setValue(`${value.slice(0, at)}${gap}@${value.slice(at)}`);
    setDismissed(null);
    placeCaret(at + gap.length + 1);
  };

  const addFiles = (incoming: readonly File[]) => {
    const problem = draft.add(incoming);
    if (problem) toast.warning(problem);
    if (incoming.length > 0) onAttach?.();
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    if (quick) toast.warning(NO_FILES_IN_QUICK);
    else addFiles(Array.from(event.dataTransfer.files));
  };

  const submit = async (mode: SendMode) => {
    const problem = validateMessage(value);
    if (problem) {
      toast.warning(problem);
      return;
    }
    if (quick && draft.items.length > 0) {
      toast.warning(NO_FILES_IN_QUICK);
      return;
    }
    setSending(true);
    const skip = new AbortController();
    skipWaiting.current = skip;
    try {
      const ready = await settle(draft.latest, skip.signal);
      // 发出去这一刻就清空（消息已经摆进对话、显示「思考中」），不等发送接口返回：接口要认领附件、可能还要重开宿主上的
      // 对话、现建沙箱容器，冷的时候两三秒，输入框一直留着像没发出去。发送失败再把文字和附件放回来
      const text = value;
      const items = draft.latest();
      setValue('');
      setCaret(0);
      draft.clear();
      try {
        await onSend(text, mode, sendable(ready), scoped);
      } catch (error) {
        setValue((current) => current || text);
        draft.restore(items);
        throw error;
      }
      if (ready.some((item) => item.state === 'failed')) toast.warning(UNPARSED);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '消息没有发出去，请稍后重试');
    } finally {
      setSending(false);
      setSkipped(false);
      skipWaiting.current = null;
    }
  };
  const skipParsing = () => {
    skipWaiting.current?.abort();
    setSkipped(true);
  };
  // 点了发送、还在等附件：说一句在等什么；等的是解析、导读就给「直接发送」
  const waitingFor = !sending ? null : waitingOn(draft.items, skipped);

  const stop = async () => {
    if (!onStop) return;
    setStopping(true);
    try {
      await onStop();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '没能停止，请稍后重试');
    } finally {
      setStopping(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (menu.onKey(event)) {
      event.preventDefault();
      return;
    }
    const mode = modeForKey({ ...event, isComposing: event.nativeEvent.isComposing }, running);
    if (!mode) return;
    event.preventDefault();
    if (!sending) void submit(mode);
  };

  return (
    <MentionScopeContext.Provider value={scope}>
      <div className="kw-composer">
        {answerMode ? null : (
          <div className="kw-composer-top">
            <AnswerModeSelect disabled={disabled} />
            {slotItems('chat.composer.top').map(({ Component }, index) => (
              <Component
                key={index}
                conversationId={conversationId}
                answerMode={effectiveAnswerMode(chosen, modes)}
                running={running}
              />
            ))}
          </div>
        )}
        {suggestion ? (
          <div className="kw-composer-suggest" role="status">
            <span>
              @ 了{suggestion.kind}，切换到「{suggestion.mode.label}」回答会更准
            </span>
            <Button
              color="secondary"
              variant="ghost"
              size="xs"
              disabled={disabled}
              onClick={() => setMode(suggestion.mode.id)}
            >
              切换
            </Button>
          </div>
        ) : null}
        {/* @ 菜单贴着输入框弹出，不算上面那一行 */}
        <div className="kw-composer-main">
          <MentionMenu menu={menu} />
          <div
            className={cx('kw-composer-box', dragging && 'is-dragging', disabled && 'is-disabled')}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={(event) => {
              // 点框里的空白处也能开始输入，和 ChatGPT 一样
              if (event.target === event.currentTarget) input.current?.focus();
            }}
          >
            {draft.items.length > 0 ? (
              // 点了发送以后不能移除：要发出去的就是这几个
              <AttachmentChips
                items={draft.items.map(chipOf)}
                onRemove={sending ? undefined : (index) => draft.remove(draft.items[index]?.key ?? '')}
                label="待发送的附件"
              />
            ) : null}
            {waitingFor ? (
              <div className="kw-composer-waiting">
                <span>{WAITING_TEXT[waitingFor]}</span>
                {waitingFor !== 'uploading' ? (
                  <Button color="secondary" variant="ghost" size="2xs" onClick={skipParsing}>
                    不等了，直接发送
                  </Button>
                ) : null}
              </div>
            ) : null}
            <MentionChips
              items={scoped}
              label="这一问 @ 点名的内容"
              onRemove={(mention) =>
                setMentions((current) => current.filter((m) => mentionKey(m) !== mentionKey(mention)))
              }
            />
            <textarea
              ref={input}
              className="kw-composer-input"
              rows={1}
              value={value}
              disabled={disabled}
              placeholder={
                placeholder ?? (running ? '补充要求或下一条消息' : quick ? '在知识库里查…' : '有问题，尽管问')
              }
              aria-label="消息"
              aria-controls={menu.open ? MENTION_MENU_ID : undefined}
              aria-activedescendant={activeOption(menu)}
              onChange={(event) => {
                setValue(event.target.value);
                setCaret(event.target.selectionStart);
              }}
              onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
              onBlur={() => setDismissed(found?.start ?? null)}
              onFocus={() => setDismissed(null)}
              onKeyDown={onKeyDown}
            />
            <div className="kw-composer-bar">
              {/* 快速问答没有读文件的工具，附件放进去也没人读 */}
              <Tooltip content={quick ? '快速问答不能带附件' : '添加文件'} compact>
                <span>
                  <Button
                    color="secondary"
                    variant="ghost"
                    uniform
                    aria-label="添加文件"
                    disabled={disabled || quick}
                    onClick={() => picker.current?.click()}
                  >
                    <PlusComposer />
                  </Button>
                </span>
              </Tooltip>
              <input
                ref={picker}
                type="file"
                multiple
                hidden
                aria-label="选择文件"
                onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = '';
                }}
              />
              <Tooltip
                content={quick ? '@ 点名知识库、文件或标签' : '@ 点名知识库、文件、标签或技能'}
                compact
              >
                <Button
                  color="secondary"
                  variant="ghost"
                  uniform
                  aria-label="@ 点名"
                  disabled={disabled}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={openMention}
                >
                  <AtSign />
                </Button>
              </Tooltip>
              <span className="kw-composer-spacer" />
              {chooseModel ? <ModelSelect conversationId={conversationId} disabled={disabled} /> : null}
              {running && onStop ? (
                <Tooltip content="停止" compact>
                  <Button
                    color="secondary"
                    variant="outline"
                    uniform
                    aria-label="停止"
                    loading={stopping}
                    onClick={() => void stop()}
                  >
                    <StopSm />
                  </Button>
                </Tooltip>
              ) : null}
              <Tooltip content={running ? '完成后发送' : '发送'} compact>
                <Button
                  color="primary"
                  uniform
                  aria-label="发送"
                  loading={sending}
                  disabled={!value.trim() || disabled}
                  onClick={() => void submit('followup')}
                >
                  <ArrowUp />
                </Button>
              </Tooltip>
            </div>
          </div>
        </div>
        <div className="kw-composer-hint">
          {running ? '回车：完成后发送 · Ctrl/⌘ + 回车：补充到当前任务 · ' : ''}
          {footnote}
        </div>
      </div>
    </MentionScopeContext.Provider>
  );
}
