/**
 * 输入框里待发的附件。数量与大小上限取服务端默认值（attachments/settings.py：每次最多 10 个、单个 20 MiB），
 * 在这里先挡一道，免得传了才被拒；最终以服务端校验为准。
 */
import { useCallback, useState } from 'react';

export const MAX_DRAFT_FILES = 10;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export interface DraftFiles {
  readonly files: readonly File[];
  /** 加入文件；有被挡下的返回原因（可以直接提示给用户），全部加入返回 null。 */
  readonly add: (incoming: readonly File[]) => string | null;
  readonly remove: (index: number) => void;
  readonly clear: () => void;
}

export function useDraftFiles(): DraftFiles {
  const [files, setFiles] = useState<readonly File[]>([]);

  const add = useCallback(
    (incoming: readonly File[]) => {
      const tooLarge = incoming.filter((file) => file.size > MAX_FILE_BYTES);
      const fit = incoming.filter((file) => file.size <= MAX_FILE_BYTES);
      const room = Math.max(0, MAX_DRAFT_FILES - files.length);
      setFiles([...files, ...fit.slice(0, room)]);
      if (tooLarge.length > 0) return `${tooLarge.map((f) => f.name).join('、')} 超过 20 MiB，没有加入`;
      if (fit.length > room) return `一次最多带 ${MAX_DRAFT_FILES} 个文件`;
      return null;
    },
    [files],
  );
  const remove = useCallback(
    (index: number) => setFiles((current) => current.filter((_, i) => i !== index)),
    [],
  );
  const clear = useCallback(() => setFiles([]), []);
  return { files, add, remove, clear };
}
