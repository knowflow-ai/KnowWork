/**
 * Word 原件预览：在浏览器里直接渲染 .docx（docx-preview，Apache-2.0），保留原来的字体、样式、编号与表格，不转 PDF。
 *
 * 知识库的定位按块对上的正文段落区间整段高亮：服务端解析时把 MinerU 的每一块对上原件正文的第几个到第几个段落或表格
 * （commercial parsing/docx_body.py），数法和这里取渲染结果的顺序一致——每页正文 article 下的直接子元素，段落、表格各一个。
 * 渲染不分页：分页会把带分页符的段落切成两段，序号就错开了；docx-preview 本来也做不了按版面自动分页。
 * 没有区间时（解析时没对上段落的块）按每块原文的开头在段落里依次找。高亮的第一段滚到视口顶部（留一点边距），
 * 从头往下看。
 * 页面比面板宽时按宽度缩小，不出横向滚动条。
 */
import { useEffect, useRef, useState } from 'react';
import { bodyElements, marked } from './docxMarks';
import type { DocxLocate } from './locate';

const MARK = 'kw-docx-mark';

/** 页面比面板宽时按宽度缩小（CSS zoom，排版不变）。 */
function fitWidth(host: HTMLElement) {
  const wrapper = host.querySelector<HTMLElement>('.docx-wrapper');
  const page = host.querySelector<HTMLElement>('section.docx');
  if (!wrapper || !page) return;
  wrapper.style.zoom = '';
  const room = host.clientWidth;
  const needed = page.offsetWidth + 32;
  wrapper.style.zoom = room > 0 && needed > room ? String(room / needed) : '';
}

export default function DocxView({
  data,
  focus,
}: {
  readonly data: ArrayBuffer;
  readonly focus?: DocxLocate;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState<ArrayBuffer>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const node = host.current;
    if (!node) return undefined;
    let live = true;
    node.replaceChildren();
    import('docx-preview')
      .then(({ renderAsync }) =>
        renderAsync(data, node, undefined, {
          breakPages: false,
          inWrapper: true,
          ignoreLastRenderedPageBreak: true,
          renderHeaders: true,
          renderFooters: true,
          useBase64URL: true,
        }),
      )
      .then(
        () => {
          if (!live) return;
          fitWidth(node);
          setRendered(data);
        },
        () => live && setFailed(true),
      );
    const resize =
      typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(() => fitWidth(node));
    resize?.observe(node);
    return () => {
      live = false;
      resize?.disconnect();
    };
  }, [data]);

  useEffect(() => {
    const node = host.current;
    if (!node || rendered !== data) return;
    node.querySelectorAll(`.${MARK}`).forEach((el) => el.classList.remove(MARK));
    if (!focus) return;
    const targets = marked(bodyElements(node), focus);
    targets.forEach((el) => el.classList.add(MARK));
    targets[0]?.scrollIntoView?.({ block: 'start' });
  }, [rendered, data, focus]);

  if (failed) return <p className="kw-preview-problem">这份 Word 预览不了，可以下载原件查看</p>;
  return <div ref={host} className="kw-docx-view" />;
}
