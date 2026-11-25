/**
 * 附件小卡片（照官方 dsh-client-ui-attachment：输入框下有一排草稿附件，消息里显示带过的文件）：
 * 类型色块、文件名、大小；草稿里的可以移除，发送前等解析时带一句状态。用户气泡与输入框共用。
 * 点得开的（同交付卡片，见 FileCard）：消息上的附件 Word、Excel、PowerPoint 按同名副本进编辑器，别的只读预览；
 * 开着在线编辑时输入框里能编辑的附件也点得开，直接进编辑器。哪张点得开、点了做什么由用的地方给（openerOf）。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import type { AttachmentChip } from '../../../api/attachments';
import { formatBytes } from '../../../utils/format';
import { FILE_KINDS } from '../../../ui/fileKindStyles';
import { fileKindOf } from '../../../ui/fileKind';

interface Props {
  readonly items: readonly AttachmentChip[];
  /** 给了就显示移除按钮（输入框里的草稿）。 */
  readonly onRemove?: (index: number) => void;
  /** 这一张点了做什么；返回 undefined 就是点不开（消息上的按路径、输入框里的按暂存 id，各自判断） */
  readonly openerOf?: (item: AttachmentChip, index: number) => (() => void) | undefined;
  readonly label?: string;
}

export function AttachmentChips({ items, onRemove, openerOf, label = '附件' }: Props) {
  if (items.length === 0) return null;
  return (
    <ul className="kw-attachment-chips" aria-label={label}>
      {items.map((item, index) => {
        const kind = FILE_KINDS[fileKindOf(item.name)];
        const open = openerOf?.(item, index);
        return (
          <li key={`${item.name}-${index}`} className="kw-attachment-chip">
            {open ? (
              <button
                type="button"
                className="kw-attachment-open"
                title={item.name}
                aria-label={`打开 ${item.name}`}
                onClick={open}
              />
            ) : null}
            <span className="kw-attachment-icon" style={{ background: kind.color }} aria-hidden>
              {kind.icon}
            </span>
            <span className="kw-attachment-name" title={item.name}>
              {item.name}
            </span>
            <span className="kw-attachment-size">{formatBytes(item.size)}</span>
            {item.note ? (
              <span className="kw-attachment-note">
                {item.busy ? <LoadingIndicator size={12} /> : null}
                {item.note}
              </span>
            ) : null}
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
