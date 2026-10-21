/** 只显示这一次的密钥、链接：等宽字、灰底框，右侧一个复制按钮。 */
import { CopyButton } from '@openai/apps-sdk-ui/components/Button';

export function SecretText({ value }: { readonly value: string }) {
  return (
    <div className="kw-secret-once">
      <code>{value}</code>
      <CopyButton copyValue={value} color="secondary" variant="outline" size="sm">
        {({ copied }) => (copied ? '已复制' : '复制')}
      </CopyButton>
    </div>
  );
}
