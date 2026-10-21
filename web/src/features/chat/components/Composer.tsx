/**
 * 底部输入框，照 ChatGPT：大圆角白框，上面是待发的附件，中间是会随内容长高的输入区，
 * 底部一行左边是附件与技能，右边是停止与发送（深色圆形按钮）。附件可以点回形针选，也可以拖进来。
 * 运行中可以插话：回车 = 完成后发送（followup），Ctrl/⌘ + 回车 = 补充到当前任务（steer）；显示停止按钮。
 */
import { useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ArrowUp, Bolt, PaperclipAttach, StopSm } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { validateMessage, type SendMode } from '../../../api/conversations';
import { chipsOf } from '../../../api/attachments';
import { cx } from '../../../ui/cx';
import { toast } from '../../../ui/toastStore';
import { useDraftFiles } from '../hooks/useDraftFiles';
import { AttachmentChips } from './AttachmentChips';
import { modeForKey } from './keys';

export interface SkillOption {
  readonly key: string;
  readonly name: string;
  readonly description?: string;
}

interface Props {
  readonly running: boolean;
  readonly onSend: (text: string, mode: SendMode, files: readonly File[]) => Promise<void>;
  readonly onStop?: () => Promise<void>;
  readonly skills?: readonly SkillOption[];
  readonly disabled?: boolean;
  readonly placeholder?: string;
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

function SkillMenu({
  skills,
  onPick,
}: {
  readonly skills: readonly SkillOption[];
  readonly onPick: (skill: SkillOption) => void;
}) {
  const trigger = (
    <Button color="secondary" variant="ghost" uniform aria-label="选择技能" disabled={skills.length === 0}>
      <Bolt />
    </Button>
  );
  if (skills.length === 0) {
    return (
      <Tooltip content="还没有可用的技能" compact>
        <span>{trigger}</span>
      </Tooltip>
    );
  }
  return (
    <Menu>
      <Menu.Trigger>{trigger}</Menu.Trigger>
      <Menu.Content side="top" align="start" maxHeight={360}>
        {skills.map((skill) => (
          <Menu.Item key={skill.key} onSelect={() => onPick(skill)}>
            <div className="kw-skill-option">
              <div>{skill.name}</div>
              {skill.description ? <div className="kw-skill-option-desc">{skill.description}</div> : null}
            </div>
          </Menu.Item>
        ))}
      </Menu.Content>
    </Menu>
  );
}

export function Composer({ running, onSend, onStop, skills = [], disabled, placeholder }: Props) {
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [dragging, setDragging] = useState(false);
  const draft = useDraftFiles();
  const picker = useRef<HTMLInputElement>(null);
  const input = useAutoHeight(value);

  const addFiles = (incoming: readonly File[]) => {
    const problem = draft.add(incoming);
    if (problem) toast.warning(problem);
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!disabled) addFiles(Array.from(event.dataTransfer.files));
  };

  const submit = async (mode: SendMode) => {
    const problem = validateMessage(value);
    if (problem) {
      toast.warning(problem);
      return;
    }
    setSending(true);
    try {
      await onSend(value, mode, draft.files);
      setValue('');
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
    const mode = modeForKey({ ...event, isComposing: event.nativeEvent.isComposing }, running);
    if (!mode) return;
    event.preventDefault();
    if (!sending) void submit(mode);
  };

  return (
    <div className="kw-composer">
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
        <textarea
          ref={input}
          className="kw-composer-input"
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={placeholder ?? (running ? '补充要求或下一条消息' : '有问题，尽管问')}
          aria-label="消息"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="kw-composer-bar">
          <Tooltip content="添加文件" compact>
            <Button
              color="secondary"
              variant="ghost"
              uniform
              aria-label="添加附件"
              disabled={disabled}
              onClick={() => picker.current?.click()}
            >
              <PaperclipAttach />
            </Button>
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
          <SkillMenu skills={skills} onPick={(skill) => setValue((v) => `/${skill.name} ${v}`)} />
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
      <div className="kw-composer-hint">
        {running ? '回车：完成后发送 · Ctrl/⌘ + 回车：补充到当前任务 · ' : ''}
        回答可能有误，重要结论请点开出处核对
      </div>
    </div>
  );
}
