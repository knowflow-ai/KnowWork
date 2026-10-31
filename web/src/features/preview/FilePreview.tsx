/**
 * 文件预览抽屉（界面规范：文件产物「可在右侧预览」；内容宽于 1000 的查看界面用抽屉）。
 * 预览只读；下载按钮在抽屉顶部右侧。Word（.docx）在浏览器里直接渲染原件，知识库里的旧格式 Word（.doc）渲染入库时
 * 转出的 docx；PPT 第一次预览要在沙箱里转换，可能要十几秒，期间显示骨架屏。
 * 产物预览和知识库的引用预览共用这一个抽屉；知识库多传一个 locate 定位并高亮，产物不传。
 * 不要抽屉、直接嵌在页面里时用 PreviewPane。
 */
import { lazy, Suspense, useState } from 'react';
import { ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Download, FileBlank } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { readableError, requestBytes } from '../../api/http';
import { Empty } from '../../ui/Empty';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { Markdown } from '../chat/components/Markdown';
import { HtmlModeSwitch } from './HtmlModeSwitch';
import { TextView } from './TextView';
import type { PreviewLocate } from './locate';
import { pdfSource, previewKindOf, type PreviewKind } from './previewKind';
import { FileKindIcon } from '../../ui/FileKindIcon';
import { BlobImage } from './BlobImage';

const PdfView = lazy(() => import('./PdfView'));
const DocxView = lazy(() => import('./DocxView'));
const SheetView = lazy(() => import('./SheetView'));
const HtmlView = lazy(() => import('./HtmlView'));

export interface PreviewFile {
  readonly name: string;
  /** 原件地址：下载按钮与表格、文本、图片的预览都取它 */
  readonly url: string;
  /** PDF 阅读器取哪里；不给就是 url 后面接 /preview（产物预览的接口），知识库文档的预览接口不在这个位置 */
  readonly pdfUrl?: string;
  /** 给了就按 Word 渲染这里的 docx：知识库入库时把旧格式 Word（.doc）转成了 docx（浏览器读不了 .doc） */
  readonly docxUrl?: string;
}

/** 用哪种阅读器：给了 docxUrl 就按 Word，其余看扩展名。 */
function kindOf(file: PreviewFile): PreviewKind {
  return file.docxUrl ? 'docx' : previewKindOf(file.name);
}

const WIDTH = 1000;

function useBytes(file: PreviewFile, kind: PreviewKind) {
  const source =
    kind === 'pdf'
      ? (file.pdfUrl ?? pdfSource(file.url))
      : kind === 'docx'
        ? (file.docxUrl ?? file.url)
        : file.url;
  return useQuery({
    queryKey: ['file-preview', source],
    queryFn: ({ signal }) => requestBytes(source, signal),
    enabled: kind !== 'none',
    staleTime: Infinity,
    retry: false,
  });
}

function Body({
  file,
  kind,
  data,
  scripted,
  locate,
}: {
  readonly file: PreviewFile;
  readonly kind: PreviewKind;
  readonly data: ArrayBuffer;
  readonly scripted: boolean;
  readonly locate?: PreviewLocate;
}) {
  const text = () => new TextDecoder('utf-8').decode(data);
  switch (kind) {
    case 'pdf':
      return <PdfView data={data} focus={locate?.type === 'pdf' ? locate : undefined} />;
    case 'docx':
      return <DocxView data={data} focus={locate?.type === 'docx' ? locate : undefined} />;
    case 'sheet':
      return <SheetView data={data} focus={locate?.type === 'sheet' ? locate : undefined} />;
    case 'html':
      return <HtmlView data={data} scripted={scripted} />;
    case 'markdown':
      // 带定位时按原文显示才标得出引用的那段
      return locate?.type === 'text' ? <TextView text={text()} focus={locate} /> : <Markdown text={text()} />;
    case 'text':
      return <TextView text={text()} focus={locate?.type === 'text' ? locate : undefined} />;
    case 'image':
      return <BlobImage className="kw-preview-image" data={data} alt={file.name} />;
    default:
      return null;
  }
}

export function FilePreview({
  file,
  onClose,
  locate,
}: {
  readonly file: PreviewFile | undefined;
  readonly onClose: () => void;
  readonly locate?: PreviewLocate;
}) {
  // 记下是哪个文件开了脚本：换一个文件预览时自动回到安全预览（关掉抽屉时整个卸载，状态随之清掉）
  const [scriptedUrl, setScriptedUrl] = useState<string>();
  const html = file !== undefined && previewKindOf(file.name) === 'html';
  // HTML、PDF、Word 自己管滚动和留白（PDF 阅读器要贴边放缩放控件与页面底色，Word 的渲染自带灰底和页边）
  const edgeToEdge = html || (file !== undefined && ['pdf', 'docx'].includes(kindOf(file)));
  const scripted = file !== undefined && scriptedUrl === file.url;
  if (!file) return null;
  return (
    <Sheet
      title={
        <span className="kw-labeled">
          <FileKindIcon name={file.name} />
          {file.name}
        </span>
      }
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
      <PreviewContent file={file} scripted={scripted} locate={locate} />
    </Sheet>
  );
}

/** 不带抽屉的预览：知识库文档页的左栏放原件（只读，HTML 不开脚本），locate 变了就换位置高亮。 */
export function PreviewPane({
  file,
  locate,
}: {
  readonly file: PreviewFile;
  readonly locate?: PreviewLocate;
}) {
  const kind = kindOf(file);
  return (
    <div
      className={`kw-preview-pane${kind === 'pdf' || kind === 'docx' || kind === 'html' ? ' is-flush' : ''}`}
    >
      <PreviewContent file={file} scripted={false} locate={locate} />
    </div>
  );
}

function PreviewContent({
  file,
  scripted,
  locate,
}: {
  readonly file: PreviewFile;
  readonly scripted: boolean;
  readonly locate?: PreviewLocate;
}) {
  const kind = kindOf(file);
  const bytes = useBytes(file, kind);
  if (kind === 'none')
    return <Empty area icon={<FileBlank />} description="这种文件不支持预览，请下载后查看" />;
  if (bytes.isPending) return <Skeleton rows={8} className="kw-preview-state" />;
  if (bytes.isError)
    return (
      <p className="kw-preview-problem" role="alert">
        {readableError(bytes.error, '预览没有打开，请稍后重试')}
      </p>
    );
  return (
    <Suspense fallback={<Skeleton rows={8} className="kw-preview-state" />}>
      <Body file={file} kind={kind} data={bytes.data} scripted={scripted} locate={locate} />
    </Suspense>
  );
}
