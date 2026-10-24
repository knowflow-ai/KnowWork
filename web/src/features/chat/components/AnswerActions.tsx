/** 回答下方的一排单色小图标：复制、有帮助、没帮助、重新生成、运行详情。没有正文（例如这一轮失败了）时只留运行详情。 */
import { useState, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import {
  Copy,
  Regenerate,
  Terminal,
  ThumbDown,
  ThumbDownFilled,
  ThumbUp,
  ThumbUpFilled,
} from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { toast } from '../../../ui/toastStore';
import { withoutImages } from './citations';

interface Props {
  readonly text: string;
  readonly onRegenerate?: () => void;
  readonly regenerateDisabled?: boolean;
  /** 打开这一轮的运行详情（运行调试视图）。 */
  readonly onInspect?: () => void;
  /** 排在图标后面的内容（这次回答的来源按钮）。 */
  readonly trailing?: ReactNode;
}

type Feedback = 'up' | 'down' | null;

function Action({
  label,
  children,
  pressed,
  disabled,
  onClick,
}: {
  readonly label: string;
  readonly children: ReactNode;
  readonly pressed?: boolean;
  readonly disabled?: boolean;
  readonly onClick: () => void;
}) {
  return (
    <Tooltip content={label} compact>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label={label}
        aria-pressed={pressed}
        disabled={disabled}
        onClick={onClick}
      >
        {children}
      </Button>
    </Tooltip>
  );
}

export function AnswerActions({ text, onRegenerate, regenerateDisabled, onInspect, trailing }: Props) {
  const [feedback, setFeedback] = useState<Feedback>(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(withoutImages(text));
      toast.success('已复制');
    } catch {
      toast.error('复制没有成功，请手动选中文字复制');
    }
  };

  const toggle = (value: Exclude<Feedback, null>) => {
    const next = feedback === value ? null : value;
    setFeedback(next);
    if (next) toast.success('感谢反馈');
  };

  const inspect = onInspect ? (
    <Action label="运行详情" onClick={onInspect}>
      <Terminal />
    </Action>
  ) : null;
  if (!text) return inspect ? <div className="kw-answer-actions">{inspect}</div> : null;

  return (
    <div className="kw-answer-actions">
      <Action label="复制" onClick={() => void copy()}>
        <Copy />
      </Action>
      <Action label="有帮助" pressed={feedback === 'up'} onClick={() => toggle('up')}>
        {feedback === 'up' ? <ThumbUpFilled /> : <ThumbUp />}
      </Action>
      <Action label="没帮助" pressed={feedback === 'down'} onClick={() => toggle('down')}>
        {feedback === 'down' ? <ThumbDownFilled /> : <ThumbDown />}
      </Action>
      {onRegenerate ? (
        <Action label="重新生成" disabled={regenerateDisabled} onClick={onRegenerate}>
          <Regenerate />
        </Action>
      ) : null}
      {inspect}
      {trailing}
    </div>
  );
}
