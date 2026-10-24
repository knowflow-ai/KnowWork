/** 文件产物卡片：类型色块图标、文件名、类型与大小，右侧预览（能预览的类型）与下载按钮。 */
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Download, Eye } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { formatBytes } from '../../../utils/format';
import { previewKindOf } from '../../preview/previewKind';
import { fileKindOf } from '../../../ui/fileKind';
import { FILE_KINDS } from '../../../ui/fileKindStyles';

interface Props {
  readonly name: string;
  readonly size?: number;
  readonly url?: string;
  readonly onPreview?: () => void;
}

export function FileCard({ name, size, url, onPreview }: Props) {
  const kind = FILE_KINDS[fileKindOf(name)];
  const sub = size === undefined ? kind.label : `${kind.label} · ${formatBytes(size)}`;
  return (
    <div className="kw-file-card">
      <div className="kw-file-icon" style={{ background: kind.color }} aria-hidden>
        {kind.icon}
      </div>
      <div className="kw-file-meta">
        <div className="kw-file-name" title={name}>
          {name}
        </div>
        <div className="kw-file-sub">{sub}</div>
      </div>
      {url && onPreview && previewKindOf(name) !== 'none' ? (
        <Tooltip content="预览" compact>
          <Button color="secondary" variant="ghost" uniform aria-label={`预览 ${name}`} onClick={onPreview}>
            <Eye />
          </Button>
        </Tooltip>
      ) : null}
      {url ? (
        <Tooltip content="下载" compact>
          <ButtonLink
            as="a"
            href={url}
            download={name}
            color="secondary"
            variant="ghost"
            uniform
            aria-label={`下载 ${name}`}
          >
            <Download />
          </ButtonLink>
        </Tooltip>
      ) : (
        <Tooltip content="文件还在生成" compact>
          <span>
            <Button color="secondary" variant="ghost" uniform aria-label={`下载 ${name}`} disabled>
              <Download />
            </Button>
          </span>
        </Tooltip>
      )}
    </div>
  );
}
