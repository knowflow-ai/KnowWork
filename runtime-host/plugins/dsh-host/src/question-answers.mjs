/**
 * 用户回答的整理与校验。提问应答者（前台等待中的问题）和 HTTP 驱动（已转入 continued 的问题）共用。
 *
 * 官方词汇（dsh-user-questions）：每个问题恰好一条回答 `{ id, selected, custom? }`，selected 是选项标签；
 * 单选题填了 custom 时 selected 为空；selected 为空且没有 custom 表示跳过这一题。
 * 官方 `answer()` 只核对问题 id，前台等待中的回答官方不核对（它信任自己的界面）。我们的回答是业务服务
 * 转交的用户输入，所以在这里统一再核对：选的确实是该题的选项、单选最多一个、文字有长度上限，
 * 不让不存在的选项或超长文本进到模型上下文里。
 */

const MAX_QUESTIONS = 64
const MAX_SELECTED = 64
const MAX_ID = 256
const MAX_LABEL = 1000
const MAX_CUSTOM = 4000

export class AnswerError extends Error {}

const isRecord = value => typeof value === 'object' && value !== null && !Array.isArray(value)

function boundedString(value, max, field) {
  if (typeof value !== 'string' || value.length === 0 || value.length > max) {
    throw new AnswerError(`${field} 必须是 1 到 ${max} 个字符的字符串`)
  }
  return value
}

function parseItem(raw, index) {
  if (!isRecord(raw)) throw new AnswerError(`answers[${index}] 必须是对象`)
  const id = boundedString(raw.id, MAX_ID, `answers[${index}].id`)
  if (!Array.isArray(raw.selected) || raw.selected.length > MAX_SELECTED) {
    throw new AnswerError(`answers[${index}].selected 必须是不超过 ${MAX_SELECTED} 项的数组`)
  }
  const selected = raw.selected.map((label, i) => boundedString(label, MAX_LABEL, `answers[${index}].selected[${i}]`))
  if (raw.custom !== undefined && (typeof raw.custom !== 'string' || raw.custom.length > MAX_CUSTOM)) {
    throw new AnswerError(`answers[${index}].custom 必须是不超过 ${MAX_CUSTOM} 个字符的字符串`)
  }
  const custom = typeof raw.custom === 'string' && raw.custom.trim().length > 0 ? raw.custom : undefined
  return { id, selected, ...(custom === undefined ? {} : { custom }) }
}

/**
 * 请求体 → 官方回答批次。只看形状，不看问题。
 * @returns {{ answers: { id: string, selected: string[], custom?: string }[] }}
 */
export function parseAnswer(raw) {
  if (!isRecord(raw) || !Array.isArray(raw.answers) || raw.answers.length === 0 || raw.answers.length > MAX_QUESTIONS) {
    throw new AnswerError(`answers 必须是 1 到 ${MAX_QUESTIONS} 项的数组`)
  }
  return { answers: raw.answers.map(parseItem) }
}

/**
 * 回答是否对得上这一批问题：每题恰好一条，选项都存在，单选题最多选一个、填了文字就不再选项。
 * @param {readonly { id: string, options?: readonly { label: string }[], multiSelect?: boolean }[]} questions
 * @param {{ answers: readonly { id: string, selected: readonly string[], custom?: string }[] }} answer
 */
export function checkAnswer(questions, answer) {
  const byId = new Map(questions.map(question => [question.id, question]))
  const seen = new Set()
  for (const item of answer.answers) {
    const question = byId.get(item.id)
    if (question === undefined) throw new AnswerError(`没有这个问题：${item.id}`)
    if (seen.has(item.id)) throw new AnswerError(`问题 ${item.id} 回答了不止一次`)
    seen.add(item.id)
    const labels = new Set((question.options ?? []).map(option => option.label))
    const unknown = item.selected.find(label => !labels.has(label))
    if (unknown !== undefined) throw new AnswerError(`问题 ${item.id} 没有选项「${unknown}」`)
    if (new Set(item.selected).size !== item.selected.length) throw new AnswerError(`问题 ${item.id} 的选项重复了`)
    if (question.multiSelect !== true && item.selected.length > (item.custom === undefined ? 1 : 0)) {
      throw new AnswerError(`问题 ${item.id} 是单选题`)
    }
  }
  if (seen.size !== byId.size) throw new AnswerError('每个问题都要有一条回答（跳过的题 selected 留空）')
}
