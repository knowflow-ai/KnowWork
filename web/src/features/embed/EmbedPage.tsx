/**
 * 网页嵌入页（/embed/:key，嵌在接入方网页的 iframe 里，由嵌入脚本 widget.ts 打开）与分享链接（/s/:key，整页打开）。
 * 服务端见 channels/web.py。
 *
 * 不登录：读公开配置 → 换会话令牌（只放内存，api/embedAuth.ts）→ 进渠道所在的空间 → 有上次的会话就接着，
 * 没有就等第一句话再开。换令牌时公开访问带访客 id（存在本浏览器里，不是凭据，只用来接着上一段对话；嵌在别的网站里时
 * 浏览器本来就按那个网站分开存），认证访问带接入方签的登录信息（loginToken.ts：问外层要，或从地址 # 后面取）。
 * 会话令牌两小时过期，提前十分钟续；认证访问续不上时再问外层要一次登录信息。
 * 嵌在别的网站里时把名字、按钮颜色告诉嵌入脚本，点关闭让它收起小窗。访客不能带附件、不看运行详情（embedMode.ts）。
 */
import { useEffect, useState } from 'react';
import { useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ComposeEditSquare, X } from '@openai/apps-sdk-ui/components/Icon';
import { listConversations } from '../../api/conversations';
import {
  fetchEmbedConfig,
  openEmbedSession,
  renewEmbedSession,
  type EmbedConfig,
  type EmbedSession,
} from '../../api/embed';
import { setEmbedToken } from '../../api/embedAuth';
import { ApiError, readableError } from '../../api/http';
import { useWorkspaceStore } from '../../stores/workspace';
import { Empty } from '../../ui/Empty';
import { Skeleton } from '../../ui/Skeleton';
import { EmbeddedChat } from '../chat/EmbeddedChat';
import { EmbedModeContext, VISITOR } from './embedMode';
import { loginToken, MissingLoginToken } from './loginToken';
import { EMBED_CLOSE, EMBED_READY } from './protocol';
import { visitorId } from './visitor';

const RENEW_EARLY_SECONDS = 600;
const GONE = '这个入口已经关闭或不存在';

const inFrame = () => window.parent !== window;

interface Ready {
  readonly conversationId?: string;
}

function enter(session: EmbedSession) {
  setEmbedToken(session.token);
  // 只设当前空间，不记成「上次用的空间」：分享链接在同一个站点打开时，不改登录着的人的偏好
  useWorkspaceStore.setState({ id: session.workspaceId });
}

/** 换会话令牌要带的：公开访问是访客 id，认证访问是接入方签的登录信息 */
async function proofFor(access: EmbedConfig['access']) {
  return access === 'member' ? { token: await loginToken() } : { visitor: visitorId() };
}

/** 打开会话、接着上次的对话；令牌快过期时续，续不上（认证访问再要一次登录信息也不行）就报关闭。 */
function useEmbedSession(key: string | undefined, access: EmbedConfig['access'] | undefined) {
  const [ready, setReady] = useState<Ready>();
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    if (!key || !access) return;
    let cancelled = false;
    let timer: number | undefined;
    const schedule = (session: EmbedSession) => {
      const wait = Math.max(30, session.expiresAt - Date.now() / 1000 - RENEW_EARLY_SECONDS);
      timer = window.setTimeout(() => {
        renewEmbedSession()
          .catch(async () => {
            if (access !== 'member') throw new Error(GONE);
            return openEmbedSession(key, await proofFor(access));
          })
          .then((next) => {
            if (cancelled) return;
            enter(next);
            schedule(next);
          })
          .catch(() => {
            if (!cancelled) setProblem(GONE);
          });
      }, wait * 1000);
    };
    proofFor(access)
      .then((proof) => openEmbedSession(key, proof))
      .then(async (session) => {
        if (cancelled) return;
        enter(session);
        schedule(session);
        const page = await listConversations();
        if (!cancelled) setReady({ conversationId: page.items[0]?.id });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setProblem(
          error instanceof ApiError && error.status === 404
            ? GONE
            : error instanceof MissingLoginToken
              ? error.message
              : readableError(error, '没能打开，请稍后重试'),
        );
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      setEmbedToken(null);
    };
  }, [key, access]);

  return { ready, problem };
}

export default function EmbedPage({ standalone = false }: { readonly standalone?: boolean }) {
  const { key } = useParams();
  const config = useQuery({
    queryKey: ['embed-config', key],
    queryFn: () => fetchEmbedConfig(key ?? ''),
    enabled: Boolean(key),
    retry: false,
  });
  const { ready, problem } = useEmbedSession(key, config.data?.access);
  // 点「新对话」：换一个空白的，第一句话发出去时再开
  const [round, setRound] = useState(0);
  const [fresh, setFresh] = useState(false);
  const embedded = !standalone && inFrame();

  useEffect(() => {
    if (!config.data || !embedded) return;
    const { title, color, position } = config.data;
    // 只是名字与外观（本来就公开），接入方页面是哪个网站不用管
    window.parent.postMessage({ type: EMBED_READY, title, color, position }, '*');
  }, [config.data, embedded]);

  if (config.isError || problem) {
    const message = config.isError
      ? config.error instanceof ApiError && config.error.status === 404
        ? GONE
        : readableError(config.error, '没能打开，请稍后重试')
      : problem;
    return (
      <div className="kw-embed">
        <Empty area title="暂时用不了" description={message} />
      </div>
    );
  }
  if (!config.data || !ready) {
    return (
      <div className="kw-embed">
        <Skeleton rows={4} className="kw-page-state" />
      </div>
    );
  }
  const { title, greeting, starters, answerMode } = config.data;
  return (
    <div className={standalone ? 'kw-embed is-standalone' : 'kw-embed'}>
      <header className="kw-embed-header">
        <span className="kw-embed-title">{title}</span>
        <div className="kw-embed-actions">
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            aria-label="新对话"
            onClick={() => {
              setFresh(true);
              setRound((value) => value + 1);
            }}
          >
            <ComposeEditSquare />
          </Button>
          {embedded ? (
            <Button
              color="secondary"
              variant="ghost"
              size="sm"
              uniform
              aria-label="收起"
              onClick={() => window.parent.postMessage({ type: EMBED_CLOSE }, '*')}
            >
              <X />
            </Button>
          ) : null}
        </div>
      </header>
      <EmbedModeContext.Provider value={VISITOR}>
        <EmbeddedChat
          key={round}
          answerMode={answerMode}
          conversationId={fresh ? undefined : ready.conversationId}
          heading={greeting ? undefined : title}
          hint={greeting || undefined}
          starters={starters}
          footnote="回答由 AI 生成，请核对重要信息"
        />
      </EmbedModeContext.Provider>
    </div>
  );
}
