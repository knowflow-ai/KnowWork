/**
 * 交付的文件拖到别的应用（访达、桌面、聊天软件）：只在外壳给了原生拖出时有（utils/shellFileDrag.ts，桌面端），
 * 照 DSH 官方 file-drag 的规则：
 * - 字节来源鼠标移上去就准备（读出这个文件交给外壳写成临时文件），移开没拖就释放；
 * - 准备好之前起拖静默取消（等准备好再拖）；准备失败的（太大、读不出来）下一次起拖时提示改用下载；
 * - 卸载时释放没用上的准备。
 */
import { useEffect, useRef, type DragEvent } from 'react';
import { toast } from '../../../ui/toastStore';
import { mediaTypeOf, shellFileDrag, type ShellFileDrag } from '../../../utils/shellFileDrag';

export interface FileDragProps {
  readonly draggable: true;
  readonly onPointerEnter: () => void;
  readonly onPointerLeave: () => void;
  readonly onDragStart: (event: DragEvent<HTMLElement>) => void;
}

interface Preparation {
  readonly requestId: string;
  handle?: string;
  failed: boolean;
  settled: boolean;
}

function releaseOf(drag: ShellFileDrag, entry: Preparation): void {
  if (entry.handle) drag.release(entry.handle);
  else if (!entry.settled) drag.cancel(entry.requestId);
}

export function useFileDrag(
  name: string,
  read: (() => Promise<ArrayBuffer>) | undefined,
): FileDragProps | undefined {
  const drag = shellFileDrag();
  const prepared = useRef<Preparation | null>(null);
  useEffect(
    () => () => {
      const entry = prepared.current;
      const shell = shellFileDrag();
      prepared.current = null;
      if (entry && shell) releaseOf(shell, entry);
    },
    [],
  );
  if (!drag || !read) return undefined;

  const release = () => {
    const entry = prepared.current;
    prepared.current = null;
    if (entry) releaseOf(drag, entry);
  };
  const prepare = () => {
    if (prepared.current) return;
    const entry: Preparation = { requestId: crypto.randomUUID(), failed: false, settled: false };
    prepared.current = entry;
    read()
      .then((buffer) =>
        drag.prepare(
          { kind: 'bytes', bytes: new Uint8Array(buffer), mediaType: mediaTypeOf(name), name },
          entry.requestId,
        ),
      )
      .then((handle) => {
        entry.settled = true;
        // 准备的这一会儿鼠标已经移开了：用不上，马上释放
        if (prepared.current !== entry) {
          if (handle) drag.release(handle);
          return;
        }
        if (handle) entry.handle = handle;
        else entry.failed = true;
      })
      .catch(() => {
        entry.settled = true;
        entry.failed = true;
      });
  };
  return {
    draggable: true,
    onPointerEnter: prepare,
    onPointerLeave: release,
    onDragStart: (event) => {
      // 交给外壳起系统拖动，页面自己的拖动不要
      event.preventDefault();
      const entry = prepared.current;
      if (!entry) return;
      if (entry.handle) {
        prepared.current = null;
        void drag.start(entry.handle);
      } else if (entry.failed) {
        prepared.current = null;
        toast.warning('这个文件拖不出去（太大或读不出来），请用下载');
      }
    },
  };
}
