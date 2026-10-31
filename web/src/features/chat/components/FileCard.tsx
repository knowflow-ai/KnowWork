/**
 * 回答下面交付的文件，照 DSH 官方交付卡片（dsh-client-ui-deliverables）的规则做：
 * - 卡片：类型图标、文件名，下面一行是模型交付时写的说明（去掉末尾括号里的补充），没写就写类型与大小；
 *   点卡片本身打开预览（鼠标移上去说明换成「在右侧预览」），右边是下载；不能预览的类型卡片点不了。
 * - 排列：一个一行，多个排两列（区域窄于 620 时一列）；超过四个先收起，「全部 N 个文件」展开。
 * 外观用产品自己的：彩色类型色块、白底描边、最宽 360（官方灰底细线图标、铺满整行，放在这里显得空、类型不醒目）。
 * 官方插件挂在 DSH 自己的网页客户端里、直接找宿主读文件，用不了，这里照它的样子自己做，文件走业务服务的产物接口。
 */
import { useState } from 'react';
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, ChevronUp, Download } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { formatBytes } from '../../../utils/format';
import type { ArtifactView } from '../events/types';
import { previewKindOf } from '../../preview/previewKind';
import { fileKindOf } from '../../../ui/fileKind';
import { FILE_KINDS } from '../../../ui/fileKindStyles';

const COLLAPSED_COUNT = 4;

interface Props {
  readonly name: string;
  readonly size?: number;
  readonly url?: string;
  readonly description?: string;
  readonly onPreview?: () => void;
}

/** 说明末尾括号里的补充去掉（同官方 cardDescription），去完是空的就用 fallback。 */
function cardDescription(description: string | undefined, fallback: string): string {
  const trimmed = description?.replace(/\s*(?:\([^()]*\)|（[^（）]*）)\s*$/u, '').trim();
  return trimmed ? trimmed : fallback;
}

export function FileCard({ name, size, url, description, onPreview }: Props) {
  const kind = FILE_KINDS[fileKindOf(name)];
  const previewable = url !== undefined && onPreview !== undefined && previewKindOf(name) !== 'none';
  const sub = cardDescription(
    description,
    size === undefined ? kind.label : `${kind.label} · ${formatBytes(size)}`,
  );
  return (
    <div className="kw-file-card">
      {previewable ? (
        <button
          type="button"
          className="kw-file-card-open"
          title={name}
          aria-label={`预览 ${name}`}
          onClick={onPreview}
        />
      ) : null}
      <span className="kw-file-icon" style={{ background: kind.color }} aria-hidden>
        {kind.icon}
      </span>
      <div className="kw-file-body">
        <div className="kw-file-meta">
          <div className="kw-file-name">{name}</div>
          <div className="kw-file-sub">
            <span className="kw-file-sub-text">{sub}</span>
            {previewable ? <span className="kw-file-hint">在右侧预览</span> : null}
          </div>
        </div>
        <div className="kw-file-actions">
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
      </div>
    </div>
  );
}

export function FileCards({
  files,
  onPreview,
}: {
  readonly files: readonly ArtifactView[];
  readonly onPreview: (file: ArtifactView) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (files.length === 0) return null;
  const collapsible = files.length > COLLAPSED_COUNT;
  const shown = collapsible && !expanded ? files.slice(0, COLLAPSED_COUNT) : files;
  return (
    <div className="kw-file-cards">
      <ul className="kw-file-grid" aria-label="交付的文件" data-single={files.length === 1 || undefined}>
        {shown.map((file) => (
          <li key={file.id}>
            <FileCard
              name={file.name}
              size={file.size}
              url={file.url}
              description={file.description}
              onPreview={() => onPreview(file)}
            />
          </li>
        ))}
      </ul>
      {collapsible ? (
        <button
          type="button"
          className="kw-file-toggle"
          aria-expanded={expanded}
          aria-label={expanded ? '收起交付文件列表' : `展开全部 ${files.length} 个交付文件`}
          onClick={() => setExpanded((value) => !value)}
        >
          <span>{expanded ? '收起' : `全部 ${files.length} 个文件`}</span>
          {expanded ? <ChevronUp /> : <ChevronDown />}
        </button>
      ) : null}
    </div>
  );
}
