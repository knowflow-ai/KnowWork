# API 使用说明

KnowWork 的全部功能都有 HTTP 接口，网页界面用的就是这些接口。别的系统要接 KnowWork，用 API Key 调它们。

## 接口文档

部署好以后打开 `https://<你的域名>/api/docs`（本机开发是 http://127.0.0.1:5173/api/docs），是 Swagger 页面：
按「对话」「技能」「成员与权限」……分组列出每个接口的参数和说明，可以直接在页面上试。OpenAPI 描述在
`/api/openapi.json`，能导入 Postman、Apifox 或者生成客户端代码。

文档页本身不用登录。在页面上试接口时，点右上角「Authorize」填 API Key；已经在同一个浏览器登录了 KnowWork 的，
不填也能试（带的是登录的 Cookie）。

## 认证

程序调用用 API Key，放在请求头里：

```
Authorization: Bearer ak_xxxxxxxx
```

- **新建**：在网页左下角的账号菜单 →「个人设置」→「API Key」里新建。密钥只在建的那一次显示，丢了只能重建；
- **权限范围**：建的时候选，一个 Key 只能调它范围里的接口，而且不会超过你本人在空间里的权限：

| 范围 | 能做什么 |
|---|---|
| `conversations:use` | 使用对话：新建对话、发消息、取回答 |
| `knowledge:read` | 检索知识：对外知识接口与 MCP |
| `knowledge:manage` | 管理知识源 |
| `workspace:manage` | 管理成员与授权 |
| `models:manage` | 查看用量统计（模型配置只有系统管理员在网页上能改，API Key 不行） |
| `tools:manage` | 管理工具与 MCP 服务 |
| `audit:read` | 查看审计日志 |

- **有效期**：建的时候选，到期自动失效；不用了在同一页删掉。

## 几个约定

- 路径里的 `{workspace_id}` 是工作空间编号，只有一个空间时是 `default`；
- 出错时返回对应的 HTTP 状态码和 `{"detail": "说明"}`，说明是给人看的中文，不含内部细节；
- 时间都是带时区的 ISO 8601（例如 `2026-10-28T09:30:00+08:00`）。

## 对外知识接口

给别的系统检索、阅读知识库用，权限范围 `knowledge:read`，只查得到 Key 所属用户被授权的知识库。

列出能查的知识库（说明、元数据字段、标签）：

```sh
curl -H "Authorization: Bearer $KEY" https://kw.example.com/api/v1/knowledge-bases
```

检索：

```sh
curl -X POST https://kw.example.com/api/v1/knowledge/search \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"query": "差旅住宿标准", "top_n": 8}'
```

可选参数：`knowledge_bases`（只查这几个库，写成列出知识库时返回的 id）、`mode`（`hybrid` 默认，`semantic`
只按语义，`keyword` 只按字面，适合编号、型号）、`folders`、`tags`、`filters`（按元数据字段筛，字段名与可选值见
列出知识库的结果）、`multi_hop`、`visual`（这一次开不开多跳检索、视觉检索，`true` 或 `false`；不填按知识库自己的设置。
只对列出知识库时 `routes` 里有这一项的库起作用，知识库设成检索时关闭的打不开）。

结果里每个片段带 `source_id`、`doc_ref` 和 `span_ref`。要看上下文或整篇原文，接着调阅读：

```sh
curl -X POST https://kw.example.com/api/v1/knowledge/read \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"source_id": "<检索结果里的 source_id>", "doc_ref": "<检索结果里的 doc_ref>", "chunk": "<span_ref>", "context": 2}'
```

阅读还能按目录读（`outline: true` 先看目录，再用 `section` 读某一节）、在文档里查找（`find`），没读完时用返回的
`next_cursor` 续读。

## MCP

同样的检索、阅读能力也以 MCP（流式 HTTP）提供，地址是 `https://<你的域名>/api/v1/mcp`，同样用带 `knowledge:read`
的 API Key。在支持 MCP 的客户端里这样配：

```json
{
  "mcpServers": {
    "knowwork": {
      "url": "https://kw.example.com/api/v1/mcp",
      "headers": { "Authorization": "Bearer ak_xxxxxxxx" }
    }
  }
}
```

提供三个工具：列出知识库、检索、阅读，参数与上面的接口相同。

## 对话

用 `conversations:use` 范围的 Key，可以像网页里一样和智能体对话：

1. 新建对话：`POST /api/workspaces/{workspace_id}/conversations`，返回对话 `id`；
2. 发消息：`POST /api/workspaces/{workspace_id}/conversations/{id}/messages`，body 是 `{"text": "问题"}`；
3. 取回答：`GET /api/workspaces/{workspace_id}/conversations/{id}/events` 是事件流（Server-Sent Events），回答的
   文字、调了哪些工具、引用了哪些原文都在里面，回合结束时有结束事件。

各接口的参数、事件的字段见接口文档的「对话」一组。
