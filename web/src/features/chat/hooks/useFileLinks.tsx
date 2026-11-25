/**
 * 回答里的文件链接点开以后（照 DSH 官方桌面版）：右侧预览这个文件，顶部是来源给得出的操作（FileActions）。
 * 文件在哪、怎么读、怎么转 PDF、怎么打开由对话的来源给（sources.ts 的 ConversationFiles），预览用共用的文件预览抽屉。
 * 来源列得出工作文件夹（本机对话）时还有文件面板（照官方的工作区文件，files/）：对话栏右上角「文件」或 ⌘P 打开，
 * 右边一栏是目录树；从里面点开的文件、面板开着时回答里点开的文件，都在右边这栏打开（能编辑的是编辑器，别的是预览），
 * 旁边可以留着目录树。
 * 右边这栏可以开好几个文件，一个一个标签排在上沿（照官方右侧栏的标签条）：已经有标签时，再点开的文件都进这栏（收起着就展开）；
 * 没有标签时，能编辑的、或文件面板开着时点开的进这栏，别的照旧开抽屉。标签按对话记着（fileTabs.ts）：标签条右端可以收起这栏、
 * 标签留着，收起后对话头上有展开按钮；切到别的对话再回来、刷新页面都还在；关掉最后一个标签才清空、这栏收起。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { FileEditPane, FilePreview, type PreviewFile } from '../../preview/FilePreview';
import { isEditableFormat } from '../../preview/previewKind';
import { useSidebar } from '../../../stores/sidebar';
import { useMediaQuery } from '../../../ui/useMediaQuery';
import type { AttachmentSource, ConversationFiles } from '../sources';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';
import type { FileOpener } from '../components/fileLinkContext';
import { fileNameOf, type FileTarget } from '../components/fileLinks';
import { FileActions } from '../components/FileActions';
import { PaneToggleButton } from '../components/PaneToggleButton';
import { editRequestText, useComposerInbox } from '../editRequest';
import { useQueryClient } from '@tanstack/react-query';
import { Sheet } from '../../../ui/Sheet';
import { filesKey } from '../files/useDirectory';
import { FilesButton, FilesPane, ReloadButton } from '../files/FilesPane';
import { workspacePathOf } from '../files/treePaths';
import { useFilesPanel, useFilesShortcut } from '../files/useFilesPanel';
import type { DirectoryListing } from '../sources';
import { useFileTabs } from './fileTabs';

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
  // 右边那栏的标签（按对话记着，fileTabs.ts），和没有标签时盖在对话上的抽屉里的那一个
  const fileTabs = useFileTabs(conversationId);
  const { tabs } = fileTabs;
  const [sheet, setSheet] = useState<FileTarget>();
  const offer = useComposerInbox((state) => state.offer);
  const closeSheet = useCallback(() => setSheet(undefined), []);
  const fileOf = useCallback(
    (target: FileTarget): PreviewFile | undefined => {
      if (files === undefined) return undefined;
      const { path } = target;
      const { pdf, save, open, edit, editorChanges, discardEdits, saved } = files;
      return {
        name: fileNameOf(path),
        id: `${conversationId}\n${path}`,
        location: path,
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
    },
    [conversationId, files, offer],
  );
  // 能编辑的文件、文件面板开着时的任何文件并排打开在对话右边（照 ChatGPT Canvas），侧栏暂时收起给两栏腾地方；
  // 窗口太窄放不下两栏时照旧用抽屉
  const browsable = files?.list !== undefined ? { ...files, list: files.list } : undefined;
  const panel = useFilesPanel(browsable !== undefined);
  const wide = useMediaQuery(SIDE_BY_SIDE);
  const roomy = useMediaQuery(FILES_BESIDE);
  const editable = files?.edit !== undefined;
  const panelOpen = panel.open;
  const hasTabs = tabs.items.length > 0;
  const openTab = fileTabs.open;
  const open = useCallback(
    (target: FileTarget) => {
      // 有标签（展开着、收起着都算）就都进标签、展开这栏；没有时能编辑的、文件面板开着时点开的进它，别的开抽屉
      if (wide && (hasTabs || panelOpen || (editable && isEditableFormat(target.path)))) {
        setSheet(undefined);
        openTab(target);
      } else {
        setSheet(target);
      }
    },
    [wide, hasTabs, panelOpen, editable, openTab],
  );
  const opener = useMemo<FileOpener | undefined>(() => {
    if (files === undefined) return undefined;
    const { save, edit, workingCopy } = files;
    return {
      open,
      // 附件只读：能编辑的先要副本（拿不到就是这里没开在线编辑），按副本打开，人和模型改的都是那一份；
      // 原件留着是「用户最初给的样子」，模型这一轮读到的附件原文也一直对得上。
      // 输入框里还没发出去的（给的是 id）只在开着在线编辑时点得开，副本要不到就说一声，没有原件可预览
      openAttachment: (source: AttachmentSource) => {
        const sent = 'path' in source ? source.path : null;
        if (sent !== null && (!edit || !workingCopy || !isEditableFormat(sent))) {
          open({ path: sent });
          return;
        }
        if (!workingCopy) return;
        workingCopy(conversationId, source)
          .then((copy) => {
            const path = copy ?? sent;
            if (path !== null) open({ path });
            else toast.error('这里暂时不能直接编辑这个附件');
          })
          .catch((error: unknown) => toast.error(readableError(error, '没能打开这个附件')));
      },
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
  }, [conversationId, files, open]);
  const tabFiles = useMemo(
    () => tabs.items.map(fileOf).filter((file): file is PreviewFile => file !== undefined),
    [tabs.items, fileOf],
  );
  const activeTab = tabs.items.find((item) => item.path === tabs.activeId);
  const side = wide && tabFiles.length > 0 && tabs.expanded;
  // 窗口窄到放不下两栏时，展开着的那栏的当前文件改到抽屉里看；关掉抽屉就是收起那一栏（标签留着）
  const drawerTarget = side ? undefined : (sheet ?? (tabs.expanded ? activeTab : undefined));
  const drawerFile = useMemo(
    () => (drawerTarget === undefined ? undefined : fileOf(drawerTarget)),
    [drawerTarget, fileOf],
  );
  const { collapse, expand, select: selectTab, close: closeTabs } = fileTabs;
  const closeDrawer = useCallback(() => {
    if (sheet !== undefined) closeSheet();
    else collapse();
  }, [sheet, closeSheet, collapse]);
  const fold = useSidebar((state) => state.fold);
  useEffect(() => {
    if (!side) return;
    fold(true);
    return () => fold(false);
  }, [side, fold]);
  const client = useQueryClient();
  const root = client.getQueryData<DirectoryListing>(filesKey(conversationId, ''))?.root ?? null;
  const shown = side ? activeTab : drawerTarget;
  const current = shown ? (workspacePathOf(shown.path, root) ?? undefined) : undefined;
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
        onOpen: (path: string) => open({ path }),
        onClose: panel.close,
        current,
      }
    : undefined;
  // 有标签就一直挂着编辑区（收起时 hidden），切回来、展开回来编辑器不用重开
  const editPane =
    wide && tabs.activeId !== undefined && tabFiles.length > 0 ? (
      <FileEditPane
        files={tabFiles}
        activeId={`${conversationId}\n${tabs.activeId}`}
        hidden={!side}
        onSelect={(id) => selectTab(id.slice(conversationId.length + 1))}
        onClose={(ids) => closeTabs(ids.map((id) => id.slice(conversationId.length + 1)))}
        tools={
          <>
            {tree ? <FilesButton pressed={panel.open && panel.beside} onClick={toggle} /> : null}
            <PaneToggleButton expanded count={tabs.items.length} onClick={collapse} />
          </>
        }
        aside={tree && panel.open && panel.beside ? <FilesPane {...tree} bare /> : undefined}
      />
    ) : null;
  const filesPane = !side && tree && panel.open && roomy ? <FilesPane {...tree} /> : null;
  const pane =
    editPane || filesPane ? (
      <>
        {editPane}
        {filesPane}
      </>
    ) : null;
  const preview =
    drawerFile !== undefined ? (
      <FilePreview file={drawerFile} onClose={closeDrawer} />
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
  // 收起着、还有标签：对话头上给展开按钮（照官方：右侧栏收起后会话头角上的展开按钮）
  const reopen = hasTabs && !side && drawerTarget === undefined;
  const headerTools =
    (tree && !side) || reopen ? (
      <>
        {tree && !side ? <FilesButton pressed={panel.open} onClick={toggle} /> : null}
        {reopen ? <PaneToggleButton expanded={false} count={tabs.items.length} onClick={expand} /> : null}
      </>
    ) : null;
  return {
    opener,
    preview,
    pane,
    paneMode: side ? 'file' : filesPane ? 'files' : null,
    tools: headerTools,
  };
}
