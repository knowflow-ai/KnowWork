import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import { utils, write } from 'xlsx';
import userEvent from '../../test/user';
import { ApiError } from '../../api/http';
import { jsonResponse, renderWithApp, routeFetch } from '../../test/render';
import { FileCard } from '../chat/components/FileCard';
import { EDITOR_STARTING_HINT_MS, FilePreview, opensInEditor } from './FilePreview';
import { serverFile } from './serverFile';
import { staticHtmlDocument } from './htmlDocument';
import { pdfSource, previewKindOf } from './previewKind';
import { parseSheets } from './sheets';

vi.mock('./PdfView', () => ({
  default: ({ data }: { data: ArrayBuffer }) => <div>PDF 阅读器：{new TextDecoder().decode(data)}</div>,
}));
vi.mock('./DocxView', () => ({
  default: ({ data }: { data: ArrayBuffer }) => <div>Word 原件：{new TextDecoder().decode(data)}</div>,
}));

afterEach(() => vi.unstubAllGlobals());

const URL_DOCX = '/api/workspaces/default/conversations/c1/artifacts/art_1';

describe('预览类型', () => {
  it('Word（.docx）直接渲染原件；旧格式 Word、PPT、PDF 走 PDF 阅读器；表格、Markdown、文本、图片各自渲染，其余只能下载', () => {
    expect(['a.docx', 'a.DOCX'].map(previewKindOf)).toEqual(['docx', 'docx']);
    expect(['a.DOC', 'b.pptx', 'c.pdf'].map(previewKindOf)).toEqual(['pdf', 'pdf', 'pdf']);
    expect(['a.xlsx', 'b.csv'].map(previewKindOf)).toEqual(['sheet', 'sheet']);
    expect(['报告.html', 'b.HTM'].map(previewKindOf)).toEqual(['html', 'html']);
    expect(['a.md', 'b.txt', 'c.json', 'd.png', 'e.zip', 'noext'].map(previewKindOf)).toEqual([
      'markdown',
      'text',
      'text',
      'image',
      'none',
      'none',
    ]);
    expect(pdfSource(URL_DOCX)).toBe(`${URL_DOCX}/preview`);
  });

  it('表格按工作表解析，整张表都读进来', () => {
    const book = utils.book_new();
    utils.book_append_sheet(
      book,
      utils.aoa_to_sheet([
        ['区域', '销售额'],
        ['华东', 1200],
      ]),
      '汇总',
    );
    utils.book_append_sheet(book, utils.aoa_to_sheet(Array.from({ length: 600 }, (_, i) => [i])), '明细');
    const sheets = parseSheets(write(book, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer);
    expect(sheets.map((s) => [s.name, s.rows.length, s.firstRow])).toEqual([
      ['汇总', 2, 1],
      ['明细', 600, 1],
    ]);
    expect(sheets[0]!.rows[1]).toEqual(['华东', 1200]);
  });
});

const REPORT = `<html><head><link rel="stylesheet" href="https://cdn.example.com/a.css">
<script src="https://cdn.example.com/chart.js"></script><style>h1{color:red}</style></head>
<body><h1 onclick="steal()">季度报告</h1><a href="javascript:alert(1)">链接</a>
<img src="https://evil.example.com/x.png"><iframe src="https://evil.example.com"></iframe>
<script>fetch('/api/auth/me')</script></body></html>`;

describe('HTML 静态预览', () => {
  it('去掉脚本、事件、链接、外部资源和嵌套框架，保留正文与内联样式，CSP 放在 head 第一位', () => {
    const html = staticHtmlDocument(new TextEncoder().encode(REPORT).buffer as ArrayBuffer);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.head.firstElementChild?.getAttribute('http-equiv')).toBe('Content-Security-Policy');
    expect(doc.head.firstElementChild?.getAttribute('content')).toContain("script-src 'none'");
    expect(doc.querySelectorAll('script, link, iframe, [onclick], [href]')).toHaveLength(0);
    expect(doc.querySelector('h1')?.textContent).toBe('季度报告');
    expect(doc.querySelector('style')?.textContent).toContain('color:red');
  });
});

describe('文件预览', () => {
  it('HTML 默认在不给任何权限的 iframe 里静态预览，点「运行脚本」才换成只允许脚本的隔离 iframe', async () => {
    // jsdom 没有 Blob 地址；本文件环境独立，补上即可（iframe 卸载在 afterEach 之后，不能用 stubGlobal）
    Object.assign(URL, { createObjectURL: () => 'blob:report', revokeObjectURL: () => undefined });
    routeFetch({ [`GET ${URL_DOCX}`]: new Response(REPORT) });
    renderWithApp(
      <FilePreview file={serverFile({ name: '报告.html', url: URL_DOCX })} onClose={() => undefined} />,
    );
    const frame = await screen.findByTitle('HTML 文档预览');
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame.getAttribute('srcdoc')).toContain('季度报告');
    expect(frame.getAttribute('srcdoc')).not.toContain('<script');
    await userEvent.click(within(screen.getByRole('dialog')).getByText('运行脚本'));
    const scripted = await screen.findByTitle('HTML 文档预览');
    expect(scripted).toHaveAttribute('sandbox', 'allow-scripts');
    expect(scripted).toHaveAttribute('src', 'blob:report');
    expect(scripted).not.toHaveAttribute('srcdoc');
  });

  it('Word 不转换：打开后取原件、在浏览器里直接渲染', async () => {
    const calls = routeFetch({ [`GET ${URL_DOCX}`]: new Response('word-bytes') });
    renderWithApp(
      <FilePreview file={serverFile({ name: '简报.docx', url: URL_DOCX })} onClose={() => undefined} />,
    );
    expect(await screen.findByText('Word 原件：word-bytes')).toBeInTheDocument();
    expect(calls.find('GET', `${URL_DOCX}/preview`)).toHaveLength(0);
  });

  it('来源能编辑的 Word 直接是编辑器；编辑不了（null）退回只读预览，打不开时只读预览上方写明原因', async () => {
    const localDocx = (edit: () => Promise<{ url: string } | null>) => ({
      name: '报告.docx',
      id: 'c1\n报告.docx',
      read: async () => new TextEncoder().encode('local-word').buffer,
      mutable: true,
      edit,
    });
    const { unmount } = renderWithApp(
      <FilePreview
        file={localDocx(async () => ({
          url: 'http://127.0.0.1:5123/docs/?path=%2Fw%2F%E6%8A%A5%E5%91%8A.docx',
        }))}
        onClose={() => undefined}
      />,
    );
    const frame = await screen.findByTitle('编辑 报告.docx');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('src', 'http://127.0.0.1:5123/docs/?path=%2Fw%2F%E6%8A%A5%E5%91%8A.docx');
    expect(screen.queryByText(/Word 原件/)).not.toBeInTheDocument();
    unmount();

    const second = renderWithApp(
      <FilePreview file={localDocx(async () => null)} onClose={() => undefined} />,
    );
    expect(await screen.findByText('Word 原件：local-word')).toBeInTheDocument();
    second.unmount();

    const third = renderWithApp(
      <FilePreview
        file={localDocx(async () => {
          throw new Error('编辑器没起来');
        })}
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText('Word 原件：local-word')).toBeInTheDocument();
    expect(screen.getByText('编辑器暂时打不开，先只读预览')).toBeInTheDocument();
    third.unmount();

    // 服务端说得出原因的（例如同时在用的人数到了上限、附件只读）照原话写
    renderWithApp(
      <FilePreview
        file={localDocx(async () => {
          throw new ApiError(409, '附件是只读的，不能直接改；可以让智能体另存一份再编辑');
        })}
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText('Word 原件：local-word')).toBeInTheDocument();
    expect(screen.getByText('附件是只读的，不能直接改；可以让智能体另存一份再编辑')).toBeInTheDocument();
  });

  it('编辑器还在起（第一次打开要现起一个）：等了一会儿说一句在等什么，打开后收掉', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let open: (value: { url: string }) => void = () => undefined;
      const starting = new Promise<{ url: string }>((resolve) => {
        open = resolve;
      });
      const hint = '正在启动编辑器，第一次打开要十几秒';
      renderWithApp(
        <FilePreview
          file={{
            name: '报告.docx',
            id: 'c1\n报告.docx',
            read: async () => new ArrayBuffer(0),
            mutable: true,
            edit: () => starting,
          }}
          onClose={() => undefined}
        />,
      );
      expect(screen.queryByText(hint)).not.toBeInTheDocument();
      await act(async () => {
        vi.advanceTimersByTime(EDITOR_STARTING_HINT_MS);
      });
      expect(screen.getByText(hint)).toBeInTheDocument();
      open({ url: 'http://127.0.0.1:5123/docs/?path=%2Fw%2F%E6%8A%A5%E5%91%8A.docx' });
      expect(await screen.findByTitle('编辑 报告.docx')).toBeInTheDocument();
      expect(screen.queryByText(hint)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('编辑器打不开、文件本身也不在了（例如已随执行环境回收）：原因只说一次，头部和正文都不给下载', async () => {
    const gone = '文件已随执行环境回收，交付过的文件仍可在交付卡片里打开';
    renderWithApp(
      <FilePreview
        file={{
          name: '周报.docx',
          id: 'c1\n周报.docx',
          read: async () => {
            throw new ApiError(410, gone);
          },
          mutable: true,
          edit: async () => {
            throw new ApiError(410, gone);
          },
          actions: <button type="button">下载</button>,
        }}
        onClose={() => undefined}
      />,
    );
    await screen.findByRole('alert');
    expect(screen.getAllByText(gone)).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '下载' })).not.toBeInTheDocument();
  });

  it('找不到文件（404）同样不给下载；只是预览没打开、原件还在时，头部和说明下面照旧给下载', async () => {
    const failing = (status: number, message: string) => ({
      name: '方案.docx',
      id: `c1\n方案-${status}.docx`,
      read: async (): Promise<ArrayBuffer> => {
        throw new ApiError(status, message);
      },
      mutable: true,
      actions: <button type="button">下载</button>,
    });
    const { unmount } = renderWithApp(
      <FilePreview file={failing(404, '找不到这个文件，可能已经移走或删掉了')} onClose={() => undefined} />,
    );
    await screen.findByRole('alert');
    expect(screen.queryByRole('button', { name: '下载' })).not.toBeInTheDocument();
    unmount();

    renderWithApp(<FilePreview file={failing(503, '文件暂时读不了：沙箱忙')} onClose={() => undefined} />);
    await screen.findByRole('alert');
    expect(screen.getAllByRole('button', { name: '下载' })).toHaveLength(2);
  });

  it('编辑器里存了盘：交给来源存回对话；存不回去时提示再存一次', async () => {
    const EDITOR = 'http://127.0.0.1:5123';
    const saved = vi.fn(async () => undefined);
    renderWithApp(
      <FilePreview
        file={{
          name: '报告.docx',
          id: 'c1\n报告.docx',
          read: async () => new TextEncoder().encode('cloud-word').buffer,
          mutable: true,
          edit: async () => ({ url: `${EDITOR}/docs/?path=%2Fwork%2Fc1%2Fa.docx` }),
          saved,
        }}
        onClose={() => undefined}
      />,
    );
    const frame = (await screen.findByTitle('编辑 报告.docx')) as HTMLIFrameElement;
    const fromEditor = (data: unknown, origin = EDITOR) =>
      window.dispatchEvent(new MessageEvent('message', { data, origin, source: frame.contentWindow }));
    fromEditor({ type: 'genoffice:saved', path: '/work/c1/a.docx' }, 'http://evil.example');
    expect(saved).not.toHaveBeenCalled();
    fromEditor({ type: 'genoffice:saved', path: '/work/c1/a.docx' });
    expect(saved).toHaveBeenCalledTimes(1);
    saved.mockRejectedValueOnce(new ApiError(413, '没能存成新的一版：本会话产物累计将超过 100 MiB 上限'));
    fromEditor({ type: 'genoffice:saved', path: '/work/c1/a.docx' });
    expect(
      await screen.findByText('没能存成新的一版：本会话产物累计将超过 100 MiB 上限'),
    ).toBeInTheDocument();
  });

  it('本机的 Excel、PowerPoint 也进编辑器；旧格式、知识库转出来的 Word、来源编辑不了的不进', () => {
    const local = (name: string, extra: Record<string, unknown> = {}) => ({
      name,
      id: `c1\n${name}`,
      read: async () => new ArrayBuffer(0),
      edit: async () => null,
      ...extra,
    });
    expect(opensInEditor(local('报告.docx'))).toBe(true);
    expect(opensInEditor(local('营收.xlsx'))).toBe(true);
    expect(opensInEditor(local('汇报.PPTX'))).toBe(true);
    expect(opensInEditor(local('旧表.xls'))).toBe(false);
    expect(opensInEditor(local('旧稿.pptx', { edit: undefined }))).toBe(false);
    expect(opensInEditor(local('入库.doc', { docx: async () => new ArrayBuffer(0) }))).toBe(false);
  });

  it('编辑器和对话联动：「AI 修改」交给对话并标上「AI 正在改」；AI 改了文件重新载入，又有没存的改动让用户选', async () => {
    const EDITOR = 'http://127.0.0.1:5123';
    const edit = vi.fn(async () => ({ url: `${EDITOR}/docs/?path=%2Fw%2Fa.docx` }));
    const aiEdit = vi.fn();
    const discardEdits = vi.fn(async () => undefined);
    let changed: ((change: { changed: boolean; unsaved: boolean }) => void) | undefined;
    const unsubscribe = vi.fn();
    renderWithApp(
      <FilePreview
        file={{
          name: '报告.docx',
          id: 'c1\n报告.docx',
          read: async () => new TextEncoder().encode('local-word').buffer,
          mutable: true,
          edit,
          aiEdit,
          discardEdits,
          editorChanges: (listener) => {
            changed = listener;
            return unsubscribe;
          },
        }}
        onClose={() => undefined}
      />,
    );
    const frame = (await screen.findByTitle('编辑 报告.docx')) as HTMLIFrameElement;
    const posted = vi.spyOn(frame.contentWindow as Window, 'postMessage');
    const fromEditor = (
      data: unknown,
      origin = EDITOR,
      source: MessageEventSource | null = frame.contentWindow,
    ) => window.dispatchEvent(new MessageEvent('message', { data, origin, source }));
    const request = {
      type: 'genoffice:ai-edit',
      instruction: '改简洁些',
      send: true,
      selection: { startBlockIndex: 2, endBlockIndex: 3, isRange: true, text: '原来的两段' },
    };

    // 别的来源、别的窗口冒充编辑器：不认
    fromEditor(request, 'http://evil.example');
    fromEditor(request, EDITOR, window);
    expect(aiEdit).not.toHaveBeenCalled();
    fromEditor(request);
    expect(aiEdit).toHaveBeenCalledWith({
      instruction: '改简洁些',
      send: true,
      selection: '原来的两段',
      blocks: { start: 2, end: 3 },
    });
    expect(posted).toHaveBeenLastCalledWith(
      { type: 'genoffice:ai-busy', ranges: [{ start: 2, end: 3, label: 'AI 正在改' }] },
      EDITOR,
    );
    // 编辑器重新起好：标记再发一次
    fromEditor({ type: 'genoffice:ready' });
    expect(posted).toHaveBeenCalledTimes(2);

    // AI 的改动真的落进了文档：马上收掉标记，不等这一轮结束；之后编辑器重新起好也不再补发
    fromEditor({ type: 'genoffice:agent-edited' }, 'http://evil.example');
    expect(posted).toHaveBeenCalledTimes(2);
    fromEditor({ type: 'genoffice:agent-edited' });
    expect(posted).toHaveBeenLastCalledWith({ type: 'genoffice:ai-busy', ranges: [] }, EDITOR);
    fromEditor({ type: 'genoffice:ready' });
    expect(posted).toHaveBeenCalledTimes(3);

    // 这一轮没改文件：收掉标记，不重新载入
    changed?.({ changed: false, unsaved: false });
    expect(posted).toHaveBeenLastCalledWith({ type: 'genoffice:ai-busy', ranges: [] }, EDITOR);
    expect(edit).toHaveBeenCalledTimes(1);

    // AI 改了、编辑器里没有没存的：重新载入（再要一次地址、换一个新页面）
    changed?.({ changed: true, unsaved: false });
    await vi.waitFor(() => expect(edit).toHaveBeenCalledTimes(2));
    expect(await screen.findByTitle('编辑 报告.docx')).not.toBe(frame);

    // 改了、编辑器里又有没存的：说清楚，让用户选
    changed?.({ changed: true, unsaved: true });
    expect(await screen.findByText('AI 改了这个文件，这里还有没保存的改动。')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保留我的' }));
    expect(screen.queryByText('AI 改了这个文件，这里还有没保存的改动。')).not.toBeInTheDocument();
    expect(discardEdits).not.toHaveBeenCalled();
    changed?.({ changed: true, unsaved: true });
    await userEvent.click(await screen.findByRole('button', { name: '载入 AI 改过的版本' }));
    expect(discardEdits).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(edit).toHaveBeenCalledTimes(3));
    expect(screen.queryByText('AI 改了这个文件，这里还有没保存的改动。')).not.toBeInTheDocument();

    // 编辑服务断了（重启过、被结束）：重新要地址、换一个新页面，用户看不到断线
    const current = (await screen.findByTitle('编辑 报告.docx')) as HTMLIFrameElement;
    fromEditor({ type: 'genoffice:disconnected' }, EDITOR, current.contentWindow);
    await vi.waitFor(() => expect(edit).toHaveBeenCalledTimes(4));
  });

  it('给了转好的 docx（知识库把 .doc 转成了 docx）就按 Word 渲染它，下载仍是原件', async () => {
    const calls = routeFetch({ 'GET /kb/d1/preview': new Response('converted-docx') });
    renderWithApp(
      <FilePreview
        file={serverFile({
          name: '旧合同.doc',
          url: '/kb/d1/original',
          pdfUrl: '/kb/d1/preview',
          docxUrl: '/kb/d1/preview',
        })}
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText('Word 原件：converted-docx')).toBeInTheDocument();
    expect(calls.find('GET', '/kb/d1/original')).toHaveLength(0);
    expect(within(screen.getByRole('dialog')).getByRole('link', { name: /下载/ })).toHaveAttribute(
      'href',
      '/kb/d1/original',
    );
  });

  it('PPT 卡片有预览按钮，打开后取服务端转换的 PDF', async () => {
    const calls = routeFetch({ [`GET ${URL_DOCX}/preview`]: new Response('%PDF-fake') });
    const onPreview = vi.fn();
    renderWithApp(<FileCard name="简报.pptx" onPreview={onPreview} />);
    await userEvent.click(screen.getByRole('button', { name: '预览 简报.pptx' }));
    expect(onPreview).toHaveBeenCalledOnce();
    renderWithApp(
      <FilePreview file={serverFile({ name: '简报.pptx', url: URL_DOCX })} onClose={() => undefined} />,
    );
    expect(await screen.findByText('PDF 阅读器：%PDF-fake')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByRole('link', { name: /下载/ })).toHaveAttribute(
      'href',
      URL_DOCX,
    );
    expect(calls.find('GET', `${URL_DOCX}/preview`)).toHaveLength(1);
  });

  it('转换失败时显示服务端给的原因', async () => {
    routeFetch({
      [`GET ${URL_DOCX}/preview`]: jsonResponse({ detail: '预览暂时不可用：文档转换超时' }, 503),
    });
    renderWithApp(
      <FilePreview file={serverFile({ name: '简报.pptx', url: URL_DOCX })} onClose={() => undefined} />,
    );
    expect(await screen.findByText('预览暂时不可用：文档转换超时')).toBeInTheDocument();
  });

  it('表格在浏览器里渲染；不支持的类型只给下载', async () => {
    const book = utils.book_new();
    utils.book_append_sheet(
      book,
      utils.aoa_to_sheet([
        ['区域', '销售额'],
        ['华南', 860],
      ]),
      '汇总',
    );
    const bytes = write(book, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer;
    routeFetch({ [`GET ${URL_DOCX}`]: new Response(bytes) });
    renderWithApp(
      <FilePreview file={serverFile({ name: '表.xlsx', url: URL_DOCX })} onClose={() => undefined} />,
    );
    expect(await screen.findByText('华南')).toBeInTheDocument();
    expect(screen.getByText('860')).toBeInTheDocument();
  });

  it('不支持预览的类型不显示预览按钮，打开后说明不能预览、给下载', () => {
    renderWithApp(<FileCard name="包.zip" onPreview={() => undefined} />);
    expect(screen.queryByRole('button', { name: /预览/ })).not.toBeInTheDocument();
    renderWithApp(
      <FilePreview file={serverFile({ name: '包.zip', url: URL_DOCX })} onClose={() => undefined} />,
    );
    expect(screen.getByText('这种文件不能在这里预览')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /下载/ }).length).toBeGreaterThan(0);
  });
});
