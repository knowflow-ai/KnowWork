/**
 * 插件详情抽屉，照 DSH 官方插件页的详情（dsh-client-ui-plugin-manager，0.2.2-rc.5）：标题下面是包名，先是作者写的
 * 说明文档，然后是组件清单，最后是来源、版本这些。插件提供了什么，靠作者写的说明和组件清单讲清楚。
 * 说明文档里的图片不显示：官方从宿主读包里的文件，我们只存了文字（也不去外网取图）。
 */
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { readableError } from '../../../api/http';
import { fetchUploadDetail, type PluginComponent } from '../../../api/plugins';
import { withoutImages, type PluginDetailView } from './detailView';
import { currentWorkspaceId } from '../../../stores/workspace';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { Markdown } from '../../chat/components/Markdown';
import { uploadDetailKey } from './queries';

function Document({ text }: { readonly text: string }) {
  return text.trim() ? (
    <div className="kw-plugin-detail-doc">
      <Markdown text={withoutImages(text)} />
    </div>
  ) : null;
}

function UploadDocument({ id, admin }: { readonly id: string; readonly admin: boolean }) {
  const detail = useQuery({
    queryKey: uploadDetailKey(currentWorkspaceId(), id, admin),
    queryFn: () => fetchUploadDetail(id, admin),
  });
  if (detail.isPending) return <Skeleton rows={3} />;
  if (detail.isError)
    return (
      <p className="kw-secondary-text">
        {readableError(detail.error, '说明文档没有加载出来')}
        <Button color="secondary" variant="ghost" size="sm" onClick={() => void detail.refetch()}>
          重试
        </Button>
      </p>
    );
  return <Document text={detail.data} />;
}

function Components({ items }: { readonly items: readonly PluginComponent[] }) {
  return (
    <ul className="kw-plugin-components">
      {items.map((item) => (
        <li key={item.module}>
          <span className="kw-plugin-component-title">{item.title}</span>
          {item.description ? <span className="kw-secondary-text">{item.description}</span> : null}
          {item.title !== item.module ? (
            <code className="kw-plugin-component-module">{item.module}</code>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function PluginDetail({
  view,
  onClose,
}: {
  readonly view: PluginDetailView;
  readonly onClose: () => void;
}) {
  const document: ReactNode =
    'text' in view.detail ? (
      <Document text={view.detail.text} />
    ) : (
      <UploadDocument id={view.detail.uploadId} admin={view.detail.admin} />
    );
  return (
    <Sheet title={view.title} onClose={onClose}>
      <div className="kw-detail-summary">
        {view.subtitle ? <p className="kw-plugin-detail-sub">{view.subtitle}</p> : null}
        {view.description ? <p className="kw-plugin-detail-desc">{view.description}</p> : null}
      </div>
      {document}
      {view.components.length > 0 ? (
        <section className="kw-detail-section">
          <h3>组件</h3>
          <Components items={view.components} />
        </section>
      ) : null}
      <section className="kw-detail-section">
        <h3>信息</h3>
        <DescriptionList items={view.facts} />
      </section>
    </Sheet>
  );
}
