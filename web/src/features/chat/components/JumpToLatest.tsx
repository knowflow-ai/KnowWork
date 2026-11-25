/**
 * 「回到最新」（照 ChatGPT、DeepSeek）：往上翻看历史时，浮在输入框上方正中的一个向下箭头，点了平滑滚到底，
 * 之后新内容来了照常跟着（hooks/useFollowLatest.ts）。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ArrowDown } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';

export function JumpToLatest({ onClick }: { readonly onClick: () => void }) {
  return (
    <div className="kw-jump-latest">
      <Tooltip content="回到最新" compact>
        <Button color="secondary" variant="outline" size="sm" uniform aria-label="回到最新" onClick={onClick}>
          <ArrowDown />
        </Button>
      </Tooltip>
    </div>
  );
}
