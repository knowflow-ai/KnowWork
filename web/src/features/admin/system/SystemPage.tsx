/**
 * 系统设置：整套部署共用的品牌（名称、Logo）与系统信息。只有系统管理员（初始化时建的账号）能进，
 * 服务端同样逐个接口判定。改完品牌，侧栏、登录页、浏览器标签页都跟着变。
 */
import { useRef, useState, type FormEvent } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileUpload, Lock } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { readableError } from '../../../api/http';
import {
  DEFAULT_BRAND,
  DEFAULT_MARK,
  getSystemInfo,
  MAX_BRAND_NAME,
  renameBrand,
  resetLogo,
  uploadLogo,
  validateBrandName,
  validateLogo,
  type Brand,
} from '../../../api/system';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Empty } from '../../../ui/Empty';
import { Field } from '../../../ui/Field';
import { Skeleton } from '../../../ui/Skeleton';
import { toast } from '../../../ui/toastStore';
import { BrandMark } from '../../brand/BrandMark';
import { BRAND_KEY, useBrand, useIsSystemAdmin } from '../../brand/queries';
import { AdminPage } from '../AdminPage';

const UNKNOWN = '未知';

function useBrandChange<Input>(fn: (input: Input) => Promise<Brand>, success: string, failure: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (brand) => {
      client.setQueryData(BRAND_KEY, brand);
      toast.success(success);
    },
    onError: (error) => toast.error(readableError(error, failure)),
  });
}

function NameForm() {
  const brand = useBrand();
  const custom = brand.name === DEFAULT_BRAND.name ? '' : brand.name;
  const [draft, setDraft] = useState<string>();
  const value = draft ?? custom;
  const [problem, setProblem] = useState<string | null>(null);
  const rename = useBrandChange(renameBrand, '名称已保存', '名称没有保存成功，请稍后重试');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const invalid = validateBrandName(value);
    setProblem(invalid);
    if (!invalid) rename.mutate(value, { onSuccess: () => setDraft(undefined) });
  };
  return (
    <form className="kw-system-row" onSubmit={submit} noValidate>
      <Field
        label="名称"
        help="显示在浏览器标签、侧栏和登录页；留空用 KnowWork"
        error={problem ?? undefined}
        className="kw-system-grow"
      >
        {(id) => (
          <Input
            id={id}
            placeholder={DEFAULT_BRAND.name}
            maxLength={MAX_BRAND_NAME}
            invalid={Boolean(problem)}
            value={value}
            onChange={(event) => setDraft(event.target.value)}
          />
        )}
      </Field>
      <Button
        type="submit"
        color="secondary"
        variant="outline"
        loading={rename.isPending}
        disabled={value === custom}
      >
        保存
      </Button>
    </form>
  );
}

function LogoForm() {
  const brand = useBrand();
  const picker = useRef<HTMLInputElement>(null);
  const upload = useBrandChange(uploadLogo, 'Logo 已更新', 'Logo 没有上传成功，请稍后重试');
  const reset = useBrandChange(resetLogo, '已恢复默认 Logo', '没有恢复成功，请稍后重试');
  const pick = (file: File | undefined) => {
    if (!file) return;
    const problem = validateLogo(file);
    if (problem) toast.warning(problem);
    else upload.mutate(file);
  };
  return (
    <Field label="Logo" help="建议用方形图标，名称会显示在图标旁边；PNG、JPEG、WebP 或 SVG，不超过 512 KB">
      <div className="kw-system-logo">
        <img className="kw-system-logo-preview" src={brand.logoUrl ?? DEFAULT_MARK} alt="当前 Logo" />
        <Button
          color="secondary"
          variant="outline"
          loading={upload.isPending}
          onClick={() => picker.current?.click()}
        >
          <FileUpload />
          上传 Logo
        </Button>
        {brand.logoUrl ? (
          <Button
            color="secondary"
            variant="ghost"
            loading={reset.isPending}
            onClick={() => reset.mutate(undefined)}
          >
            恢复默认
          </Button>
        ) : null}
        <input
          ref={picker}
          type="file"
          hidden
          accept="image/png,image/jpeg,image/webp,image/svg+xml"
          aria-label="选择 Logo 图片"
          onChange={(event) => {
            pick(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
      </div>
    </Field>
  );
}

function SystemInfoList() {
  const info = useQuery({ queryKey: ['system', 'info'], queryFn: getSystemInfo });
  if (info.isPending) return <Skeleton rows={3} />;
  if (info.isError)
    return <p className="kw-secondary-text">{readableError(info.error, '系统信息没有加载出来')}</p>;
  return (
    <DescriptionList
      items={[
        { label: 'KnowWork 版本', value: info.data.knowwork },
        { label: 'DSH 版本', value: info.data.dsh ?? UNKNOWN },
        { label: '数据库版本', value: info.data.database ?? UNKNOWN },
        ...(info.data.license ? [{ label: '授权', value: info.data.license }] : []),
        ...info.data.extensions.map((item) => ({
          label: `扩展模块 ${item.name}`,
          value: item.enabled ? '已启用' : `未启用：${item.reason ?? '原因未知'}`,
        })),
      ]}
    />
  );
}

export default function SystemPage() {
  const systemAdmin = useIsSystemAdmin();
  if (systemAdmin.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (!systemAdmin.data) {
    return (
      <Empty
        area
        icon={<Lock />}
        title="没有权限"
        description="只有系统管理员（初始化时创建的账号）能修改系统设置"
      />
    );
  }
  return (
    <AdminPage title="系统设置" description="整套部署共用的设置，只有系统管理员能修改。">
      <section className="kw-system-section" aria-label="品牌">
        <h2>品牌</h2>
        <div className="kw-system-preview" aria-label="效果预览">
          <BrandMark />
        </div>
        <NameForm />
        <LogoForm />
      </section>
      <section className="kw-system-section" aria-label="系统信息">
        <h2>系统信息</h2>
        <SystemInfoList />
      </section>
    </AdminPage>
  );
}
