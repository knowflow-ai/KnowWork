/** mermaid 库第一次没加载上（网络抖了一下）：这张图报失败，下一张图重新加载，不是整页的图都画不出来。 */
import { expect, it, vi } from 'vitest';
import { renderMermaid } from './mermaid';

const loads = vi.hoisted(() => ({ count: 0 }));
vi.mock('mermaid', () => {
  loads.count += 1;
  if (loads.count === 1) throw new Error('Failed to fetch dynamically imported module');
  return {
    default: {
      initialize: vi.fn(),
      parse: vi.fn(async () => true),
      render: vi.fn(async () => ({ svg: '<svg></svg>' })),
    },
  };
});

it('第一次没加载上，下一张图重新加载', async () => {
  await expect(renderMermaid('graph LR\n  A --> B')).rejects.toThrow();
  await expect(renderMermaid('graph LR\n  A --> B')).resolves.toBe('<svg></svg>');
  expect(loads.count).toBe(2);
});
