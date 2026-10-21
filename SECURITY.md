# 安全

## 报告漏洞

请不要在公开 issue、讨论区或 PR 里披露漏洞。到本仓库的 **Security → Report a vulnerability** 私下提交，
写清楚影响的版本、复现步骤和可能的影响。我们会尽快确认，修复发布后再公开细节，并在说明里致谢（如果你愿意）。

## 支持的版本

只有最新发布的版本会收到安全修复。

## 部署时需要知道的

- **会话沙箱默认挂载 `docker.sock`**，这等于给了业务服务宿主机 root 权限，是为了按会话创建隔离容器而有意为之，
  只适合私有化单机部署。不需要时用 `deploy/install.sh --no-sandbox` 关掉，或改用远端 Docker。
- `deploy/.env` 与 Docker 卷 `knowwork_secrets` 里有凭证和部署主密钥，不要提交到仓库，也不要随意复制。
- 默认用 http 访问。对外开放前请在网关前配好 HTTPS，并把 `KNOWWORK_COOKIE_SECURE` 改成 `true`。
