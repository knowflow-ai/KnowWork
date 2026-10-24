import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import { utils, write } from 'xlsx';
import userEvent from '../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../test/render';
import { FileCard } from '../chat/components/FileCard';
import { FilePreview } from './FilePreview';
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

const URL_DOCX = '/api/conversations/c1/artifacts/art_1';

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
    renderWithApp(<FilePreview file={{ name: '报告.html', url: URL_DOCX }} onClose={() => undefined} />);
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
    renderWithApp(<FilePreview file={{ name: '简报.docx', url: URL_DOCX }} onClose={() => undefined} />);
    expect(await screen.findByText('Word 原件：word-bytes')).toBeInTheDocument();
    expect(calls.find('GET', `${URL_DOCX}/preview`)).toHaveLength(0);
  });

  it('给了 docxUrl（知识库把 .doc 转成了 docx）就按 Word 渲染它，下载仍是原件', async () => {
    const calls = routeFetch({ 'GET /kb/d1/preview': new Response('converted-docx') });
    renderWithApp(
      <FilePreview
        file={{
          name: '旧合同.doc',
          url: '/kb/d1/original',
          pdfUrl: '/kb/d1/preview',
          docxUrl: '/kb/d1/preview',
        }}
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
    renderWithApp(<FileCard name="简报.pptx" size={1024} url={URL_DOCX} onPreview={onPreview} />);
    await userEvent.click(screen.getByRole('button', { name: '预览 简报.pptx' }));
    expect(onPreview).toHaveBeenCalledOnce();
    renderWithApp(<FilePreview file={{ name: '简报.pptx', url: URL_DOCX }} onClose={() => undefined} />);
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
    renderWithApp(<FilePreview file={{ name: '简报.pptx', url: URL_DOCX }} onClose={() => undefined} />);
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
    renderWithApp(<FilePreview file={{ name: '表.xlsx', url: URL_DOCX }} onClose={() => undefined} />);
    expect(await screen.findByText('华南')).toBeInTheDocument();
    expect(screen.getByText('860')).toBeInTheDocument();
  });

  it('不支持预览的类型不显示预览按钮，打开也只提示下载', () => {
    renderWithApp(<FileCard name="包.zip" url={URL_DOCX} onPreview={() => undefined} />);
    expect(screen.queryByRole('button', { name: /预览/ })).not.toBeInTheDocument();
    renderWithApp(<FilePreview file={{ name: '包.zip', url: URL_DOCX }} onClose={() => undefined} />);
    expect(screen.getByText('这种文件不支持预览，请下载后查看')).toBeInTheDocument();
  });
});
