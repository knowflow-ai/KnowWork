/** 网页嵌入配置的校验与整理（WebFields、ChannelDialog 用；规则同服务端 channels/settings.py 的 WebOptions）。 */
import type { WebOptions } from '../../../api/channels';

export const ORIGIN = /^https?:\/\/(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d{1,5})?$/;
export const MAX_ORIGINS = 20;
export const MAX_STARTERS = 4;

/** 一行一个的多行文字整理成列表（去掉空行与两头空白） */
export const lines = (value: string) =>
  value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

/** 网页嵌入的配置哪里不对；都对返回 undefined */
export function webProblem(web: WebOptions): string | undefined {
  const bad = web.origins.find((origin) => !ORIGIN.test(origin));
  if (bad) return `网站写成 https://bi.example.com 这样（不带路径）：${bad}`;
  if (web.origins.length > MAX_ORIGINS) return `允许嵌入的网站最多 ${MAX_ORIGINS} 个`;
  if (web.starters.length > MAX_STARTERS) return `快捷提问最多 ${MAX_STARTERS} 个`;
  if (web.starters.some((s) => s.length > 60)) return '每个快捷提问不超过 60 个字';
  if (web.color && !/^#[0-9A-Fa-f]{6}$/.test(web.color)) return '按钮颜色写成 #1F6FEB 这样';
  if (!(web.perMinute >= 1 && web.perMinute <= 120)) return '每个访客每分钟的提问次数在 1 到 120 之间';
  if (!(web.perDay >= 1 && web.perDay <= 1_000_000)) return '每天的提问次数在 1 到 100 万之间';
  return undefined;
}

/** 嵌入代码的三种用法：悬浮窗脚本、整页 iframe、分享链接（origin 是 KnowWork 的地址，key 是发布令牌） */
export function embedSnippets(origin: string, key: string) {
  const encoded = encodeURIComponent(key);
  return {
    widget: `<script src="${origin}/embed/v1/widget.js" data-channel="${key}" async></script>`,
    frame: `<iframe src="${origin}/embed/${encoded}" style="width: 100%; height: 640px; border: 0" allow="clipboard-write"></iframe>`,
    link: `${origin}/s/${encoded}`,
  };
}

/** 认证访问的嵌入代码：悬浮窗带 data-token-url（接入方后端给当前登录用户签登录信息的地址）；App 整页打开时登录信息放在 # 后面 */
export function identifiedSnippets(origin: string, key: string) {
  const encoded = encodeURIComponent(key);
  return {
    widget: `<script src="${origin}/embed/v1/widget.js" data-channel="${key}" data-token-url="/api/knowwork-token" async></script>`,
    app: `${origin}/embed/${encoded}#token=<接入方后端签的登录信息>`,
  };
}

/** 接入方后端签登录信息（JWT，HS256）的示例：密钥放在接入方后端的环境变量里，不进前端代码 */
export const SIGNING_SAMPLES: readonly { readonly label: string; readonly code: string }[] = [
  {
    label: 'Python',
    code: `import os, time, uuid
import jwt  # pip install pyjwt

def knowwork_token(user):
    now = int(time.time())
    return jwt.encode({
        "sub": str(user.id),     # 你们系统里稳定不变的用户编号（不要用会改的用户名）
        "name": user.name,       # 可选：显示的名字
        "email": user.email,     # 可选：用来对上 KnowWork 里已有的人
        "groups": user.roles,    # 可选：按「组对照」进出用户组
        "iat": now, "exp": now + 300, "jti": uuid.uuid4().hex,
    }, os.environ["KNOWWORK_EMBED_SECRET"], algorithm="HS256")`,
  },
  {
    label: 'Node.js',
    code: `const jwt = require('jsonwebtoken'); // npm install jsonwebtoken
const crypto = require('crypto');

function knowworkToken(user) {
  return jwt.sign(
    { sub: String(user.id), name: user.name, email: user.email, groups: user.roles, jti: crypto.randomUUID() },
    process.env.KNOWWORK_EMBED_SECRET,
    { algorithm: 'HS256', expiresIn: 300 },
  );
}`,
  },
  {
    label: 'Java',
    code: `// io.jsonwebtoken:jjwt-api、jjwt-impl、jjwt-jackson 0.12.x
SecretKey key = Keys.hmacShaKeyFor(System.getenv("KNOWWORK_EMBED_SECRET").getBytes(StandardCharsets.UTF_8));
Instant now = Instant.now();
String token = Jwts.builder()
    .subject(user.getId())                 // 你们系统里稳定不变的用户编号
    .claim("name", user.getName())
    .claim("email", user.getEmail())       // 可选
    .claim("groups", user.getRoles())      // 可选
    .issuedAt(Date.from(now))
    .expiration(Date.from(now.plusSeconds(300)))
    .id(UUID.randomUUID().toString())
    .signWith(key, Jwts.SIG.HS256)
    .compact();`,
  },
];
