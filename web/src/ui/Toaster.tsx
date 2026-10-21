/**
 * 轻提示的显示：深色胶囊白字，出错时前面加一个警示图标，不整条变红。组件库没有，照 ChatGPT 的提示条。
 * 挂到 body 上：对话框打开时 Radix 会把应用根节点标成 aria-hidden，提示条放在里面读屏就读不到了。
 */
import { createPortal } from 'react-dom';
import { CheckCircle, Warning } from '@openai/apps-sdk-ui/components/Icon';
import { useToasts } from './toastStore';

export function Toaster() {
  const items = useToasts((state) => state.items);
  if (items.length === 0) return null;
  return createPortal(
    <div className="kw-toaster">
      {items.map((item) => (
        <div
          key={item.id}
          className="kw-toast"
          role={item.kind === 'error' || item.kind === 'warning' ? 'alert' : 'status'}
        >
          {item.kind === 'error' || item.kind === 'warning' ? (
            <Warning className="kw-toast-icon is-alert" aria-hidden="true" />
          ) : item.kind === 'success' ? (
            <CheckCircle className="kw-toast-icon" aria-hidden="true" />
          ) : null}
          <span>{item.text}</span>
        </div>
      ))}
    </div>,
    document.body,
  );
}
