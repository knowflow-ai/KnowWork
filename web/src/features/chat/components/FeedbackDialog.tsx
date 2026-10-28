/**
 * 对一轮回答的反馈（照 DSH 官方的消息反馈弹窗）：点了赞或踩打开，选一个分类、写几句说明，都可以不填；
 * 提交后才记下评分。只记录，不会发给模型。
 */
import { useState } from 'react';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import {
  FEEDBACK_CATEGORIES,
  MAX_FEEDBACK_NOTE,
  type FeedbackCategory,
  type FeedbackInput,
  type Rating,
} from '../../../api/feedback';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';

interface Props {
  readonly rating: Rating;
  /** 返回是否提交成功（失败已提示，弹窗留着好改）。 */
  readonly onSubmit: (input: FeedbackInput) => Promise<boolean>;
  readonly onClose: () => void;
}

export function FeedbackDialog({ rating, onSubmit, onClose }: Props) {
  const [category, setCategory] = useState<FeedbackCategory | ''>('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    const ok = await onSubmit({ rating, category: category || null, note });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      title={rating === 'like' ? '这条回答有帮助' : '这条回答哪里不好'}
      description="分类和说明都可以不填；只用来改进回答，不会发给模型。"
      onSubmit={() => void submit()}
      onClose={onClose}
      footer={<DialogActions okText="提交" loading={busy} onCancel={onClose} />}
    >
      <Field label="分类">
        <RadioGroup<FeedbackCategory | ''>
          className="kw-feedback-categories"
          aria-label="分类"
          direction="row"
          value={category}
          onChange={setCategory}
        >
          {FEEDBACK_CATEGORIES.map((item) => (
            <RadioGroup.Item key={item.value} value={item.value}>
              {item.label}
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      </Field>
      <Field label="说明">
        {(id) => (
          <Textarea
            id={id}
            rows={3}
            maxLength={MAX_FEEDBACK_NOTE}
            placeholder={rating === 'like' ? '哪里好（可不填）' : '哪里不对、希望怎么答（可不填）'}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}
