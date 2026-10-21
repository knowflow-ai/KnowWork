/**
 * 附件小卡片（照官方 dsh-client-ui-attachment：输入框下有一排草稿附件，消息里显示带过的文件）：
 * 类型色块、文件名、大小；草稿里的可以移除。用户气泡与输入框共用。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import type { AttachmentChip } from '../../../api/attachments';
import { formatBytes } from '../../../utils/format';
import { FILE_KINDS } from './fileKindStyles';
import { fileKindOf } from './fileKind';

interface Props {
  readonly items: readonly AttachmentChip[];
  /** 给了就显示移除按钮（输入框里的草稿）。 */
  readonly onRemove?: (index: number) => void;
  readonly label?: string;
}

export function AttachmentChips({ items, onRemove, label = '附件' }: Props) {
  if (items.length === 0) return null;
  return (
    <ul className="kw-attachment-chips" aria-label={label}>
      {items.map((item, index) => {
        const kind = FILE_KINDS[fileKindOf(item.name)];
        return (
          <li key={`${item.name}-${index}`} className="kw-attachment-chip">
            <span className="kw-attachment-icon" style={{ background: kind.color }} aria-hidden>
              {kind.icon}
            </span>
            <span className="kw-attachment-name" title={item.name}>
              {item.name}
            </span>
            <span className="kw-attachment-size">{formatBytes(item.size)}</span>
            {onRemove ? (
              <Button
                color="secondary"
                variant="ghost"
                size="2xs"
                uniform
                aria-label={`移除 ${item.name}`}
                onClick={() => onRemove(index)}
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
