/**
 * 文件预览抽屉（界面规范：文件产物「可在右侧预览」；内容宽于 1000 的查看界面用抽屉）。
 * 预览只读；下载按钮在抽屉顶部右侧。Word、PPT 第一次预览要在沙箱里转换，可能要十几秒，期间显示骨架屏。
 */
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Download } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { readableError, requestBytes } from '../../api/http';
import { Empty } from '../../ui/Empty';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { Markdown } from '../chat/components/Markdown';
import { HtmlModeSwitch } from './HtmlModeSwitch';
import { pdfSource, previewKindOf, type PreviewKind } from './previewKind';

const PdfView = lazy(() => import('./PdfView'));
const SheetView = lazy(() => import('./SheetView'));
const HtmlView = lazy(() => import('./HtmlView'));

export interface PreviewFile {
  readonly name: string;
  readonly url: string;
}

const WIDTH = 1000;

function useBytes(file: PreviewFile, kind: PreviewKind) {
  const source = kind === 'pdf' ? pdfSource(file.url) : file.url;
  return useQuery({
    queryKey: ['file-preview', source],
    queryFn: ({ signal }) => requestBytes(source, signal),
    enabled: kind !== 'none',
    staleTime: Infinity,
    retry: false,
  });
}

function ImageView({ data, name }: { readonly data: ArrayBuffer; readonly name: string }) {
  const url = useMemo(() => URL.createObjectURL(new Blob([data])), [data]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return <img className="kw-preview-image" src={url} alt={name} />;
}

function Body({
  file,
  kind,
  data,
  scripted,
}: {
  readonly file: PreviewFile;
  readonly kind: PreviewKind;
  readonly data: ArrayBuffer;
  readonly scripted: boolean;
}) {
  const text = () => new TextDecoder('utf-8').decode(data);
  switch (kind) {
    case 'pdf':
      return <PdfView data={data} />;
    case 'sheet':
      return <SheetView data={data} />;
    case 'html':
      return <HtmlView data={data} scripted={scripted} />;
    case 'markdown':
      return <Markdown text={text()} />;
    case 'text':
      return <pre className="kw-preview-text">{text()}</pre>;
    case 'image':
      return <ImageView data={data} name={file.name} />;
    default:
      return null;
  }
}

export function FilePreview({
  file,
  onClose,
}: {
  readonly file: PreviewFile | undefined;
  readonly onClose: () => void;
}) {
  // 记下是哪个文件开了脚本：换一个文件预览时自动回到安全预览（关掉抽屉时整个卸载，状态随之清掉）
  const [scriptedUrl, setScriptedUrl] = useState<string>();
  const html = file !== undefined && previewKindOf(file.name) === 'html';
  // HTML 与 PDF 自己管滚动和留白（PDF 阅读器要贴边放缩放控件与页面底色）
  const edgeToEdge = html || (file !== undefined && previewKindOf(file.name) === 'pdf');
  const scripted = file !== undefined && scriptedUrl === file.url;
  if (!file) return null;
  return (
    <Sheet
      title={file.name}
      width={WIDTH}
      flush={edgeToEdge}
      onClose={onClose}
      extra={
        <>
          {html ? (
            <HtmlModeSwitch
              scripted={scripted}
              onChange={(on) => setScriptedUrl(on ? file.url : undefined)}
            />
          ) : null}
          <ButtonLink
            as="a"
            href={file.url}
            download={file.name}
            color="secondary"
            variant="outline"
            size="sm"
          >
            <Download />
            下载
          </ButtonLink>
        </>
      }
    >
      <PreviewContent file={file} scripted={scripted} />
    </Sheet>
  );
}

function PreviewContent({ file, scripted }: { readonly file: PreviewFile; readonly scripted: boolean }) {
  const kind = previewKindOf(file.name);
  const bytes = useBytes(file, kind);
  if (kind === 'none') return <Empty description="这种文件不支持预览，请下载后查看" />;
  if (bytes.isPending) return <Skeleton rows={8} className="kw-preview-state" />;
  if (bytes.isError)
    return (
      <p className="kw-preview-problem" role="alert">
        {readableError(bytes.error, '预览没有打开，请稍后重试')}
      </p>
    );
  return (
    <Suspense fallback={<Skeleton rows={8} className="kw-preview-state" />}>
      <Body file={file} kind={kind} data={bytes.data} scripted={scripted} />
    </Suspense>
  );
}
