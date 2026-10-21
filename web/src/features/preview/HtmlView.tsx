/**
 * HTML 预览正文（两种模式见 htmlDocument.ts），铺满抽屉正文，不加边框。
 * 模式由抽屉顶部的切换控件决定；切换时卸掉原来的 iframe，正在跑的脚本随之结束，Blob 地址在卸载时释放。
 */
import { useEffect, useMemo } from 'react';
import { decodeHtml, staticHtmlDocument } from './htmlDocument';

const TITLE = 'HTML 文档预览';

function StaticFrame({ data }: { readonly data: ArrayBuffer }) {
  const html = useMemo(() => {
    try {
      return staticHtmlDocument(data);
    } catch {
      return undefined;
    }
  }, [data]);
  if (html === undefined)
    return (
      <p className="kw-preview-problem" role="alert">
        无法预览这份 HTML 文档
      </p>
    );
  return <iframe className="kw-preview-html" title={TITLE} srcDoc={html} sandbox="" />;
}

function ScriptedFrame({ data }: { readonly data: ArrayBuffer }) {
  const url = useMemo(() => URL.createObjectURL(new Blob([decodeHtml(data)], { type: 'text/html' })), [data]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return <iframe key={url} className="kw-preview-html" title={TITLE} src={url} sandbox="allow-scripts" />;
}

export default function HtmlView({
  data,
  scripted,
}: {
  readonly data: ArrayBuffer;
  readonly scripted: boolean;
}) {
  return scripted ? <ScriptedFrame data={data} /> : <StaticFrame data={data} />;
}
