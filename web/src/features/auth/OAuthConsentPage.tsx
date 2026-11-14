/**
 * 应用登录确认页（/oauth/authorize）：桌面端等应用打开系统浏览器到这里，用户确认后带着一次性授权码回到应用
 * （OAuth 授权码 + PKCE，后端见 identity/oauth.py）。要已登录（RequireAuth 包着；单点登录回来会回到这一页）。
 *
 * 一定要用户点「允许」才发码：本机上别的程序也能打开这个地址，不能借着浏览器里现成的登录态悄悄拿走令牌。
 * 请求有问题但回调地址可信时，把错误带回应用；客户端或回调地址本身不可信时只在页面上说明，不跳走。
 */
import { useEffect, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import { checkAuthorization, decideAuthorization } from '../../api/oauth';
import { readableError } from '../../api/http';
import { Skeleton } from '../../ui/Skeleton';
import { AuthCard } from './AuthCard';
import { navigation } from './inClient';
import { useMe } from './queries';

type Outcome = 'approved' | 'denied';

export default function OAuthConsentPage() {
  const { search } = useLocation();
  const me = useMe();
  const check = useQuery({
    queryKey: ['oauth', 'authorize', search],
    queryFn: () => checkAuthorization(search),
    staleTime: Infinity,
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const bounce = check.data?.kind === 'error' ? check.data.redirectTo : null;

  useEffect(() => {
    if (bounce) navigation.go(bounce);
  }, [bounce]);

  const decide = async (approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const target = await decideAuthorization(search, approve);
      setOutcome(approve ? 'approved' : 'denied');
      navigation.go(target);
    } catch (err) {
      setError(readableError(err, '没有成功，请稍后重试'));
    } finally {
      setBusy(false);
    }
  };

  if (check.isPending) return <Skeleton rows={4} className="kw-page-state" />;
  if (check.isError) {
    return (
      <AuthCard
        title="无法完成应用登录"
        description={readableError(check.error, '服务暂时不可用，请稍后重试')}
      >
        {null}
      </AuthCard>
    );
  }
  const result = check.data;
  if (result.kind === 'error') {
    return result.redirectTo ? (
      <AuthCard title="正在返回应用…" description="登录请求有问题，已把原因告诉应用。">
        {null}
      </AuthCard>
    ) : (
      <AuthCard title="无法完成应用登录" description={`这个登录请求无效：${result.message}`}>
        {null}
      </AuthCard>
    );
  }
  if (outcome) {
    return (
      <AuthCard
        title={outcome === 'approved' ? '已允许，请回到应用继续' : '已拒绝这次登录'}
        description="可以关掉这个页面了。"
      >
        {null}
      </AuthCard>
    );
  }
  const account = me.data ? `${me.data.name}（${me.data.email ?? me.data.id}）` : '';
  return (
    <AuthCard
      title={`允许「${result.clientName}」登录你的账号？`}
      description="允许后，这个应用能以你的身份使用，和在浏览器里一样。不是你本人刚刚发起的，请拒绝。"
      error={error}
    >
      <dl className="kw-consent-facts">
        {result.deviceName ? (
          <div>
            <dt>设备</dt>
            <dd>{result.deviceName}</dd>
          </div>
        ) : null}
        {account ? (
          <div>
            <dt>账号</dt>
            <dd>{account}</dd>
          </div>
        ) : null}
      </dl>
      <div className="kw-consent-actions">
        <Button
          color="secondary"
          variant="outline"
          size="lg"
          disabled={busy}
          onClick={() => void decide(false)}
        >
          拒绝
        </Button>
        <Button color="primary" size="lg" loading={busy} onClick={() => void decide(true)}>
          允许
        </Button>
      </div>
    </AuthCard>
  );
}
