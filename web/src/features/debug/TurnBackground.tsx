/**
 * 运行详情顶部：这一轮随提问带上的背景（扩展每轮补的上下文，例如记忆），原样给出模型看到的文字。
 * 默认收起，写法同步骤里的「思考过程」。每轮都带的那段只在变了时才发，沿用之前那一份时写明是第几轮发的。
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import type { DebugBackground } from '../../api/debug';
import { Block } from './Block';

function labelOf(item: DebugBackground, turn: number): string {
  if (item.kind === 'turn') return '只给这一轮';
  return item.sentTurn === turn ? '每轮都带 · 这一轮发的' : `每轮都带 · 沿用第 ${item.sentTurn} 轮发的`;
}

export function TurnBackground({
  items,
  turn,
}: {
  readonly items: readonly DebugBackground[];
  readonly turn: number;
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className="kw-trace-background">
      <button
        type="button"
        className="kw-process-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown /> : <ChevronRight />}
        随提问带上的背景
      </button>
      {open
        ? items.map((item) => (
            <Block key={`${item.kind}-${item.sentTurn}`} label={labelOf(item, turn)} text={item.text} />
          ))
        : null}
    </div>
  );
}
