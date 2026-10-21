/**
 * 浮在预览底部正中的缩放控件：缩小、当前比例（点开选「适应宽度」或固定档）、放大。
 * 行为照 DSH 官方预览（client/zoom/ZoomControls.js，MIT）；样式照 ChatGPT 的浮层：白底胶囊、细描边投影、文字按钮。
 */
import { useEffect, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Minus, Plus } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { MAX_ZOOM, MIN_ZOOM, percentOf, steppedZoom, ZOOM_OPTIONS } from './zoom';

const FIT_KEY = 'fit-width';

export function ZoomControls({
  zoom,
  fitWidth,
  visible,
  onZoom,
  onFitWidth,
  onActiveChange,
}: {
  readonly zoom: number;
  readonly fitWidth: boolean;
  readonly visible: boolean;
  readonly onZoom: (zoom: number) => void;
  readonly onFitWidth: () => void;
  /** 鼠标在控件上、菜单展开或键盘焦点在里面时为真，这期间控件不收起。 */
  readonly onActiveChange: (active: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    onActiveChange(open || hovered || focused);
  }, [open, hovered, focused, onActiveChange]);
  const selected = fitWidth ? FIT_KEY : String(zoom);
  const pick = (key: string) => (key === FIT_KEY ? onFitWidth() : onZoom(Number(key)));
  return (
    <div
      className={visible ? 'kw-zoom-controls kw-zoom-controls-visible' : 'kw-zoom-controls'}
      role="toolbar"
      aria-label="缩放"
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
      }}
    >
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label="缩小"
        disabled={zoom <= MIN_ZOOM}
        onClick={() => onZoom(steppedZoom(zoom, -1))}
      >
        <Minus />
      </Button>
      <Menu onOpen={() => setOpen(true)} onClose={() => setOpen(false)}>
        <Menu.Trigger>
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            className="kw-zoom-percent"
            aria-label="缩放比例"
          >
            {percentOf(zoom)}
          </Button>
        </Menu.Trigger>
        <Menu.Content side="top" align="center" minWidth={140}>
          <Menu.RadioGroup value={selected} onChange={pick}>
            <Menu.RadioItem value={FIT_KEY}>适应宽度</Menu.RadioItem>
            {ZOOM_OPTIONS.map((value) => (
              <Menu.RadioItem key={value} value={String(value)}>
                {percentOf(value)}
              </Menu.RadioItem>
            ))}
          </Menu.RadioGroup>
        </Menu.Content>
      </Menu>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label="放大"
        disabled={zoom >= MAX_ZOOM}
        onClick={() => onZoom(steppedZoom(zoom, 1))}
      >
        <Plus />
      </Button>
    </div>
  );
}
