/**
 * 输入框里待发的附件：一加进来就传上去暂存（服务端马上开始解析），打字的工夫多半就解析好了；解析中、长文档生成导读中的
 * 隔一会儿查一次进度；
 * 移除的顺手在服务端删掉。数量与大小上限取服务端默认值（attachments/settings.py：每次最多 10 个、单个 20 MiB），
 * 在这里先挡一道，免得传了才被拒；最终以服务端校验为准。传不上去的（格式不支持等）从列表里去掉并说明原因。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  attachmentStates,
  discardAttachment,
  PARSE_POLL_MS,
  stageAttachment,
  type SentAttachment,
  type StagedAttachment,
} from '../../../api/attachments';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';

export const MAX_DRAFT_FILES = 10;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

/**
 * uploading 传上去之前；parsing 提取或识别文字中；digesting 长文档在生成导读；其余是解析的结果
 * （none：这种文件不解析，传上去就能发；failed：没能解析，照原件发）。
 */
export type DraftState = 'uploading' | 'parsing' | 'digesting' | 'ready' | 'failed' | 'none';

export interface DraftAttachment {
  readonly key: string;
  readonly name: string;
  readonly size: number;
  readonly state: DraftState;
  readonly id?: string;
  /** 导读做到第几段、一共几段 */
  readonly progress?: readonly [number, number];
}

/** 服务端的状态换成输入框里的：先等解析，解析完是长文档的再等导读（导读没做成不拦着，照解析结果发）。 */
function draftStateOf(staged: StagedAttachment): Pick<DraftAttachment, 'state' | 'progress'> {
  if (staged.parse === 'parsing') return { state: 'parsing' };
  if (staged.digest === 'digesting') return { state: 'digesting', progress: staged.digestProgress };
  return { state: staged.parse };
}

const sameProgress = (a?: readonly [number, number], b?: readonly [number, number]) =>
  a?.[0] === b?.[0] && a?.[1] === b?.[1];

export interface DraftAttachments {
  readonly items: readonly DraftAttachment[];
  /** 加入文件；有被挡下的返回原因（可以直接提示给用户），全部加入返回 null。 */
  readonly add: (incoming: readonly File[]) => string | null;
  readonly remove: (key: string) => void;
  /** 发出去以后清空（已经随消息认领进会话，不用删） */
  readonly clear: () => void;
  /** 发送失败：把清掉的放回来（排在这期间新加的前面） */
  readonly restore: (items: readonly DraftAttachment[]) => void;
  /** 最新的列表（发送前等上传、解析时在异步流程里读） */
  readonly latest: () => readonly DraftAttachment[];
}

let counter = 0;
const nextKey = () => {
  counter += 1;
  return `draft-${counter}`;
};

/** 能随消息发出去的：传上去了的（解析中、没解析成的也能发，模型拿原件读）。 */
export const sendable = (items: readonly DraftAttachment[]): readonly SentAttachment[] =>
  items.flatMap((item) => (item.id ? [{ id: item.id, name: item.name, size: item.size }] : []));

export function useDraftAttachments(): DraftAttachments {
  const [items, setItems] = useState<readonly DraftAttachment[]>([]);
  // 异步流程（上传回来、发送前等待）里要读最新的列表
  const current = useRef(items);
  useLayoutEffect(() => {
    current.current = items;
  }, [items]);

  const update = useCallback((key: string, change: Partial<DraftAttachment>) => {
    setItems((list) => list.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }, []);

  const upload = useCallback(
    (key: string, file: File) => {
      stageAttachment(file).then(
        (staged) => {
          // 传的时候已经被移除了：服务端那份也删掉
          if (!current.current.some((item) => item.key === key)) {
            void discardAttachment(staged.id).catch(() => undefined);
            return;
          }
          update(key, { id: staged.id, ...draftStateOf(staged) });
        },
        (error: unknown) => {
          setItems((list) => list.filter((item) => item.key !== key));
          toast.error(readableError(error, `${file.name} 没有传上去，请重新添加`));
        },
      );
    },
    [update],
  );

  const add = useCallback(
    (incoming: readonly File[]) => {
      const tooLarge = incoming.filter((file) => file.size > MAX_FILE_BYTES);
      const fit = incoming.filter((file) => file.size <= MAX_FILE_BYTES);
      const room = Math.max(0, MAX_DRAFT_FILES - current.current.length);
      const accepted = fit.slice(0, room).map((file) => ({ file, key: nextKey() }));
      setItems((list) => [
        ...list,
        ...accepted.map(({ file, key }) => ({
          key,
          name: file.name,
          size: file.size,
          state: 'uploading' as const,
        })),
      ]);
      for (const { file, key } of accepted) upload(key, file);
      if (tooLarge.length > 0) return `${tooLarge.map((f) => f.name).join('、')} 超过 20 MiB，没有加入`;
      if (fit.length > room) return `一次最多带 ${MAX_DRAFT_FILES} 个文件`;
      return null;
    },
    [upload],
  );

  const remove = useCallback((key: string) => {
    const item = current.current.find((entry) => entry.key === key);
    if (item?.id) void discardAttachment(item.id).catch(() => undefined);
    setItems((list) => list.filter((entry) => entry.key !== key));
  }, []);

  const clear = useCallback(() => setItems([]), []);
  const restore = useCallback((back: readonly DraftAttachment[]) => {
    setItems((list) => [...back.filter((item) => !list.some((entry) => entry.key === item.key)), ...list]);
  }, []);
  const latest = useCallback(() => current.current, []);

  // 有解析中、生成导读中的就隔一会儿查一次；查不到（网络抖动）下次再查
  const busy = items.filter((item) => (item.state === 'parsing' || item.state === 'digesting') && item.id);
  const watched = busy.map((item) => item.id as string).join(',');
  useEffect(() => {
    if (!watched) return undefined;
    const ids = watched.split(',');
    const timer = window.setInterval(() => {
      attachmentStates(ids).then(
        (states) => {
          const byId = new Map(states.map((state) => [state.id, draftStateOf(state)]));
          setItems((list) =>
            list.map((item) => {
              const next = item.id ? byId.get(item.id) : undefined;
              if (!next || (next.state === item.state && sameProgress(next.progress, item.progress))) {
                return item;
              }
              return { ...item, ...next };
            }),
          );
        },
        () => undefined,
      );
    }, PARSE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [watched]);

  return { items, add, remove, clear, restore, latest };
}
