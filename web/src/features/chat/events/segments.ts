/**
 * 一轮回答在提问处切成几段显示（照 ChatGPT「问一句 → 你答 → 接着做」的节奏）。DSH 的提问是同一轮里的一次工具调用，
 * 这一轮没有结束；照 ChatGPT 的样子，提问之前的工作收成一行过程、下面是提问，答完后你的回答像一条用户消息，后面的工作
 * 另起一行过程，紧跟着最终回答。中途的说明文字照旧收在各段的过程里，只在提问处切开。
 * 每一段给一个只含这一段内容的 RunView（ProcessSummary 照常画），用时按这一段算：从开始（或上一个问题答上）到提问
 * （或这一轮结束）。最终答案、待办、结果卡片仍挂在整轮上，由一轮回答（AssistantTurn）画。
 */
import type { QuestionView, RunView, TrailEntry } from './types';

export interface RunSegment {
  readonly key: string;
  /** 这一段的过程：只含这一段的思考、说明、步骤 */
  readonly run: RunView;
  /** 这一段以哪次提问结尾；最后一段没有 */
  readonly question?: QuestionView;
}

interface Slice {
  readonly entries: readonly TrailEntry[];
  readonly questionId?: string;
}

function slicesOf(trail: readonly TrailEntry[]): readonly Slice[] {
  return trail.reduce<readonly Slice[]>(
    (slices, entry) => {
      const last = slices[slices.length - 1] ?? { entries: [] };
      const head = slices.slice(0, -1);
      return entry.type === 'question'
        ? [...head, { ...last, questionId: entry.id }, { entries: [] }]
        : [...head, { ...last, entries: [...last.entries, entry] }];
    },
    [{ entries: [] }],
  );
}

function textsOf(entries: readonly TrailEntry[], type: 'thinking' | 'note'): readonly string[] {
  return entries.flatMap((entry) => (entry.type === type ? [entry.text] : []));
}

export function splitAtQuestions(run: RunView, questions: readonly QuestionView[]): readonly RunSegment[] {
  const byId = new Map(questions.map((question) => [question.id, question]));
  const slices = slicesOf(run.trail);
  return slices.map((slice, index) => {
    const question = slice.questionId ? byId.get(slice.questionId) : undefined;
    const previous = index > 0 ? slices[index - 1] : undefined;
    const startedAt = previous ? byId.get(previous.questionId ?? '')?.answeredAt : run.startedAt;
    const callIds = new Set(slice.entries.flatMap((entry) => (entry.type === 'step' ? [entry.callId] : [])));
    const content = {
      ...run,
      trail: slice.entries,
      steps: run.steps.filter((step) => callIds.has(step.callId)),
      thinking: textsOf(slice.entries, 'thinking'),
      notes: textsOf(slice.entries, 'note'),
      answer: '',
      startedAt,
    };
    const last = index === slices.length - 1;
    // 以提问结尾的一段已经做完了：到提问那一刻为止；没有提问时间就只写做了什么、不写用时
    const segment: RunView = last
      ? content
      : {
          ...content,
          status: 'completed',
          endedAt: question?.askedAt ?? startedAt ?? 0,
          ...(question?.askedAt === undefined ? { startedAt: undefined } : {}),
          endReason: undefined,
          problems: [],
        };
    return { key: `segment-${index}`, run: segment, ...(question ? { question } : {}) };
  });
}
