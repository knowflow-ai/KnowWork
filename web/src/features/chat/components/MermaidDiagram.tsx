/** 回答里的 mermaid 图：画好之前先显示原文；画不出来显示原文并说明。SVG 由库在严格安全模式下生成（见 mermaid.ts）。 */
import { useEffect, useState } from 'react';
import { renderMermaid } from './mermaid';

interface Drawn {
  readonly source: string;
  readonly svg?: string;
  readonly failed?: boolean;
}

export function MermaidDiagram({ source }: { readonly source: string }) {
  const [drawn, setDrawn] = useState<Drawn>();
  useEffect(() => {
    let alive = true;
    renderMermaid(source).then(
      (svg) => {
        if (alive) setDrawn({ source, svg });
      },
      () => {
        if (alive) setDrawn({ source, failed: true });
      },
    );
    return () => {
      alive = false;
    };
  }, [source]);
  // 原文变了（还在输出时补了一行）：旧图作废，等新的画好
  const current = drawn?.source === source ? drawn : undefined;
  if (current?.svg) {
    return (
      <div
        className="kw-mermaid"
        role="img"
        aria-label="图"
        dangerouslySetInnerHTML={{ __html: current.svg }}
      />
    );
  }
  return (
    <div className="kw-mermaid-raw">
      {current?.failed ? <p className="kw-mermaid-note">没能画成图，下面是原文</p> : null}
      <pre>
        <code className="language-mermaid">{source}</code>
      </pre>
    </div>
  );
}
