/**
 * 提问卡片：Agent 执行中途向用户要信息、要决定。形态照审批卡片：中性边框、图标、问题、底部左侧说明右侧动作。
 * - 一次提问可以有几题，一起提交；单选用单选框，多选用复选框，「其他」填文字；
 * - 单选题填了文字就以文字为准（官方约定：单选的 custom 替代选项），选了选项就清掉文字；
 * - 模型按官方约定在推荐选项的标签末尾加「(Recommended)」，显示时换成「推荐」标记，提交时用原标签（questionText）。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { QuestionMarkCircle } from '@openai/apps-sdk-ui/components/Icon';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import { MAX_ANSWER_LENGTH, type QuestionAnswer } from '../../../api/conversations';
import type { QuestionAnswerView, QuestionItemView, QuestionOptionView, QuestionView } from '../events/types';
import { answerText, displayLabel } from './questionText';

interface Props {
  readonly question: QuestionView;
  /** 返回是否提交成功；失败由调用方提示，卡片恢复可提交。 */
  readonly onAnswer: (answers: readonly QuestionAnswer[]) => Promise<boolean>;
}

interface Draft {
  readonly selected: readonly string[];
  readonly custom: string;
}

const EMPTY: Draft = { selected: [], custom: '' };

const CLOSED_TEXT: Record<NonNullable<QuestionView['closedReason']>, string> = {
  timeout: '等待超时，这个问题已失效',
  cancelled: '已失效',
  interrupted: '运行时重启过，这个问题没等到回答，请在输入框里直接回复',
  expired: '这个问题已经结束，请在输入框里直接回复',
};

function OptionLabel({ option }: { readonly option: QuestionOptionView }) {
  const { text, recommended } = displayLabel(option.label);
  return (
    <span className="kw-question-option">
      <span>
        {text}
        {recommended ? <span className="kw-question-tag">推荐</span> : null}
      </span>
      {option.description ? <span className="kw-question-desc">{option.description}</span> : null}
    </span>
  );
}

function filled(draft: Draft): boolean {
  return draft.selected.length > 0 || draft.custom.trim().length > 0;
}

function toAnswer(item: QuestionItemView, draft: Draft): QuestionAnswer {
  const custom = draft.custom.trim();
  // 单选题填了文字时 selected 必须为空（官方约定），选项与文字在界面上本就互斥，这里再兜一次
  const selected = !item.multiSelect && custom ? [] : draft.selected;
  return custom ? { id: item.id, selected, custom } : { id: item.id, selected };
}

interface FieldProps {
  readonly item: QuestionItemView;
  readonly draft: Draft;
  readonly disabled: boolean;
  readonly onChange: (draft: Draft) => void;
}

function QuestionField({ item, draft, disabled, onChange }: FieldProps) {
  const onCustom = (custom: string) =>
    onChange(item.multiSelect || !custom.trim() ? { ...draft, custom } : { selected: [], custom });
  const toggle = (label: string, checked: boolean) =>
    onChange({
      ...draft,
      selected: checked ? [...draft.selected, label] : draft.selected.filter((value) => value !== label),
    });
  const hasOptions = item.options.length > 0;
  return (
    <fieldset className="kw-question-item" disabled={disabled}>
      <legend className="kw-question-title">
        {item.header ? <span className="kw-question-header">{item.header}</span> : null}
        {item.question}
      </legend>
      {item.detail ? <div className="kw-question-desc">{item.detail}</div> : null}
      {hasOptions && item.multiSelect ? (
        <div className="kw-question-options">
          {item.options.map((option) => (
            <Checkbox
              key={option.label}
              checked={draft.selected.includes(option.label)}
              disabled={disabled}
              onCheckedChange={(checked) => toggle(option.label, checked)}
              label={<OptionLabel option={option} />}
            />
          ))}
        </div>
      ) : null}
      {hasOptions && !item.multiSelect ? (
        <RadioGroup
          className="kw-question-options"
          aria-label={item.question}
          direction="col"
          // 填了文字时一个都不选：值给空串，不能给 undefined（会变成不受控）
          value={draft.selected[0] ?? ''}
          disabled={disabled}
          onChange={(value) => onChange({ selected: [value], custom: '' })}
        >
          {item.options.map((option) => (
            <RadioGroup.Item key={option.label} value={option.label}>
              <OptionLabel option={option} />
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      ) : null}
      <Textarea
        className="kw-question-input"
        aria-label={`回答：${item.question}`}
        placeholder={
          !hasOptions ? '输入你的回答' : item.multiSelect ? '其他（可选）' : '其他（填写后以此为准）'
        }
        rows={1}
        autoResize
        maxRows={6}
        maxLength={MAX_ANSWER_LENGTH}
        value={draft.custom}
        disabled={disabled}
        onChange={(event) => onCustom(event.target.value)}
      />
    </fieldset>
  );
}

function ReadOnlyItem({
  item,
  answer,
}: {
  readonly item: QuestionItemView;
  readonly answer?: QuestionAnswerView;
}) {
  const reply = answerText(answer);
  return (
    <div className="kw-question-item">
      <div className="kw-question-title">
        {item.header ? <span className="kw-question-header">{item.header}</span> : null}
        {item.question}
      </div>
      {reply ? <div className="kw-question-reply">你的回答：{reply}</div> : null}
    </div>
  );
}

function footText(question: QuestionView, sent: boolean): string {
  if (question.status === 'answered') return '已回答';
  if (question.status === 'pending' && sent) return '已提交，等 Agent 接收';
  if (question.status === 'closed') return CLOSED_TEXT[question.closedReason ?? 'cancelled'];
  return question.continued ? 'Agent 先继续做别的了，你回答后会接着处理' : '回答后 Agent 接着执行';
}

export function QuestionCard({ question, onAnswer }: Props) {
  const [drafts, setDrafts] = useState<Readonly<Record<string, Draft>>>({});
  const [busy, setBusy] = useState(false);
  // 提交成功到「已回答」事件到达之间锁住卡片：这时再提交，后端会说问题已结束
  const [sent, setSent] = useState(false);
  const pending = question.status === 'pending';
  const draftOf = (id: string) => drafts[id] ?? EMPTY;
  const complete = question.questions.every((item) => filled(draftOf(item.id)));
  const submit = async () => {
    setBusy(true);
    try {
      setSent(await onAnswer(question.questions.map((item) => toAnswer(item, draftOf(item.id)))));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="kw-question" aria-label="需要你回答的问题">
      <div className="kw-question-head">
        <QuestionMarkCircle className="kw-approval-icon" aria-hidden />
        <div className="kw-question-body">
          {question.questions.map((item) =>
            pending ? (
              <QuestionField
                key={item.id}
                item={item}
                draft={draftOf(item.id)}
                disabled={busy || sent}
                onChange={(draft) => setDrafts((current) => ({ ...current, [item.id]: draft }))}
              />
            ) : (
              <ReadOnlyItem
                key={item.id}
                item={item}
                answer={question.answers?.find((a) => a.id === item.id)}
              />
            ),
          )}
        </div>
      </div>
      <div className="kw-question-foot">
        <span className="kw-question-desc">{footText(question, sent)}</span>
        {pending && !sent ? (
          <Button color="primary" onClick={() => void submit()} loading={busy} disabled={!complete || busy}>
            提交回答
          </Button>
        ) : null}
      </div>
    </section>
  );
}
