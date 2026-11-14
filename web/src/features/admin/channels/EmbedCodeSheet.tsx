/**
 * 网页嵌入的「嵌入代码」，点一下复制。
 * 公开访问三种用法：悬浮窗（一行 <script>）、整页（<iframe>）、分享链接。
 * 认证访问：先生成认证密钥（明文只显示这一次，放进接入方后端），接入方后端给当前登录用户签登录信息（JWT），悬浮窗按
 * data-token-url 去取；手机 App 整页打开时把登录信息放在地址 # 后面。附各语言签名的示例。
 * 悬浮窗与整页只在「允许嵌入的网站」里能用（网关按它放行）。发布令牌、认证密钥泄露了或不想让旧的再用，就重置。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { useQueryClient } from '@tanstack/react-query';
import { publicOrigin } from '../../../utils/publicOrigin';
import type { Channel } from '../../../api/channels';
import { resetWebKey, resetWebSecret } from '../../../api/channels';
import { readableError } from '../../../api/http';
import { confirm } from '../../../ui/confirmStore';
import { Sheet } from '../../../ui/Sheet';
import { Tabs } from '../../../ui/Tabs';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { useAction } from '../useAction';
import { embedSnippets, identifiedSnippets, SIGNING_SAMPLES } from './webOptions';

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('已复制');
  } catch {
    toast.error('复制没有成功，请手动选中复制');
  }
}

function Code({ code }: { readonly code: string }) {
  return (
    <pre className="kw-embed-code">
      <code>{code}</code>
    </pre>
  );
}

function Snippet({
  title,
  help,
  code,
}: {
  readonly title: string;
  readonly help: string;
  readonly code: string;
}) {
  return (
    <section className="kw-embed-snippet">
      <div className="kw-embed-snippet-head">
        <h3>{title}</h3>
        <Button color="secondary" variant="outline" size="sm" onClick={() => void copy(code)}>
          复制
        </Button>
      </div>
      <p className="kw-secondary">{help}</p>
      <Code code={code} />
    </section>
  );
}

/** 认证密钥：生成或重置，明文只在这一次显示（接口只回这一次） */
function SecretSection({ channel }: { readonly channel: Channel }) {
  const client = useQueryClient();
  const [shown, setShown] = useState<string>();
  const [pending, setPending] = useState(false);
  const generate = async () => {
    if (
      channel.secretSet &&
      !(await confirm({
        title: '重置认证密钥？',
        description: '旧密钥签的登录信息不再认，正开着的小窗也会失效，要把接入方后端里的密钥换成新的。',
        okText: '重置',
        danger: true,
      }))
    )
      return;
    setPending(true);
    try {
      const result = await resetWebSecret(channel.id);
      setShown(result.secret);
      void client.invalidateQueries({ queryKey: KEYS.channels });
    } catch (error) {
      toast.error(readableError(error, '没有生成成功，请稍后重试'));
    } finally {
      setPending(false);
    }
  };
  return (
    <section className="kw-embed-snippet">
      <div className="kw-embed-snippet-head">
        <h3>认证密钥</h3>
        <Button
          color="secondary"
          variant="outline"
          size="sm"
          loading={pending}
          onClick={() => void generate()}
        >
          {channel.secretSet || shown ? '重置认证密钥' : '生成认证密钥'}
        </Button>
      </div>
      {shown ? (
        <>
          <p className="kw-secondary">
            只显示这一次，请马上放进接入方后端的配置里（例如环境变量
            KNOWWORK_EMBED_SECRET），不要写进网页代码。
          </p>
          <div className="kw-embed-secret">
            <Code code={shown} />
            <Button color="secondary" variant="outline" size="sm" onClick={() => void copy(shown)}>
              复制
            </Button>
          </div>
        </>
      ) : (
        <p className="kw-secondary">
          {channel.secretSet
            ? `已生成（末四位 ${channel.secretMask || '****'}）。接入方后端用它签登录信息；忘了就重置一个。`
            : '还没有生成：接入方后端要用它给登录的人签登录信息，先生成一个。'}
        </p>
      )}
    </section>
  );
}

function PublicCode({ webKey }: { readonly webKey: string }) {
  const snippets = embedSnippets(publicOrigin(), webKey);
  return (
    <>
      <Snippet
        title="悬浮窗（推荐）"
        help="贴到网站每个页面的 </body> 前面。右下角出一个按钮，点开是小窗，手机上全屏；按钮颜色、开场白改了不用重新贴。"
        code={snippets.widget}
      />
      <Snippet title="整页" help="放在页面里的某一块，宽高按你们页面调。" code={snippets.frame} />
      <Snippet title="分享链接" help="直接发给别人，打开就能问，不用登录。" code={snippets.link} />
    </>
  );
}

function IdentifiedCode({ channel, webKey }: { readonly channel: Channel; readonly webKey: string }) {
  const snippets = identifiedSnippets(publicOrigin(), webKey);
  return (
    <>
      <SecretSection channel={channel} />
      <Snippet
        title="悬浮窗"
        help="贴到网站每个页面的 </body> 前面。把 /api/knowwork-token 换成你们后端的一个接口：给当前登录的人签登录信息、原样返回（同一个网站，带着你们自己的登录状态）。也可以在页面上设 window.KnowWorkSettings = { getToken: () => ... } 自己取。"
        code={snippets.widget}
      />
      <section className="kw-embed-snippet">
        <h3>后端签登录信息</h3>
        <p className="kw-secondary">
          JWT，HS256，用上面的认证密钥签；sub、exp、jti 必填，有效期不超过 10
          分钟，每次打开签一个新的（只能用一次）。
        </p>
        <Tabs
          items={SIGNING_SAMPLES.map((sample) => ({
            key: sample.label,
            label: sample.label,
            children: <Code code={sample.code} />,
          }))}
        />
      </section>
      <Snippet
        title="手机 App 整页打开"
        help="App 后端签好登录信息，用 WebView 打开这个地址；# 后面的登录信息不会发给服务器，打开后自动从地址栏去掉。"
        code={snippets.app}
      />
    </>
  );
}

export function EmbedCodeSheet({
  channel,
  onClose,
}: {
  readonly channel: Channel;
  readonly onClose: () => void;
}) {
  const reset = useAction((id: string) => resetWebKey(id), {
    invalidate: [KEYS.channels],
    success: '已重置，旧的嵌入代码和分享链接都不能用了，请换成新的',
    failure: '没有重置成功，请稍后重试',
  });
  const web = channel.web;
  if (!web) return null;
  const identified = channel.audience === 'member';
  const allowed = web.origins.length > 0 ? web.origins.join('、') : '';
  return (
    <Sheet title={`嵌入代码 · ${channel.name}`} width={760} onClose={onClose}>
      <p className="kw-secondary">
        {allowed
          ? `小窗只能嵌在这些网站里：${allowed}。要加网站，在「修改」里改「允许嵌入的网站」。`
          : identified
            ? '还没有允许嵌入的网站：小窗嵌不进去，只能用 App 整页打开。要嵌进你们的网站，先在「修改」里填上它的地址。'
            : '还没有允许嵌入的网站，现在只能用分享链接打开。要嵌进你们的网站，先在「修改」里填上它的地址。'}
        {channel.enabled ? '' : ' 这个入口已停用，启用后才能打开。'}
      </p>
      {identified ? (
        <IdentifiedCode channel={channel} webKey={web.webKey} />
      ) : (
        <PublicCode webKey={web.webKey} />
      )}
      <div className="kw-embed-reset">
        <Button
          color="danger"
          variant="outline"
          loading={reset.pending}
          onClick={() =>
            void confirm({
              title: '重置发布令牌？',
              description: '旧的嵌入代码、分享链接和正开着的小窗都会失效，要把网站上的代码换成新的。',
              okText: '重置',
              danger: true,
            }).then((ok) => (ok ? reset.run(channel.id) : undefined))
          }
        >
          重置发布令牌
        </Button>
      </div>
    </Sheet>
  );
}
