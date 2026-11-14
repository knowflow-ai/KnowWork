/**
 * 回答下面交付的文件，照 DSH 官方交付卡片（dsh-client-ui-deliverables）的规则做：
 * - 卡片：类型图标、文件名，下面一行是模型交付时写的说明（去掉末尾括号里的补充），没写就写类型与大小；
 *   点卡片本身打开预览（鼠标移上去说明换成「在右侧预览」），右边是下载；不能预览的类型卡片点不了。
 * - 网页与桌面同一套（方案 7.7.3）：按官方 present 交付的路径出卡片，预览、下载经这段对话的文件（fileLinkContext），
 *   和回答里的文件链接同一个口子；这里打不开（别的电脑上看本机会话）时卡片只是展示。
 * - 排列：一个一行，多个排两列（区域窄于 620 时一列）；超过四个先收起，「全部 N 个文件」展开。
 * 外观用产品自己的：彩色类型色块、白底描边、最宽 360（官方灰底细线图标、铺满整行，放在这里显得空、类型不醒目）。
 * 官方插件挂在 DSH 自己的网页客户端里、直接找宿主读文件，用不了，这里照它的样子自己做。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, ChevronUp, Download } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import type { DeliverableView } from '../events/types';
import { useFileOpener, type FileOpener } from './fileLinkContext';
import { useFileDrag, type FileDragProps } from './useFileDrag';
import { fileNameOf } from './fileLinks';
import { previewKindOf } from '../../preview/previewKind';
import { fileKindOf } from '../../../ui/fileKind';
import { FILE_KINDS } from '../../../ui/fileKindStyles';

const COLLAPSED_COUNT = 4;

interface Props {
  readonly name: string;
  readonly description?: string;
  /** 点开预览；不给就是这里打不开 */
  readonly onPreview?: () => void;
  /** 存到本机；不给下载按钮置灰 */
  readonly onSave?: () => void;
  /** 能拖到别的应用（外壳给了原生拖出时，见 useFileDrag） */
  readonly drag?: FileDragProps;
}

/** 说明末尾括号里的补充去掉（同官方 cardDescription），去完是空的就用 fallback。 */
function cardDescription(description: string | undefined, fallback: string): string {
  const trimmed = description?.replace(/\s*(?:\([^()]*\)|（[^（）]*）)\s*$/u, '').trim();
  return trimmed ? trimmed : fallback;
}

export function FileCard({ name, description, onPreview, onSave, drag }: Props) {
  const kind = FILE_KINDS[fileKindOf(name)];
  const previewable = onPreview !== undefined && previewKindOf(name) !== 'none';
  const sub = cardDescription(description, kind.label);
  return (
    <div className="kw-file-card" {...drag}>
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
          {onSave ? (
            <Tooltip content="下载" compact>
              <Button color="secondary" variant="ghost" uniform aria-label={`下载 ${name}`} onClick={onSave}>
                <Download />
              </Button>
            </Tooltip>
          ) : (
            <Tooltip content="这里打不开这个文件" compact>
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

/** 一张交付卡片：预览、下载经这段对话的文件；外壳能拖出时卡片可以拖到别的应用 */
function DeliverableCard({ file, opener }: { readonly file: DeliverableView; readonly opener?: FileOpener }) {
  const name = fileNameOf(file.path);
  const read = opener?.read;
  const drag = useFileDrag(name, read ? () => read(file.path) : undefined);
  return (
    <FileCard
      name={name}
      description={file.description}
      onPreview={opener ? () => opener.open({ path: file.path }) : undefined}
      onSave={opener?.save ? () => opener.save?.(file.path) : undefined}
      drag={drag}
    />
  );
}

/** 一轮交付的文件（官方 present），按路径经这段对话的文件预览、下载 */
export function DeliverableCards({ files }: { readonly files: readonly DeliverableView[] }) {
  const opener = useFileOpener();
  const [expanded, setExpanded] = useState(false);
  if (files.length === 0) return null;
  const collapsible = files.length > COLLAPSED_COUNT;
  const shown = collapsible && !expanded ? files.slice(0, COLLAPSED_COUNT) : files;
  return (
    <div className="kw-file-cards">
      <ul className="kw-file-grid" aria-label="交付的文件" data-single={files.length === 1 || undefined}>
        {shown.map((file) => (
          <li key={file.path}>
            <DeliverableCard file={file} opener={opener} />
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
