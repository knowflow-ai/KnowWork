/**
 * 回答的来源，照 ChatGPT：回答下方那排图标末尾一个「N 个来源」按钮，点开右侧面板逐条列出这次引用的文件
 * （同一份文档的几处引用合成一条，写明编号、页码和首次引用那处的摘录）。点一条在预览抽屉里打开原文，
 * 定位到首次引用的那处；知识源没有预览器时跳原文链接。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileDocument } from '@openai/apps-sdk-ui/components/Icon';
import { Sheet } from '../../../ui/Sheet';
import { FilePreview } from '../../preview/FilePreview';
import type { CitedFile } from './citations';
import { previewerOf, useSourcePreview } from './useSourcePreview';

function Summary({ file }: { readonly file: CitedFile }) {
  const pages = file.pages.length > 0 ? ` · 第 ${file.pages.join('、')} 页` : '';
  return (
    <>
      <span className="kw-source-title">
        <FileDocument className="kw-source-icon" aria-hidden />
        {file.title}
      </span>
      <span className="kw-source-meta">
        {file.numbers.map((n) => `[${n}]`).join(' ')}
        {pages}
      </span>
      {file.first.excerpt ? <span className="kw-source-excerpt">{file.first.excerpt}</span> : null}
    </>
  );
}

export function AnswerSources({ files }: { readonly files: readonly CitedFile[] }) {
  const [listing, setListing] = useState(false);
  const { opened, opening, open, close } = useSourcePreview();
  if (files.length === 0) return null;
  const openFile = async (file: CitedFile) => {
    if (await open(file.first)) setListing(false);
  };
  return (
    <>
      <Button color="secondary" variant="ghost" size="sm" onClick={() => setListing(true)}>
        {files.length} 个来源
      </Button>
      {listing ? (
        <Sheet title="来源" width={420} onClose={() => setListing(false)}>
          <ul className="kw-source-list" aria-label="引用的文件">
            {files.map((file) => (
              <li key={file.key}>
                {previewerOf(file.first) ? (
                  <button
                    type="button"
                    className="kw-source-item"
                    disabled={opening}
                    aria-label={`查看原文 ${file.title}`}
                    onClick={() => void openFile(file)}
                  >
                    <Summary file={file} />
                  </button>
                ) : file.first.url ? (
                  <a
                    className="kw-source-item"
                    href={file.first.url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`查看原文 ${file.title}`}
                  >
                    <Summary file={file} />
                  </a>
                ) : (
                  <div className="kw-source-item">
                    <Summary file={file} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Sheet>
      ) : null}
      {opened ? <FilePreview file={opened.file} locate={opened.locate} onClose={close} /> : null}
    </>
  );
}
