/**
 * 回答下方的一排单色小图标：复制、有帮助、没帮助、重新生成、运行详情。没有正文（例如这一轮失败了）时只留运行详情。
 * 有帮助、没帮助照 DSH 官方的消息反馈：点了打开反馈弹窗，提交后才记下；再点已选的那个直接撤回，不打开弹窗。
 */
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
import type { FeedbackInput, Rating } from '../../../api/feedback';
import { toast } from '../../../ui/toastStore';
import { withoutImages } from './citations';
import { FeedbackDialog } from './FeedbackDialog';

interface Props {
  readonly text: string;
  readonly onRegenerate?: () => void;
  readonly regenerateDisabled?: boolean;
  /** 打开这一轮的运行详情（运行调试视图）。 */
  readonly onInspect?: () => void;
  /** 排在图标后面的内容（这次回答的来源按钮）。 */
  readonly trailing?: ReactNode;
  /** 这一轮已记下的评分；没给反馈的处理（比如还没有会话）时不显示赞、踩。 */
  readonly feedback?: Rating | null;
  /** 返回是否成功（失败已提示）。 */
  readonly onFeedback?: (input: FeedbackInput) => Promise<boolean>;
  readonly onRetract?: () => Promise<boolean>;
}

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

export function AnswerActions({
  text,
  onRegenerate,
  regenerateDisabled,
  onInspect,
  trailing,
  feedback,
  onFeedback,
  onRetract,
}: Props) {
  const [rating, setRating] = useState<Rating>();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(withoutImages(text));
      toast.success('已复制');
    } catch {
      toast.error('复制没有成功，请手动选中文字复制');
    }
  };

  const rate = (value: Rating) => {
    if (feedback === value) void onRetract?.();
    else setRating(value);
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
      {onFeedback ? (
        <>
          <Action label="有帮助" pressed={feedback === 'like'} onClick={() => rate('like')}>
            {feedback === 'like' ? <ThumbUpFilled /> : <ThumbUp />}
          </Action>
          <Action label="没帮助" pressed={feedback === 'dislike'} onClick={() => rate('dislike')}>
            {feedback === 'dislike' ? <ThumbDownFilled /> : <ThumbDown />}
          </Action>
        </>
      ) : null}
      {onRegenerate ? (
        <Action label="重新生成" disabled={regenerateDisabled} onClick={onRegenerate}>
          <Regenerate />
        </Action>
      ) : null}
      {inspect}
      {trailing}
      {rating && onFeedback ? (
        <FeedbackDialog rating={rating} onSubmit={onFeedback} onClose={() => setRating(undefined)} />
      ) : null}
    </div>
  );
}
