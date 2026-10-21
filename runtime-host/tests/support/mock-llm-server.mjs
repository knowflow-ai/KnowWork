/**
 * 给业务服务（Python）端到端测试用的官方模拟模型服务启动器。
 * 参数是一段 JSON（startMockLlmServer 的选项）；就绪后在 stdout 打一行
 * `{"mockReady":true,"baseURL":...,"controlURL":...}`。`GET {controlURL}/requests` 返回模拟服务收到的全部请求。
 * stdin 关闭或收到 SIGTERM 时退出。
 */

import { createServer } from 'node:http'
import { startMockLlmServer } from '@deepseek-ai/dsh-llm-mock-server'

const options = JSON.parse(process.argv[2] ?? '{}')
const llm = await startMockLlmServer({ repeatLast: true, ...options })
const control = createServer((req, res) => {
  res.writeHead(req.url === '/requests' ? 200 : 404, { 'content-type': 'application/json' })
  res.end(JSON.stringify(req.url === '/requests' ? llm.requests : { error: 'not found' }))
})
await new Promise(resolve => control.listen(0, '127.0.0.1', resolve))
process.stdout.write(`${JSON.stringify({ mockReady: true, baseURL: llm.baseURL, controlURL: `http://127.0.0.1:${control.address().port}` })}\n`)

const shutdown = async () => {
  control.close()
  await llm.close?.()
  process.exit(0)
}
process.stdin.on('end', shutdown)
process.stdin.resume()
process.on('SIGTERM', shutdown)
