/**
 * @ 的小标签：输入框上方（可以去掉）与用户气泡上方（只看）。每个都写类型名，悬停说明选了会怎样，
 * 先排「在哪里找」（知识库、目录、文件、标签），再排「怎么做」（技能）。
 * 样式和附件卡片分开：附件是带进对话的文件（带类型色块和大小），@ 是限定范围，用单色的圆角小标签。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { mentionKey, type Mention } from '../../../api/mentions';
import { cx } from '../../../ui/cx';
import { KIND_ORDER, MENTION_KINDS } from './kinds';

interface Props {
  readonly items: readonly Mention[];
  /** 给了就能去掉（输入框里的） */
  readonly onRemove?: (mention: Mention) => void;
  readonly label: string;
}

function explain(mention: Mention): string {
  const kind = MENTION_KINDS[mention.type];
  return mention.baseName ? `${kind.effect}（在知识库「${mention.baseName}」）` : kind.effect;
}

export function MentionChips({ items, onRemove, label }: Props) {
  if (items.length === 0) return null;
  const sorted = [...items].sort((a, b) => KIND_ORDER.indexOf(a.type) - KIND_ORDER.indexOf(b.type));
  return (
    <ul className="kw-mention-chips" aria-label={label}>
      {sorted.map((mention) => {
        const kind = MENTION_KINDS[mention.type];
        return (
          <li key={mentionKey(mention)} className={cx('kw-mention-chip', !onRemove && 'is-static')}>
            <Tooltip content={explain(mention)} compact>
              <span className="kw-mention-chip-body">
                <span className="kw-mention-chip-icon" aria-hidden>
                  {kind.icon}
                </span>
                <span className="kw-mention-chip-kind">{kind.label}</span>
                <span className="kw-mention-chip-name" title={mention.name}>
                  {mention.name}
                </span>
              </span>
            </Tooltip>
            {onRemove ? (
              <Button
                color="secondary"
                variant="ghost"
                size="2xs"
                uniform
                aria-label={`去掉${kind.label}「${mention.name}」`}
                onClick={() => onRemove(mention)}
              >
                <X />
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
