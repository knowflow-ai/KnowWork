/**
 * 文件预览抽屉（界面规范：文件产物「可在右侧预览」；内容宽于 1000 的查看界面用抽屉）。
 * 预览只读；顶部右侧放文件自己给的操作（服务端文件是「下载」，本机文件是「打开」）。Word（.docx）在浏览器里直接渲染原件，
 * 知识库里的旧格式 Word（.doc）渲染入库时转出的 docx；PPT 等要转成 PDF 的，第一次可能要十几秒，期间显示骨架屏。
 * 抽屉只认「怎么读」，不认地址（方案 7.7.3：网页与桌面同一套）：产物、知识库文档、本机会话里的文件都转成 PreviewFile，
 * 服务端的用 serverFile.tsx 包一层。知识库多传一个 locate 定位并高亮，产物不传。
 * 不要抽屉、直接嵌在页面里时用 PreviewPane。
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileBlank } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { ApiError, readableError } from '../../api/http';
import { Empty } from '../../ui/Empty';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { toast } from '../../ui/toastStore';
import { FileLinks, Markdown } from '../chat/components/Markdown';
import { HtmlModeSwitch } from './HtmlModeSwitch';
import { TextView } from './TextView';
import type { PreviewLocate } from './locate';
import { extensionOf, isEditableFormat, previewKindOf, type PreviewKind } from './previewKind';
import { cx } from '../../ui/cx';
import { DocTabs } from '../../ui/DocTabs';
import { FileKindIcon } from '../../ui/FileKindIcon';
import { BlobImage } from './BlobImage';
import {
  busyMessage,
  busyOf,
  editorMessageOf,
  IDLE,
  isIdle,
  type EditorAiEdit,
  type EditorBusy,
} from './editorBridge';

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
  /** 文件在哪（并排编辑区的标签上悬停时显示，同名的文件好区分）；不给就显示名字 */
  readonly location?: string;
  /** 原件字节：表格、文本、图片、HTML、Word（.docx）的预览，以及没给 pdf 时的 PDF 原件 */
  readonly read: Read;
  /** 给 PDF 阅读器的字节：Word、PPT 等转成的 PDF（PDF 原件直接读原件，不走它）；不给就只有 PDF 原件能用 PDF 阅读器 */
  readonly pdf?: Read;
  /** 给了就按 Word 渲染这份 docx：知识库入库时把旧格式 Word（.doc）转成了 docx（浏览器读不了 .doc） */
  readonly docx?: Read;
  /** 原件可能随时被改（本机文件）：关掉抽屉就丢，下次打开重新读；不给就当原件不变，读一次缓存着 */
  readonly mutable?: boolean;
  /** 这份文件为什么只能看、不能编辑（例如云端对话里的 Office 文件）：只读预览的上面写一行 */
  readonly readOnlyNote?: string;
  /** 顶部的操作（下载、打开）；不能预览、读不出来时也摆在说明下面；服务端说文件不在了（404、410）就都不摆 */
  readonly actions?: ReactNode;
  /**
   * 能在编辑器里打开时给（桌面端的本机对话、装了在线编辑的云端对话）：返回编辑器地址，抽屉里直接是编辑器（设计稿：预览直接
   * 变编辑）；返回 null 照旧只读预览，出错时只读预览上方写明原因
   */
  readonly edit?: () => Promise<{ readonly url: string } | null>;
  /** 编辑器里存了盘：云端把它存回对话，成为新的一版（本机不给，文件就在磁盘上） */
  readonly saved?: () => Promise<void>;
  /** 编辑器里的「AI 修改」：选中的原文与要求交给对话（editorBridge.ts） */
  readonly aiEdit?: (request: EditorAiEdit) => void;
  /** 编辑器外面（这一轮里 AI）改了这个文件：每轮结束通知一次；返回取消订阅 */
  readonly editorChanges?: (
    listener: (change: { readonly changed: boolean; readonly unsaved: boolean }) => void,
  ) => () => void;
  /** 丢掉编辑器里没存的改动，好重新载入磁盘上的版本 */
  readonly discardEdits?: () => Promise<void>;
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
  const [actions, onGone] = useHeaderActions(file);
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
          {actions}
        </>
      }
    >
      <PreviewContent file={file} scripted={scripted} locate={locate} onGone={onGone} />
    </Sheet>
  );
}

/** 铺满不留边的阅读器（它们自己滚）：PDF、Word、网页 */
const EDGE_TO_EDGE: readonly PreviewKind[] = ['pdf', 'docx', 'html'];

/** 这个文件是不是在编辑器里打开（能编辑的 Office 文件）：对话页据此把它并排放在对话右边，而不是盖上抽屉 */
export function opensInEditor(file: PreviewFile): boolean {
  return file.edit !== undefined && file.docx === undefined && isEditableFormat(file.name);
}

/**
 * 并排的编辑区（照 ChatGPT Canvas）：能编辑的本机 Office 文件打开在对话右边，对话一直看得到、能输入。编辑器里的「AI 修改」
 * 发到对话，AI 的回答、追问、确认就在旁边，不用关掉文档去找。
 * 打开的文件各占一个标签，排在上沿（照 DSH 官方右侧栏的标签条，ui/DocTabs）：点标签切换，每个标签自己关，右键可以关闭其他、
 * 关闭右侧；条的右端是切换类控件和当前文件的操作。看过的标签切走、整栏收起（hidden）都照样挂着（同官方 keepMounted），
 * 编辑器不用重开、没存的改动还在；没看过的标签（例如刷新后恢复的）不先挂，点到才打开，收起时也不在后台打开当前那个。
 * 开在哪、关掉以后切到哪由调用方按 ui/docTabList.ts 算，关掉最后一个由调用方收起编辑区。
 */
export function FileEditPane({
  files,
  activeId,
  hidden = false,
  onSelect,
  onClose,
  tools,
  aside,
}: {
  readonly files: readonly PreviewFile[];
  readonly activeId: string;
  /** 整栏收起：标签和看过的内容照样挂着，只是不显示 */
  readonly hidden?: boolean;
  readonly onSelect: (id: string) => void;
  /** 关掉这几个标签 */
  readonly onClose: (ids: readonly string[]) => void;
  /** 条的右端、文件操作之前的切换类控件（例如显示目录树） */
  readonly tools?: ReactNode;
  /** 正文右边的一栏（例如工作文件夹的目录树，照官方：预览旁边的目录树） */
  readonly aside?: ReactNode;
}) {
  // 服务端说不在了的文件（见 useHeaderActions）：它的标签上不再给下载
  const [gone, setGone] = useState<ReadonlySet<string>>(() => new Set());
  const onGone = useCallback((id: string) => setGone((current) => new Set([...current, id])), []);
  const active = files.find((file) => file.id === activeId) ?? files[0];
  // 显示过的标签：只挂这些（同官方「未访问的正文不会提前挂载」）
  const [visited, setVisited] = useState<ReadonlySet<string>>(() =>
    hidden || active === undefined ? new Set() : new Set([active.id]),
  );
  const shownId = hidden ? undefined : active?.id;
  // 渲染时记下刚显示的（React 推荐的「渲染中按新属性调整状态」写法，不在 effect 里再渲染一遍）
  if (shownId !== undefined && !visited.has(shownId)) setVisited(new Set([...visited, shownId]));
  if (active === undefined) return null;
  const actions = gone.has(active.id) ? undefined : active.actions;
  return (
    <section className="kw-file-pane" aria-label={active.name} hidden={hidden}>
      <DocTabs
        label="打开的文件"
        tabs={files.map((file) => ({
          id: file.id,
          title: file.name,
          icon: <FileKindIcon name={file.name} />,
          ...(file.location ? { hint: file.location } : {}),
        }))}
        activeId={active.id}
        onSelect={onSelect}
        onClose={onClose}
        end={
          tools || actions ? (
            <>
              {tools}
              {actions}
            </>
          ) : undefined
        }
      />
      <div className="kw-file-pane-main">
        {files.map((file) =>
          // 编辑器、PDF、Word、网页铺满（它们自己滚）；别的（Markdown、表格、文本、图片）同抽屉留边、在这里滚
          !visited.has(file.id) && file.id !== shownId ? null : (
            <div
              key={file.id}
              role="tabpanel"
              aria-label={file.name}
              hidden={file.id !== active.id}
              className={cx(
                'kw-file-pane-body',
                !opensInEditor(file) && !EDGE_TO_EDGE.includes(kindOf(file)) && 'is-padded',
              )}
            >
              <PreviewContent file={file} scripted={false} onGone={onGone} />
            </div>
          ),
        )}
        {aside ? <aside className="kw-file-pane-aside">{aside}</aside> : null}
      </div>
    </section>
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

/** 正文（编辑器、只读预览）共用的参数 */
interface ContentProps {
  readonly file: PreviewFile;
  readonly scripted: boolean;
  readonly locate?: PreviewLocate;
  /** 读文件时服务端说它不在了：告诉外层收起头部的下载（见 useHeaderActions） */
  readonly onGone?: (id: string) => void;
}

/**
 * 服务端说文件本身不在了（已随执行环境回收、内容不可用 410，找不到 404）：下载拿的是同一份，也拿不到，不给下载。
 * 别的失败（转换失败、暂时读不了）原件可能还在，照旧给下载。
 */
function isFileGone(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 410);
}

/** 头部的文件操作：正文报告文件不在了就收起；按文件记，换一个文件自动复位 */
function useHeaderActions(file: PreviewFile | undefined) {
  const [goneId, setGoneId] = useState<string>();
  const actions = file !== undefined && goneId !== file.id ? file.actions : undefined;
  return [actions, setGoneId] as const;
}

function PreviewContent(props: ContentProps) {
  // 能编辑的 Office 文件先试编辑器；知识库定位要高亮原文，只读预览才做得到
  if (props.file.edit && opensInEditor(props.file) && props.locate === undefined)
    return <EditableContent {...props} edit={props.file.edit} />;
  return <ReadOnlyContent {...props} note={props.file.readOnlyNote} />;
}

/** 等多久还没打开就说一句在等什么 */
export const EDITOR_STARTING_HINT_MS = 2000;

/**
 * 等编辑器打开：编辑器按人起，这个人第一次打开（或空闲回收以后）要现起一个，十几秒（2026-11 测试部署实测约 16 秒）。
 * 等了一会儿还没好就在上面说一句，不让人对着骨架干等；已经起着的一两秒就开了，看不到这句。
 */
function EditorStarting() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), EDITOR_STARTING_HINT_MS);
    return () => window.clearTimeout(timer);
  }, []);
  const skeleton = <Skeleton rows={8} className="kw-preview-state" />;
  if (!slow) return skeleton;
  return (
    <div className="kw-preview-noted">
      <p className="kw-preview-note" role="status">
        正在启动编辑器，第一次打开要十几秒
      </p>
      <div className="kw-preview-noted-body">{skeleton}</div>
    </div>
  );
}

/** AI 正在改的那几块上的标签 */
const BUSY_LABEL = 'AI 正在改';

/**
 * 编辑器嵌在抽屉里（它在另一个来源，保存直接写回原件）；没有编辑器或打不开时退回只读预览。
 * 和对话的联动：编辑器里选中一段点「AI 修改」交给对话，那几块标上「AI 正在改」，AI 的改动一落进文档就收掉；
 * 这一轮结束，AI 改了文件就重新载入编辑器显示它的修订，编辑器里又有没存的改动就让用户选；没改就收掉标记。
 */
function EditableContent({
  edit,
  ...props
}: ContentProps & {
  readonly edit: () => Promise<{ readonly url: string } | null>;
}) {
  const { aiEdit, editorChanges, discardEdits, saved } = props.file;
  // 重新载入：换一个新的编辑器页面（编辑器从磁盘重新读）
  const [generation, setGeneration] = useState(0);
  const [conflict, setConflict] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const busy = useRef<EditorBusy>(IDLE);
  const session = useQuery({
    queryKey: ['file-editor', props.file.id, generation],
    queryFn: edit,
    // 每次打开重新要地址：编辑器可能重启过、换了端口
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const origin = session.data ? new URL(session.data.url).origin : null;
  const post = useCallback(
    (next: EditorBusy) => {
      busy.current = next;
      if (origin) frame.current?.contentWindow?.postMessage(busyMessage(next, BUSY_LABEL), origin);
    },
    [origin],
  );
  const reload = useCallback(() => {
    busy.current = IDLE;
    setConflict(false);
    setGeneration((current) => current + 1);
  }, []);

  useEffect(() => {
    if (!origin) return;
    const onMessage = (event: MessageEvent) => {
      // 只认嵌在这里的那个编辑器发来的
      if (event.origin !== origin || event.source !== frame.current?.contentWindow) return;
      const message = editorMessageOf(event.data);
      if (message === 'ready') {
        if (!isIdle(busy.current)) post(busy.current);
      } else if (message === 'disconnected') {
        // 编辑服务断了（重启过、被结束）：重新要地址（桌面端会把它重新起来）再载入，用户看不到断线
        reload();
      } else if (message === 'agent-edited') {
        // AI 的改动真的落进了文档（编辑器在工具确实改了文档时才发）：马上收掉标记，不等这一轮结束。
        // 模型改完还在反复核对、想很久时，标记不再一直挂在已经改好的地方
        post(IDLE);
      } else if (message === 'saved') {
        // 存了盘：云端把编辑器里的文件存回对话；存不回去要让人知道，编辑器里的改动还在，可以再存一次
        saved?.().catch((error: unknown) =>
          toast.error(readableError(error, '改动没能存回对话，请再保存一次')),
        );
      } else if (message !== null && aiEdit) {
        aiEdit(message);
        if (message.send) post(busyOf(message, BUSY_LABEL));
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [origin, aiEdit, saved, post, reload]);

  useEffect(
    () =>
      editorChanges?.(({ changed, unsaved }) => {
        if (!changed) post(IDLE);
        else if (unsaved) {
          post(IDLE);
          setConflict(true);
        } else reload();
      }),
    [editorChanges, post, reload],
  );

  const loadChanged = async () => {
    try {
      await discardEdits?.();
      reload();
    } catch (error) {
      toast.error(readableError(error, '没能载入改过的版本，请稍后重试'));
    }
  };

  if (session.isPending) return <EditorStarting />;
  if (session.isError)
    return <ReadOnlyContent {...props} note={readableError(session.error, '编辑器暂时打不开，先只读预览')} />;
  if (session.data === null) return <ReadOnlyContent {...props} />;
  return (
    <div className="kw-editor">
      {conflict ? (
        <div className="kw-editor-notice" role="status">
          <span>AI 改了这个文件，这里还有没保存的改动。</span>
          <Button color="secondary" variant="outline" size="sm" onClick={() => void loadChanged()}>
            载入 AI 改过的版本
          </Button>
          <Button color="secondary" variant="ghost" size="sm" onClick={() => setConflict(false)}>
            保留我的
          </Button>
        </div>
      ) : null}
      <iframe
        key={generation}
        ref={frame}
        className="kw-editor-frame"
        src={session.data.url}
        title={`编辑 ${props.file.name}`}
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}

/**
 * 只读预览。note 是上方的一句说明（为什么只能看：来源只给预览，或编辑器打不开的原因），只跟着读出来的内容一起显示：
 * 文件本身也读不出来时（例如已随执行环境回收），编辑器打不开的原因和读不出来的原因往往是同一句，只说读不出来的那句。
 */
function ReadOnlyContent({
  file,
  scripted,
  locate,
  note,
  onGone,
}: ContentProps & {
  readonly note?: string;
}) {
  const kind = kindOf(file);
  const bytes = useBytes(file, kind);
  const gone = bytes.isError && isFileGone(bytes.error);
  useEffect(() => {
    if (gone) onGone?.(file.id);
  }, [gone, onGone, file.id]);
  const actions = gone ? undefined : file.actions;
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
  const body = (
    <Suspense fallback={<Skeleton rows={8} className="kw-preview-state" />}>
      <Body file={file} kind={kind} data={bytes.data} scripted={scripted} locate={locate} />
    </Suspense>
  );
  if (!note) return body;
  return (
    <div className="kw-preview-noted">
      <p className="kw-preview-note">{note}</p>
      <div className="kw-preview-noted-body">{body}</div>
    </div>
  );
}
