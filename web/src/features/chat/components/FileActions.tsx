/**
 * 对话里一个文件的顶部操作（预览抽屉右上角），按来源给了哪些动作显示（方案 7.7.3，网页与桌面同一个组件）：
 * - 文件就在这台电脑上（能用本机程序打开）：照 DSH 官方文档预览的分体按钮，主按钮「打开」用默认应用打开，
 *   旁边箭头里「显示文件位置」，能存的再加「另存为…」；
 * - 不能在本机打开、能存：一个「下载」按钮。
 * 官方：电脑上打开成功不提示，失败弹提示。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, Download } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';

export type How = 'app' | 'reveal';

interface Props {
  readonly open?: (how: How) => Promise<void>;
  readonly save?: () => Promise<void>;
}

export function FileActions({ open, save }: Props) {
  const [busy, setBusy] = useState(false);
  const run = (action: () => Promise<void>, fallback: string) => {
    setBusy(true);
    action()
      .catch((error: unknown) => toast.error(readableError(error, fallback)))
      .finally(() => setBusy(false));
  };
  const saveIt = save && (() => run(save, '没能保存这个文件'));
  if (!open) {
    return saveIt ? (
      <Button color="secondary" variant="outline" size="sm" disabled={busy} onClick={saveIt}>
        <Download />
        下载
      </Button>
    ) : null;
  }
  const openWith = (how: How) => run(() => open(how), '没能打开这个文件');
  return (
    <span className="kw-split-button">
      <Button color="secondary" variant="outline" size="sm" disabled={busy} onClick={() => openWith('app')}>
        打开
      </Button>
      <Menu>
        <Menu.Trigger>
          <Button
            color="secondary"
            variant="outline"
            size="sm"
            uniform
            disabled={busy}
            aria-label="更多打开方式"
          >
            <ChevronDown />
          </Button>
        </Menu.Trigger>
        <Menu.Content align="end" minWidth={140}>
          <Menu.Item onSelect={() => openWith('reveal')}>显示文件位置</Menu.Item>
          {saveIt ? <Menu.Item onSelect={saveIt}>另存为…</Menu.Item> : null}
        </Menu.Content>
      </Menu>
    </span>
  );
}
