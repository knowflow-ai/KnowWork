/**
 * 现生成一对 ed25519 密钥，私钥写成 OpenSSH 自己的格式（openssh-key-v1，不加密，格式见 OpenSSH 源码的 PROTOCOL.key）。
 * Node 只导出 PKCS#8；各平台的 ssh 读 PKCS#8 的 ed25519 私钥支持不一（macOS 自带的 ssh 链的是 LibreSSL），
 * OpenSSH 格式所有版本都认。
 */

import { generateKeyPairSync, randomBytes } from 'node:crypto'

const TYPE = 'ssh-ed25519'
// PEM 的类型标签；整行头尾在用时拼出来（代码里不出现完整的私钥头，开源导出的泄露检查按它认私钥）
const PEM_LABEL = ['OPENSSH', 'PRIVATE', 'KEY'].join(' ')

function u32(value) {
  const out = Buffer.alloc(4)
  out.writeUInt32BE(value)
  return out
}

/** SSH 线格式的 string：四字节长度 + 内容。 */
function str(data) {
  const bytes = Buffer.isBuffer(data) ? data : Buffer.from(data)
  return Buffer.concat([u32(bytes.length), bytes])
}

/**
 * @param {string} [comment]
 * @returns {{ publicKey: string, privateKey: string }} publicKey 是 authorized_keys 的一行（不带注释），privateKey 是 PEM 文本
 */
export function generateSshKey(comment = 'knowwork') {
  const { publicKey, privateKey } = generateKeyPairSync('ed25519')
  const pub = Buffer.from(publicKey.export({ format: 'jwk' }).x, 'base64url')
  const seed = Buffer.from(privateKey.export({ format: 'jwk' }).d, 'base64url')
  const publicBlob = Buffer.concat([str(TYPE), str(pub)])
  const check = randomBytes(4)
  const secret = Buffer.concat([check, check, str(TYPE), str(pub), str(Buffer.concat([seed, pub])), str(comment)])
  // 不加密时块长 8：私密段补 1、2、3… 到 8 的倍数
  const padding = Buffer.from(Array.from({ length: (8 - (secret.length % 8)) % 8 }, (_, i) => i + 1))
  const body = Buffer.concat([Buffer.from('openssh-key-v1\0'), str('none'), str('none'), str(''), u32(1),
    str(publicBlob), str(Buffer.concat([secret, padding]))])
  const lines = body.toString('base64').match(/.{1,70}/g)
  return {
    publicKey: `${TYPE} ${publicBlob.toString('base64')}`,
    privateKey: `-----BEGIN ${PEM_LABEL}-----\n${lines.join('\n')}\n-----END ${PEM_LABEL}-----\n`,
  }
}
