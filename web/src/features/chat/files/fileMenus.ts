/**
 * 文件面板的右键菜单（照 DSH 官方：文件行是 open-in-app 的「在侧边栏打开 / 在 Finder 中显示 / 使用默认应用打开」加
 * file-actions 的「另存为…」，文件夹行和根路径是「在 Finder 中显示 / 复制文件夹路径」，文案用官方的）。没有浮窗，官方的
 * 「在浮窗中打开」不做。来源给不出的操作不列；做不成的弹一句原因。
 */
import { readableError } from '../../../api/http';
import type { ContextMenuItem } from '../../../ui/ContextMenu';
import { toast } from '../../../ui/toastStore';
import type { ConversationFiles } from '../sources';

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
const REVEAL = MAC ? '在 Finder 中显示' : '在文件夹显示';

export interface FileMenus {
  readonly file: (path: string) => readonly ContextMenuItem[];
  readonly folder: (path: string) => readonly ContextMenuItem[];
}

function attempt(action: () => Promise<unknown>, fallback: string, done?: string): void {
  action().then(
    () => (done ? toast.success(done) : undefined),
    (error: unknown) => toast.error(readableError(error, fallback)),
  );
}

/** 工作文件夹下一个文件夹的完整位置（根是空串）；按工作文件夹的写法用 / 或 \ 拼 */
export function folderLocation(root: string, path: string): string {
  if (!path) return root;
  const separator = root.includes('\\') && !root.includes('/') ? '\\' : '/';
  return `${root.replace(/[\\/]+$/, '')}${separator}${path.split('/').join(separator)}`;
}

export function fileMenus(
  conversationId: string,
  files: ConversationFiles,
  root: string | null,
  onOpen: (path: string) => void,
): FileMenus {
  const { open, save, revealFolder } = files;
  return {
    file: (path) => [
      { label: '在侧边栏打开', onSelect: () => onOpen(path) },
      ...(open
        ? [
            {
              label: REVEAL,
              onSelect: () => attempt(() => open(conversationId, path, 'reveal'), '无法显示文件位置，请重试'),
            },
            {
              label: '使用默认应用打开',
              onSelect: () => attempt(() => open(conversationId, path, 'app'), '没能打开这个文件'),
            },
          ]
        : []),
      ...(save
        ? [
            {
              label: '另存为…',
              onSelect: () =>
                attempt(() => save(conversationId, path), '无法保存文件，请检查源文件和目标位置后重试'),
            },
          ]
        : []),
    ],
    folder: (path) => [
      ...(revealFolder
        ? [
            {
              label: REVEAL,
              onSelect: () => attempt(() => revealFolder(conversationId, path), '无法显示文件位置，请重试'),
            },
          ]
        : []),
      ...(root
        ? [
            {
              label: '复制文件夹路径',
              onSelect: () =>
                attempt(
                  () => navigator.clipboard.writeText(folderLocation(root, path)),
                  '无法复制，请检查内容是否可用后重试',
                  '已复制',
                ),
            },
          ]
        : []),
    ],
  };
}
