/**
 * 文件面板的目录树（照 DSH 官方 dsh-client-ui-sidebar-files）：根是这段对话的工作文件夹，展开一层才列一层（来源的 list），
 * 根和展开着的每一层各跟一路变化（来源的 watch），变了就重新列这一层，列的时候照旧显示原来的条目。文件夹点名字展开收起，
 * 文件点了交给 onOpen；既不是文件也不是文件夹的灰着、点不了。空文件夹、条目太多被截断、列不出来，都在那一层写一行说明。
 * 当前打开的文件那一行标出来（aria-current）。行的样子用通用树（ui/Tree）的那套。文件、文件夹行有右键菜单（fileMenus.ts）。
 */
import { useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight, Folder } from '@openai/apps-sdk-ui/components/Icon';
import { readableError } from '../../../api/http';
import { ContextMenu } from '../../../ui/ContextMenu';
import { cx } from '../../../ui/cx';
import { FileKindIcon } from '../../../ui/FileKindIcon';
import type { ConversationFiles, DirectoryEntry } from '../sources';
import { childPath, isHidden, sortEntries } from './treePaths';
import type { FileMenus } from './fileMenus';
import { useDirectory } from './useDirectory';

type List = NonNullable<ConversationFiles['list']>;

interface TreeProps {
  readonly conversationId: string;
  readonly list: List;
  readonly watch?: ConversationFiles['watch'];
  /** 展开着的文件夹（相对工作文件夹的路径）；由外面留着，面板在目录树和文件之间来回换时不丢 */
  readonly expanded: ReadonlySet<string>;
  readonly onToggle: (path: string) => void;
  readonly onOpen: (path: string) => void;
  /** 当前打开的文件（相对工作文件夹） */
  readonly current?: string;
  /** 行的右键菜单；不给就没有 */
  readonly menus?: FileMenus;
}

export function FileTree(props: TreeProps) {
  return (
    <ul role="tree" aria-label="工作文件夹里的文件" className="kw-tree kw-file-tree">
      <Level {...props} path="" depth={0} />
    </ul>
  );
}

function Level({ path, depth, ...props }: TreeProps & { readonly path: string; readonly depth: number }) {
  const query = useDirectory(props.conversationId, path, props.list, props.watch);
  const indent = { paddingLeft: depth * 16 + 28 };
  if (query.isPending) return <Note indent={indent}>正在读取…</Note>;
  if (query.isError) return <Note indent={indent}>{readableError(query.error, '没能列出这个文件夹')}</Note>;
  const entries = sortEntries(query.data.entries);
  if (entries.length === 0) return <Note indent={indent}>空文件夹</Note>;
  return (
    <>
      {entries.map((entry) =>
        entry.type === 'directory' ? (
          <Directory
            key={entry.name}
            {...props}
            entry={entry}
            path={childPath(path, entry.name)}
            depth={depth}
          />
        ) : (
          <FileRow
            key={entry.name}
            {...props}
            entry={entry}
            path={childPath(path, entry.name)}
            depth={depth}
          />
        ),
      )}
      {query.data.truncated ? <Note indent={indent}>文件太多，只列出了前 {entries.length} 个</Note> : null}
    </>
  );
}

function Note({
  indent,
  children,
}: {
  readonly indent: { paddingLeft: number };
  readonly children: React.ReactNode;
}) {
  return (
    <li role="none" className="kw-file-tree-note" style={indent}>
      {children}
    </li>
  );
}

type RowProps = TreeProps & { readonly entry: DirectoryEntry; readonly path: string; readonly depth: number };

function Directory({ entry, path, depth, ...props }: RowProps) {
  const open = props.expanded.has(path);
  const toggle = () => props.onToggle(path);
  return (
    <li role="treeitem" aria-expanded={open} aria-selected={false} className="kw-tree-item">
      <ContextMenu items={props.menus?.folder(path) ?? []}>
        <div
          className={cx('kw-tree-row', isHidden(entry.name) && 'is-hidden')}
          style={{ paddingLeft: depth * 16 + 4 }}
        >
          <button
            type="button"
            className="kw-tree-toggle"
            aria-label={open ? '收起' : '展开'}
            onClick={toggle}
          >
            {open ? <ChevronDown /> : <ChevronRight />}
          </button>
          <button type="button" className="kw-tree-title kw-file-tree-name" onClick={toggle}>
            <Folder className="kw-file-tree-icon" aria-hidden />
            <span>{entry.name}</span>
          </button>
        </div>
      </ContextMenu>
      {open ? (
        <ul role="group" className="kw-tree-children">
          <Level {...props} path={path} depth={depth + 1} />
        </ul>
      ) : null}
    </li>
  );
}

function FileRow({ entry, path, depth, ...props }: RowProps) {
  const current = props.current === path;
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // jsdom 里没有 scrollIntoView
    if (current) row.current?.scrollIntoView?.({ block: 'nearest' });
  }, [current]);
  const other = entry.type === 'other';
  return (
    <li
      role="treeitem"
      aria-selected={current}
      aria-current={current ? 'true' : undefined}
      className="kw-tree-item"
    >
      <ContextMenu items={other ? [] : (props.menus?.file(path) ?? [])}>
        <div
          ref={row}
          className={cx('kw-tree-row', current && 'is-selected', isHidden(entry.name) && 'is-hidden')}
          style={{ paddingLeft: depth * 16 + 4 }}
        >
          <span className="kw-tree-toggle" aria-hidden="true" />
          <button
            type="button"
            className="kw-tree-title kw-file-tree-name"
            disabled={other}
            title={other ? '这不是文件或文件夹，没法打开' : undefined}
            onClick={() => props.onOpen(path)}
          >
            <FileKindIcon name={entry.name} size={16} />
            <span>{entry.name}</span>
          </button>
        </div>
      </ContextMenu>
    </li>
  );
}
