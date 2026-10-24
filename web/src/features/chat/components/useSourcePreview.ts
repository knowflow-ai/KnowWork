/**
 * 打开引用出处的原文：知识源注册了引用预览器（插槽 citation.preview）时在预览抽屉里打开并按定位高亮，
 * 预览文件点开时才去取，没有权限或文档没了就提示一句。引用气泡与回答下方的引用文件共用。
 */
import { useState } from 'react';
import { readableError } from '../../../api/http';
import { slotItems } from '../../../slots/registry';
import { toast } from '../../../ui/toastStore';
import type { PreviewFile } from '../../preview/FilePreview';
import type { PreviewLocate } from '../../preview/locate';
import type { CitationSource } from '../events/types';

export function previewerOf(source?: CitationSource) {
  if (!source?.sourceId || !source.docRef) return undefined;
  return slotItems('citation.preview').find((item) => item.sourceId === source.sourceId);
}

export interface Opened {
  readonly file: PreviewFile;
  readonly locate?: PreviewLocate;
}

export function useSourcePreview() {
  const [opened, setOpened] = useState<Opened>();
  const [opening, setOpening] = useState(false);
  /** 返回是否打开了（失败已提示）。 */
  const open = async (source: CitationSource): Promise<boolean> => {
    const previewer = previewerOf(source);
    if (!previewer || !source.docRef) return false;
    setOpening(true);
    try {
      const file = await previewer.resolve(source.docRef);
      if (!file) {
        toast.error('原文已不可用，可能文档已删除或你没有权限');
        return false;
      }
      setOpened({ file, locate: source.locate });
      return true;
    } catch (error) {
      toast.error(readableError(error, '原文没有打开，请稍后重试'));
      return false;
    } finally {
      setOpening(false);
    }
  };
  return { opened, opening, open, close: () => setOpened(undefined) };
}
