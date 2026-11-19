/**
 * 回答里的文件链接点开以后（照 DSH 官方桌面版）：右侧预览这个文件，顶部是来源给得出的操作（FileActions）。
 * 文件在哪、怎么读、怎么转 PDF、怎么打开由对话的来源给（sources.ts 的 ConversationFiles），预览用共用的文件预览抽屉。
 * 来源列得出工作文件夹（本机对话）时还有文件面板（照官方的工作区文件，files/）：对话栏右上角「文件」或 ⌘P 打开，
 * 右边一栏是目录树；从里面点开的文件、面板开着时回答里点开的文件，都在右边这栏打开（能编辑的是编辑器，别的是预览），
 * 旁边可以留着目录树。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { FileEditPane, FilePreview, opensInEditor, type PreviewFile } from '../../preview/FilePreview';
import { isEditableFormat } from '../../preview/previewKind';
import { useSidebar } from '../../../stores/sidebar';
import { useMediaQuery } from '../../../ui/useMediaQuery';
import type { ConversationFiles } from '../sources';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';
import type { FileOpener } from '../components/fileLinkContext';
import { fileNameOf, type FileTarget } from '../components/fileLinks';
import { FileActions } from '../components/FileActions';
import { editRequestText, useComposerInbox } from '../editRequest';
import { useQueryClient } from '@tanstack/react-query';
import { Sheet } from '../../../ui/Sheet';
import { filesKey } from '../files/useDirectory';
import { FilesButton, FilesPane, ReloadButton } from '../files/FilesPane';
import { workspacePathOf } from '../files/treePaths';
import { useFilesPanel, useFilesShortcut } from '../files/useFilesPanel';
import type { DirectoryListing } from '../sources';

/**
 * 窗口至少这么宽才把编辑区并排放在对话右边：侧栏收起成图标栏后，对话栏至少 320，编辑器要 860 左右工具栏才放得下
 * （窄了 GenOffice 的工具栏右边被截掉）
 */
const SIDE_BY_SIDE = '(min-width: 1240px)';

/** 只有目录树（没开文件）时，窗口至少这么宽就放在对话右边一栏（320 宽），窄了用抽屉 */
const FILES_BESIDE = '(min-width: 960px)';

/**
 * 这段对话里的文件：opener 交给回答正文与交付卡片（FileLinks），preview（抽屉）摆在页面里，pane（并排的编辑区）摆在对话右边。
 * files 不给（还不知道在哪执行、别的电脑上看本机会话）时 opener 也不给，文件链接、卡片只显示文字。
 */
export function useFileLinks(
  conversationId: string,
  files: ConversationFiles | undefined,
): {
  readonly opener?: FileOpener;
  readonly preview: ReactNode;
  readonly pane: ReactNode;
  /** 右边那栏放的是什么：文件（对话栏收窄）、只有目录树（对话栏不收窄）、没有 */
  readonly paneMode: 'file' | 'files' | null;
  /** 对话栏右上角的按钮（文件面板）；来源列不出工作文件夹时没有 */
  readonly tools: ReactNode;
} {
  const [target, setTarget] = useState<FileTarget>();
  const offer = useComposerInbox((state) => state.offer);
  const close = useCallback(() => setTarget(undefined), []);
  const file = useMemo<PreviewFile | undefined>(() => {
    if (files === undefined || target === undefined) return undefined;
    const { path } = target;
    const { pdf, save, open, edit, editorChanges, discardEdits, saved } = files;
    return {
      name: fileNameOf(path),
      id: `${conversationId}\n${path}`,
      read: () => files.read(conversationId, path),
      ...(pdf ? { pdf: () => pdf(conversationId, path) } : {}),
      ...(edit
        ? {
            edit: () => edit(conversationId, path),
            // 编辑器里的「AI 修改」：写成一句点名这个文件、在编辑器里修改哪几块的话交给这段对话的输入框
            aiEdit: ({ selection, instruction, send, ...where }) =>
              offer(conversationId, editRequestText(path, selection, instruction, where), send),
          }
        : {}),
      ...(editorChanges
        ? {
            editorChanges: (listener) =>
              editorChanges(conversationId, (changes) => {
                const mine = changes.find((change) => change.path === path);
                if (mine) listener(mine);
              }),
          }
        : {}),
      ...(discardEdits ? { discardEdits: () => discardEdits(conversationId, path) } : {}),
      ...(saved ? { saved: () => saved(conversationId, path) } : {}),
      // 执行环境里的文件可能刚被智能体或用户改过：每次打开重新读
      mutable: true,
      // 来源给不出编辑（云端对话、网页嵌入）的 Office 文件：说清楚为什么只能看、去哪能改
      ...(!edit && isEditableFormat(path)
        ? { readOnlyNote: '这里的文件暂时只能预览，不能编辑。要编辑，可以在桌面端的本机对话里打开。' }
        : {}),
      actions: (
        <FileActions
          open={open && ((how) => open(conversationId, path, how))}
          save={save && (() => save(conversationId, path))}
        />
      ),
    };
  }, [conversationId, files, target, offer]);
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
  // 能编辑的文件、文件面板开着时的任何文件并排打开在对话右边（照 ChatGPT Canvas），侧栏暂时收起给两栏腾地方；
  // 窗口太窄放不下两栏时照旧用抽屉
  const browsable = files?.list !== undefined ? { ...files, list: files.list } : undefined;
  const panel = useFilesPanel(browsable !== undefined);
  const wide = useMediaQuery(SIDE_BY_SIDE);
  const roomy = useMediaQuery(FILES_BESIDE);
  const side = file !== undefined && wide && (opensInEditor(file) || panel.open);
  const fold = useSidebar((state) => state.fold);
  useEffect(() => {
    if (!side) return;
    fold(true);
    return () => fold(false);
  }, [side, fold]);
  const client = useQueryClient();
  const root = client.getQueryData<DirectoryListing>(filesKey(conversationId, ''))?.root ?? null;
  const current = target ? (workspacePathOf(target.path, root) ?? undefined) : undefined;
  const { reveal } = panel;
  useEffect(() => {
    if (panel.open) reveal(current);
  }, [panel.open, current, reveal]);
  const toggle = useCallback(() => panel.toggle(side), [panel, side]);
  useFilesShortcut(browsable !== undefined, toggle);
  const tree = browsable
    ? {
        conversationId,
        files: browsable,
        expanded: panel.expanded,
        onToggle: panel.toggleFolder,
        onOpen: (path: string) => setTarget({ path }),
        onClose: panel.close,
        current,
      }
    : undefined;
  const pane =
    side && file ? (
      <FileEditPane
        file={file}
        onClose={close}
        tools={tree ? <FilesButton pressed={panel.open && panel.beside} onClick={toggle} /> : undefined}
        aside={tree && panel.open && panel.beside ? <FilesPane {...tree} bare /> : undefined}
      />
    ) : tree && panel.open && roomy ? (
      <FilesPane {...tree} />
    ) : null;
  const preview =
    file !== undefined && !side ? (
      <FilePreview file={file} onClose={close} />
    ) : tree && panel.open && !roomy ? (
      <Sheet
        title="文件"
        width={360}
        extra={
          <ReloadButton
            onClick={() => void client.invalidateQueries({ queryKey: filesKey(conversationId) })}
          />
        }
        onClose={panel.close}
      >
        <FilesPane {...tree} bare />
      </Sheet>
    ) : null;
  return {
    opener,
    preview,
    pane,
    paneMode: side ? 'file' : pane ? 'files' : null,
    tools: tree && !side ? <FilesButton pressed={panel.open} onClick={toggle} /> : null,
  };
}
