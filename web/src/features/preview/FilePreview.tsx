/**
 * 文件预览抽屉（界面规范：文件产物「可在右侧预览」；内容宽于 1000 的查看界面用抽屉）。
 * 预览只读；顶部右侧放文件自己给的操作（服务端文件是「下载」，本机文件是「打开」）。Word（.docx）在浏览器里直接渲染原件，
 * 知识库里的旧格式 Word（.doc）渲染入库时转出的 docx；PPT 等要转成 PDF 的，第一次可能要十几秒，期间显示骨架屏。
 * 抽屉只认「怎么读」，不认地址（方案 7.7.3：网页与桌面同一套）：产物、知识库文档、本机会话里的文件都转成 PreviewFile，
 * 服务端的用 serverFile.tsx 包一层。知识库多传一个 locate 定位并高亮，产物不传。
 * 不要抽屉、直接嵌在页面里时用 PreviewPane。
 */
import { lazy, Suspense, useState, type ReactNode } from 'react';
import { FileBlank } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { readableError } from '../../api/http';
import { Empty } from '../../ui/Empty';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { FileLinks, Markdown } from '../chat/components/Markdown';
import { HtmlModeSwitch } from './HtmlModeSwitch';
import { TextView } from './TextView';
import type { PreviewLocate } from './locate';
import { extensionOf, previewKindOf, type PreviewKind } from './previewKind';
import { FileKindIcon } from '../../ui/FileKindIcon';
import { BlobImage } from './BlobImage';

const PdfView = lazy(() => import('./PdfView'));
const DocxView = lazy(() => import('./DocxView'));
const SheetView = lazy(() => import('./SheetView'));
const HtmlView = lazy(() => import('./HtmlView'));

type Read = (signal?: AbortSignal) => Promise<ArrayBuffer>;

/** 预览抽屉要的文件：名字、标识和怎么读。 */
export interface PreviewFile {
  readonly name: string;
  /** 区分是哪个文件（缓存、换文件时复位用）：服务端文件用地址，本机文件用对话加路径 */
  readonly id: string;
  /** 原件字节：表格、文本、图片、HTML、Word（.docx）的预览，以及没给 pdf 时的 PDF 原件 */
  readonly read: Read;
  /** 给 PDF 阅读器的字节：Word、PPT 等转成的 PDF（PDF 原件直接读原件，不走它）；不给就只有 PDF 原件能用 PDF 阅读器 */
  readonly pdf?: Read;
  /** 给了就按 Word 渲染这份 docx：知识库入库时把旧格式 Word（.doc）转成了 docx（浏览器读不了 .doc） */
  readonly docx?: Read;
  /** 原件可能随时被改（本机文件）：关掉抽屉就丢，下次打开重新读；不给就当原件不变，读一次缓存着 */
  readonly mutable?: boolean;
  /** 顶部的操作（下载、打开）；不能预览、读不出来时也摆在说明下面 */
  readonly actions?: ReactNode;
}

/** 用哪种阅读器：给了 docx 就按 Word，其余看扩展名；要转 PDF 的类型没给 pdf 就不能预览。 */
function kindOf(file: PreviewFile): PreviewKind {
  if (file.docx) return 'docx';
  const kind = previewKindOf(file.name);
  return kind === 'pdf' && !file.pdf && extensionOf(file.name) !== 'pdf' ? 'none' : kind;
}

/** 这种阅读器从哪读：PDF 原件读原件，要转换的用转换结果；Word 先用转好的 docx；其余读原件 */
function loaderOf(file: PreviewFile, kind: PreviewKind): readonly [string, Read] {
  if (kind === 'pdf')
    return file.pdf && extensionOf(file.name) !== 'pdf' ? ['pdf', file.pdf] : ['read', file.read];
  if (kind === 'docx') return file.docx ? ['docx', file.docx] : ['read', file.read];
  return ['read', file.read];
}

const WIDTH = 1000;

function useBytes(file: PreviewFile, kind: PreviewKind) {
  const [which, load] = loaderOf(file, kind);
  return useQuery({
    queryKey: ['file-preview', file.id, which],
    queryFn: ({ signal }) => load(signal),
    enabled: kind !== 'none',
    // 服务端的原件不会变，读一次缓存着；本机文件可能刚被改过，关掉抽屉就丢，下次打开重新读
    staleTime: file.mutable ? 0 : Infinity,
    gcTime: file.mutable ? 0 : undefined,
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
      // 文件里的相对链接相对的是这个文件，不是对话的工作目录：这里不接文件链接，只显示文字
      return locate?.type === 'text' ? (
        <TextView text={text()} focus={locate} />
      ) : (
        <FileLinks>
          <Markdown text={text()} />
        </FileLinks>
      );
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
  const [scriptedId, setScriptedId] = useState<string>();
  const html = file !== undefined && previewKindOf(file.name) === 'html';
  // HTML、PDF、Word 自己管滚动和留白（PDF 阅读器要贴边放缩放控件与页面底色，Word 的渲染自带灰底和页边）
  const edgeToEdge = html || (file !== undefined && ['pdf', 'docx'].includes(kindOf(file)));
  const scripted = file !== undefined && scriptedId === file.id;
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
            <HtmlModeSwitch scripted={scripted} onChange={(on) => setScriptedId(on ? file.id : undefined)} />
          ) : null}
          {file.actions}
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
  const { actions } = file;
  if (kind === 'none')
    return <Empty area icon={<FileBlank />} description="这种文件不能在这里预览" action={actions} />;
  if (bytes.isPending) return <Skeleton rows={8} className="kw-preview-state" />;
  if (bytes.isError)
    return (
      <>
        <p className="kw-preview-problem" role="alert">
          {readableError(bytes.error, '预览没有打开，请稍后重试')}
        </p>
        {actions ? <div className="kw-preview-problem-actions">{actions}</div> : null}
      </>
    );
  return (
    <Suspense fallback={<Skeleton rows={8} className="kw-preview-state" />}>
      <Body file={file} kind={kind} data={bytes.data} scripted={scripted} locate={locate} />
    </Suspense>
  );
}
