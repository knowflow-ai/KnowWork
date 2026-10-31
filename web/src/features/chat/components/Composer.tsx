/**
 * 底部输入框，照 ChatGPT：大圆角白框，上面是待发的附件和 @ 的小标签，中间是会随内容长高的输入区，
 * 底部一行左边是附件与 @，右边是停止与发送（深色圆形按钮）。附件可以点回形针选，也可以拖进来。
 * 运行中可以插话：回车 = 完成后发送（followup），Ctrl/⌘ + 回车 = 补充到当前任务（steer）；显示停止按钮。
 * 打 @（或点 @ 按钮）弹出点名菜单（mentions/），选中的变成小标签；发出去以后小标签留着，这个对话后面每一问都带上，
 * 直到手动去掉。
 * 框外左上角是回答方式（智能体 / 快速问答，见 AnswerModeSelect）；左下角的「+」添加文件。快速问答只查知识库（检索与阅读），
 * 不能加文件、@ 菜单不列技能，@ 过的技能先不显示也不发，切回智能体还在。
 */
import { useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ArrowUp, AtSign, PlusComposer, StopSm } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { validateMessage, type SendMode } from '../../../api/conversations';
import { chipsOf } from '../../../api/attachments';
import { mentionKey, type Mention } from '../../../api/mentions';
import { useAnswerMode } from '../../../stores/answerMode';
import { cx } from '../../../ui/cx';
import { toast } from '../../../ui/toastStore';
import { useDraftFiles } from '../hooks/useDraftFiles';
import { activeTrigger, withoutTrigger } from '../mentions/candidates';
import { MentionChips } from '../mentions/MentionChips';
import { MentionMenu } from '../mentions/MentionMenu';
import { activeOption, MENTION_MENU_ID, useMentionMenu } from '../mentions/useMentionMenu';
import { AnswerModeSelect } from './AnswerModeSelect';
import { AttachmentChips } from './AttachmentChips';
import { modeForKey } from './keys';

interface Props {
  readonly running: boolean;
  readonly onSend: (
    text: string,
    mode: SendMode,
    files: readonly File[],
    mentions: readonly Mention[],
  ) => Promise<void>;
  readonly onStop?: () => Promise<void>;
  /** 进入会话页时接着用新对话页上选的 @ */
  readonly initialMentions?: readonly Mention[];
  readonly disabled?: boolean;
  readonly placeholder?: string;
}

const NO_FILES_IN_QUICK = '快速问答不能带附件，去掉附件或切换到智能体再发';

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

export function Composer({ running, onSend, onStop, initialMentions = [], disabled, placeholder }: Props) {
  const [value, setValue] = useState('');
  const [caret, setCaret] = useState(0);
  const [mentions, setMentions] = useState<readonly Mention[]>(initialMentions);
  // Esc 关掉菜单后，同一个 @ 不再弹出，直到打下一个
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [dragging, setDragging] = useState(false);
  const draft = useDraftFiles();
  const quick = useAnswerMode((s) => s.mode) === 'quick';
  // 这一问算数的 @：快速问答用不了技能
  const scoped = quick ? mentions.filter((m) => m.type !== 'skill') : mentions;
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
    if (quick && draft.files.length > 0) {
      toast.warning(NO_FILES_IN_QUICK);
      return;
    }
    setSending(true);
    try {
      await onSend(value, mode, draft.files, scoped);
      setValue('');
      setCaret(0);
      draft.clear();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '消息没有发出去，请稍后重试');
    } finally {
      setSending(false);
    }
  };

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
    <div className="kw-composer">
      <div className="kw-composer-top">
        <AnswerModeSelect disabled={disabled} />
      </div>
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
          {draft.files.length > 0 ? (
            <AttachmentChips items={chipsOf(draft.files)} onRemove={draft.remove} label="待发送的附件" />
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
            <Tooltip content={quick ? '@ 点名知识库、文件或标签' : '@ 点名知识库、文件、标签或技能'} compact>
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
        回答可能有误，重要结论请点开出处核对
      </div>
    </div>
  );
}
