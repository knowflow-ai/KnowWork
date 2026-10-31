/** 预览图片的地址：开发时 StrictMode 会把副作用跑两遍（中间模拟一次卸载），最后用的地址不能是已经释放掉的。 */
import { StrictMode } from 'react';
import { afterEach, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { BlobImage } from './BlobImage';

const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
afterEach(() => Object.assign(URL, { createObjectURL: original.create, revokeObjectURL: original.revoke }));

it('挂上时数据已经在（预览抽屉打开时缩略图已经取过）：StrictMode 下最后用的地址没被释放', async () => {
  let made = 0;
  const revoked = new Set<string>();
  Object.assign(URL, {
    createObjectURL: () => `blob:${(made += 1)}`,
    revokeObjectURL: (url: string) => revoked.add(url),
  });
  const { unmount } = render(
    <StrictMode>
      <BlobImage data={new ArrayBuffer(4)} alt="图" />
    </StrictMode>,
  );
  await waitFor(() => expect(screen.getByRole('img')).toBeInTheDocument());
  const src = screen.getByRole('img').getAttribute('src') ?? '';
  expect(revoked.has(src)).toBe(false);
  unmount();
  expect(revoked.has(src)).toBe(true);
});
