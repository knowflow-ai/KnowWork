// Office 容器里的 MCP 门卫。
// GenOffice 自带的 MCP 服务（智能体经它改打开着的文档）只听容器内的 127.0.0.1、没有口令：它本来是给同一台电脑上的
// 智能体用的。业务服务要从容器外调它，所以在容器网卡上开一个端口，先核对口令（Authorization: Bearer），
// 再原样转给本机的 MCP 服务，Host 改成它认的 127.0.0.1:<端口>、去掉 Origin（它防 DNS 重绑定，只放行本机名字）。
// 不改 GenOffice。用 GenOffice 自带的 Node 跑：ELECTRON_RUN_AS_NODE=1 /opt/genoffice/genoffice mcp-gate.mjs
import { timingSafeEqual } from 'node:crypto'
import http from 'node:http'

const token = process.env.KNOWWORK_OFFICE_MCP_TOKEN ?? ''
const listenPort = Number(process.env.KNOWWORK_OFFICE_MCP_GATE_PORT ?? 8932)
const upstreamPort = Number(process.env.KNOWWORK_OFFICE_MCP_PORT ?? 3093)
if (token.length < 16) {
  console.error('[mcp-gate] KNOWWORK_OFFICE_MCP_TOKEN 没设或太短，不启动')
  process.exit(1)
}
const expected = Buffer.from(`Bearer ${token}`)

function authorized(header) {
  const given = Buffer.from(header ?? '')
  return given.length === expected.length && timingSafeEqual(given, expected)
}

const server = http.createServer((req, res) => {
  if (!authorized(req.headers.authorization)) {
    res.writeHead(401, { 'content-type': 'application/json' }).end('{"error":"unauthorized"}')
    return
  }
  const headers = { ...req.headers, host: `127.0.0.1:${upstreamPort}` }
  delete headers.authorization
  delete headers.origin
  const upstream = http.request(
    { host: '127.0.0.1', port: upstreamPort, method: req.method, path: req.url, headers },
    (up) => {
      res.writeHead(up.statusCode ?? 502, up.headers)
      up.pipe(res)
    },
  )
  upstream.on('error', (err) => {
    // MCP 服务还没起来（应用刚启动）或已经退出
    if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: `office mcp unavailable: ${err.code ?? err.message}` }))
  })
  req.pipe(upstream)
})
server.listen(listenPort, '0.0.0.0', () => console.log(`[mcp-gate] :${listenPort} -> 127.0.0.1:${upstreamPort}`))
