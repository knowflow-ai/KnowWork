import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { validateBrandName, validateLogo } from '../../../api/system';
import SystemPage from './SystemPage';

afterEach(() => vi.unstubAllGlobals());

const LOGO = '/api/brand/logo?v=1700000000000';

function api(allowed = true, extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/system/check': jsonResponse({ allowed }),
    'GET /api/brand': jsonResponse({ name: 'KnowWork', logo_url: null }),
    'GET /api/system/info': jsonResponse({ knowwork: '0.1.0', dsh: '0.2.1-rc.2', database: null }),
    'PUT /api/system/brand': (call) =>
      jsonResponse({ name: (call.body as { name: string | null }).name ?? 'KnowWork', logo_url: null }),
    'PUT /api/system/brand/logo': jsonResponse({ name: 'KnowWork', logo_url: LOGO }),
    'DELETE /api/system/brand/logo': jsonResponse({ name: 'KnowWork', logo_url: null }),
    ...extra,
  });
}

describe('系统设置', () => {
  it('系统信息：版本号，取不到的写未知', async () => {
    api();
    renderWithApp(<SystemPage />);
    const info = await screen.findByRole('region', { name: '系统信息' });
    expect(await within(info).findByText('0.1.0')).toBeInTheDocument();
    expect(within(info).getByText('0.2.1-rc.2')).toBeInTheDocument();
    expect(within(info).getByText('未知')).toBeInTheDocument();
  });

  it('改名称：保存后预览跟着变；留空就是恢复默认', async () => {
    const calls = api();
    renderWithApp(<SystemPage />);
    const brand = await screen.findByRole('region', { name: '品牌' });
    // 默认品牌：叶子 K 当首字母的 KnowWork 字样
    expect(within(brand).getByRole('img', { name: 'KnowWork' })).toBeInTheDocument();
    const input = within(brand).getByLabelText('名称');
    await userEvent.type(input, '星河智能');
    await userEvent.click(within(brand).getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(calls.find('PUT', '/api/system/brand')[0]?.body).toEqual({ name: '星河智能' }),
    );
    // 改过名称后显示图标加名称
    expect(await within(brand).findByText('星河智能')).toBeInTheDocument();
    expect(await screen.findByText('名称已保存')).toBeInTheDocument();
  });

  it('上传 Logo 先在前端核对类型与大小，换上后可以恢复默认', async () => {
    const calls = api();
    renderWithApp(<SystemPage />);
    const brand = await screen.findByRole('region', { name: '品牌' });
    const picker = within(brand).getByLabelText('选择 Logo 图片');
    fireEvent.change(picker, { target: { files: [new File(['GIF89a'], 'a.gif', { type: 'image/gif' })] } });
    expect(await screen.findByText('Logo 只支持 PNG、JPEG、WebP 或 SVG')).toBeInTheDocument();
    expect(calls.find('PUT', '/api/system/brand/logo')).toHaveLength(0);

    fireEvent.change(picker, { target: { files: [new File(['png'], 'logo.png', { type: 'image/png' })] } });
    await waitFor(() => expect(calls.find('PUT', '/api/system/brand/logo')).toHaveLength(1));
    expect(await within(brand).findByRole('img', { name: '当前 Logo' })).toHaveAttribute('src', LOGO);
    await userEvent.click(within(brand).getByRole('button', { name: '恢复默认' }));
    await waitFor(() => expect(calls.find('DELETE', '/api/system/brand/logo')).toHaveLength(1));
    await waitFor(() =>
      expect(within(brand).queryByRole('button', { name: '恢复默认' })).not.toBeInTheDocument(),
    );
  });

  it('不是系统管理员时看到没有权限，不去取系统信息', async () => {
    const calls = api(false);
    renderWithApp(<SystemPage />);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
    expect(calls.find('GET', '/api/system/info')).toHaveLength(0);
  });

  it('名称与 Logo 的前端校验', () => {
    expect(validateBrandName('  ')).toBeNull();
    expect(validateBrandName('x'.repeat(41))).toMatch(/40/);
    expect(validateBrandName('<b>')).toMatch(/尖括号/);
    const big = new File(['x'], 'big.png', { type: 'image/png' });
    Object.defineProperty(big, 'size', { value: 600 * 1024 });
    expect(validateLogo(big)).toMatch(/512 KB/);
    expect(validateLogo(new File(['<svg/>'], 'a.svg', { type: 'image/svg+xml' }))).toBeNull();
  });
});
