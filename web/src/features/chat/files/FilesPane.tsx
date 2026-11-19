/**
 * 文件面板（照 DSH 官方 dsh-client-ui-sidebar-files 的文件页）：对话右边一栏，头部左上关闭、标题「文件」、右边「重新读取」，
 * 下面一行是工作文件夹的位置（太长保留末尾、左边渐隐，悬停看全），再下面是目录树。点文件交给 onOpen（对话页在右边打开它）。
 */
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ArrowRotateCw, FolderOpen, X } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import type { ConversationFiles } from '../sources';
import { ContextMenu } from '../../../ui/ContextMenu';
import { fileMenus } from './fileMenus';
import { FileTree } from './FileTree';
import { filesKey, useDirectory } from './useDirectory';

interface Props {
  readonly conversationId: string;
  readonly files: ConversationFiles & { readonly list: NonNullable<ConversationFiles['list']> };
  readonly expanded: ReadonlySet<string>;
  readonly onToggle: (path: string) => void;
  readonly onOpen: (path: string) => void;
  readonly onClose: () => void;
  /** 当前打开的文件（相对工作文件夹），目录树里标出来 */
  readonly current?: string;
  /** 放在抽屉里时不画自己的头部（抽屉自己有） */
  readonly bare?: boolean;
}

export function FilesPane({ conversationId, files, bare, onClose, ...tree }: Props) {
  const client = useQueryClient();
  const root = useDirectory(conversationId, '', files.list, files.watch).data?.root ?? null;
  const reload = () => void client.invalidateQueries({ queryKey: filesKey(conversationId) });
  const menus = fileMenus(conversationId, files, root, tree.onOpen);
  const body = (
    <>
      {root ? (
        <ContextMenu items={menus.folder('')}>
          <div className="kw-files-root" title={root}>
            <span>{root}</span>
          </div>
        </ContextMenu>
      ) : null}
      <div className="kw-files-body">
        <FileTree
          conversationId={conversationId}
          list={files.list}
          watch={files.watch}
          menus={menus}
          {...tree}
        />
      </div>
    </>
  );
  if (bare) return body;
  return (
    <section className="kw-files-pane" aria-label="文件">
      <header className="kw-sheet-head">
        <Button color="secondary" variant="ghost" size="sm" uniform aria-label="关闭" onClick={onClose}>
          <X />
        </Button>
        <h2 className="kw-sheet-title">
          <span className="kw-labeled">
            <FolderOpen />
            文件
          </span>
        </h2>
        <div className="kw-sheet-extra">
          <ReloadButton onClick={reload} />
        </div>
      </header>
      {body}
    </section>
  );
}

export function ReloadButton({ onClick }: { readonly onClick: () => void }) {
  return (
    <Tooltip content="重新读取" compact>
      <Button color="secondary" variant="ghost" size="sm" uniform aria-label="重新读取" onClick={onClick}>
        <ArrowRotateCw />
      </Button>
    </Tooltip>
  );
}

const SHORTCUT =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘P' : 'Ctrl+P';

/** 对话栏右上角的「文件」：开关文件面板（右边开着文件时开关它旁边的目录树） */
export function FilesButton({
  pressed,
  onClick,
}: {
  readonly pressed: boolean;
  readonly onClick: () => void;
}) {
  return (
    <Tooltip content={`工作文件夹里的文件（${SHORTCUT}）`} compact>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label="文件"
        aria-pressed={pressed}
        onClick={onClick}
      >
        <FolderOpen />
      </Button>
    </Tooltip>
  );
}
