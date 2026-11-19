/**
 * 文件面板的开关与状态（照 DSH 官方 dsh-client-ui-sidebar-files 的 workspace.files 命令，快捷键 Mod+P）：
 * - 右边没开文件时，按钮或 ⌘P 开关文件面板；
 * - 右边开着文件、文件面板也开着时，按钮或 ⌘P 开关文件旁边的目录树；文件是从回答里点开的（面板没开），就打开面板并在文件旁边显示目录树；
 * - 展开着哪些文件夹由这里留着，面板在目录树和文件之间来回换时不丢；当前文件的上级自动展开（官方：定位当前文件）。
 */
import { useCallback, useEffect, useState } from 'react';
import { ancestorsOf } from './treePaths';

export interface FilesPanel {
  /** 文件面板开着 */
  readonly open: boolean;
  /** 右边开着文件时，旁边显示目录树 */
  readonly beside: boolean;
  readonly expanded: ReadonlySet<string>;
  readonly toggleFolder: (path: string) => void;
  /** 按钮、⌘P：fileOpen 是右边此刻开着文件 */
  readonly toggle: (fileOpen: boolean) => void;
  readonly close: () => void;
  /** 展开当前文件的各级上级 */
  readonly reveal: (path: string | undefined) => void;
}

export function useFilesPanel(enabled: boolean): FilesPanel {
  const [open, setOpen] = useState(false);
  const [beside, setBeside] = useState(true);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const toggleFolder = useCallback((path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);
  const toggle = useCallback(
    (fileOpen: boolean) => {
      if (!fileOpen) setOpen((current) => !current);
      else if (!open) {
        setOpen(true);
        setBeside(true);
      } else setBeside((current) => !current);
    },
    [open],
  );
  const close = useCallback(() => setOpen(false), []);
  const reveal = useCallback((path: string | undefined) => {
    if (!path) return;
    const above = ancestorsOf(path);
    setExpanded((current) =>
      above.every((dir) => current.has(dir)) ? current : new Set([...current, ...above]),
    );
  }, []);
  return { open: enabled && open, beside, expanded, toggleFolder, toggle, close, reveal };
}

/** ⌘P / Ctrl+P 调 run（编辑器在 iframe 里，焦点在编辑器里时由它自己处理） */
export function useFilesShortcut(enabled: boolean, run: () => void): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === 'p'
      ) {
        event.preventDefault();
        run();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled, run]);
}
