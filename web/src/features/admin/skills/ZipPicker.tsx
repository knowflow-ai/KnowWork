/** 选一个 ZIP 技能包：隐藏的文件框，由按钮或菜单项触发。 */
import { forwardRef, useImperativeHandle, useRef } from 'react';

export interface ZipPickerHandle {
  readonly open: () => void;
}

export const ZipPicker = forwardRef<ZipPickerHandle, { readonly onPick: (file: File) => void }>(
  function ZipPicker({ onPick }, ref) {
    const input = useRef<HTMLInputElement>(null);
    useImperativeHandle(ref, () => ({ open: () => input.current?.click() }), []);
    return (
      <input
        ref={input}
        type="file"
        accept=".zip,application/zip"
        hidden
        aria-label="选择技能包"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />
    );
  },
);
