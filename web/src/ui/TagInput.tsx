/**
 * 多个值的输入（标签集、可选值、ID 列表）：组件库的 TagInput 加两处修正，界面里一律用这个。
 * - 中文输入法组字时不收标签：组件库只看按的是哪个键，拼音按空格选字、按回车上屏英文时，会把还没上屏的拼音
 *   先收成一个标签（打「tian」按空格，出来「tian」和「天」两个）。外层在捕获阶段拦下组字中的按键，
 *   组件库的处理函数收不到，输入法照常工作；
 * - 分隔符是回车、英文逗号和中文逗号，不用空格：值里可能带空格（「Q3 报告」），中文输入时也常打全角逗号。
 */
import type { KeyboardEvent } from 'react';
import { TagInput as BaseTagInput, type TagInputProps } from '@openai/apps-sdk-ui/components/TagInput';

export type { Tag } from '@openai/apps-sdk-ui/components/TagInput';

export const TAG_DELIMITERS: readonly string[] = [',', '，'];

/** 输入法还在组字。Safari 结束组字的那次回车 isComposing 已是 false，但 keyCode 仍是 229。 */
function composing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}

export function TagInput({ delimiters, ...props }: TagInputProps) {
  return (
    <div
      className="kw-tag-input"
      onKeyDownCapture={(event) => {
        if (composing(event)) event.stopPropagation();
      }}
    >
      {/* 显式传 undefined 时组件库会退回它带空格的默认值，这里兜住 */}
      <BaseTagInput {...props} delimiters={delimiters ?? [...TAG_DELIMITERS]} />
    </div>
  );
}
