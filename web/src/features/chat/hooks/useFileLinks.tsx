/**
 * 回答里的文件链接点开以后（照 DSH 官方桌面版）：右侧预览这个文件，顶部是来源给得出的操作（FileActions）。
 * 文件在哪、怎么读、怎么转 PDF、怎么打开由对话的来源给（sources.ts 的 ConversationFiles），预览用共用的文件预览抽屉。
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { FilePreview, type PreviewFile } from '../../preview/FilePreview';
import type { ConversationFiles } from '../sources';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';
import type { FileOpener } from '../components/fileLinkContext';
import { fileNameOf, type FileTarget } from '../components/fileLinks';
import { FileActions } from '../components/FileActions';

/**
 * 这段对话里的文件：opener 交给回答正文与交付卡片（FileLinks），preview 摆在页面里。
 * files 不给（还不知道在哪执行、别的电脑上看本机会话）时 opener 也不给，文件链接、卡片只显示文字。
 */
export function useFileLinks(
  conversationId: string,
  files: ConversationFiles | undefined,
): { readonly opener?: FileOpener; readonly preview: ReactNode } {
  const [target, setTarget] = useState<FileTarget>();
  const close = useCallback(() => setTarget(undefined), []);
  const file = useMemo<PreviewFile | undefined>(() => {
    if (files === undefined || target === undefined) return undefined;
    const { path } = target;
    const { pdf, save, open } = files;
    return {
      name: fileNameOf(path),
      id: `${conversationId}\n${path}`,
      read: () => files.read(conversationId, path),
      ...(pdf ? { pdf: () => pdf(conversationId, path) } : {}),
      // 执行环境里的文件可能刚被智能体或用户改过：每次打开重新读
      mutable: true,
      actions: (
        <FileActions
          open={open && ((how) => open(conversationId, path, how))}
          save={save && (() => save(conversationId, path))}
        />
      ),
    };
  }, [conversationId, files, target]);
  const opener = useMemo<FileOpener | undefined>(() => {
    if (files === undefined) return undefined;
    const { save } = files;
    return {
      open: setTarget,
      read: (path: string) => files.read(conversationId, path),
      ...(save
        ? {
            save: (path: string) => {
              save(conversationId, path).catch((error: unknown) =>
                toast.error(readableError(error, '没能保存这个文件')),
              );
            },
          }
        : {}),
    };
  }, [conversationId, files]);
  return {
    opener,
    preview: file === undefined ? null : <FilePreview file={file} onClose={close} />,
  };
}
