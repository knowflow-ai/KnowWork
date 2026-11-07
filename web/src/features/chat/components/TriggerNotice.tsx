/**
 * 不是人发起的一轮（定时任务到点；后台任务、子 Agent 做完时 Agent 正空闲，官方自己开了一轮）：照官方对话界面的做法，
 * 轮次上方一条可展开的通知——图标、由什么发起、时间，展开看详情。不画成用户气泡：这句话不是这个人此刻说的。
 */
import { useState, type ComponentType } from 'react';
import { Agent, ChevronDown, ChevronRight, Clock, Terminal } from '@openai/apps-sdk-ui/components/Icon';
import { formatClock } from '../events/selectors';
import type { RunTrigger } from '../events/types';

interface Kind {
  readonly title: string;
  readonly explain: string;
  readonly Icon: ComponentType<{ 'aria-hidden'?: boolean }>;
}

const KINDS: Readonly<Record<string, Kind>> = {
  schedule: { title: '定时任务', explain: '到点自动发起了这一轮，任务内容：', Icon: Clock },
  'tool-jobs': { title: '后台任务完成', explain: '后台任务跑完了，Agent 接着处理结果：', Icon: Terminal },
  'subagent-settled': {
    title: '子 Agent 完成',
    explain: '子 Agent 做完了，Agent 接着处理结果：',
    Icon: Agent,
  },
};
const OTHER: Kind = { title: '自动发起', explain: '这一轮不是你发起的：', Icon: Clock };

export function TriggerNotice({ trigger }: { readonly trigger: RunTrigger }) {
  const [open, setOpen] = useState(false);
  const { title, explain, Icon } = KINDS[trigger.source] ?? OTHER;
  return (
    <section className="kw-trigger" aria-label={`${title}发起的这一轮`}>
      <button
        type="button"
        className="kw-trigger-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon aria-hidden />
        <span className="kw-trigger-title">{title}</span>
        <time dateTime={new Date(trigger.time).toISOString()}>{formatClock(trigger.time)}</time>
        {open ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}
      </button>
      {open ? (
        <div className="kw-trigger-body">
          <p className="kw-trigger-explain">{explain}</p>
          <div className="kw-trigger-text">{trigger.text}</div>
        </div>
      ) : null}
    </section>
  );
}
