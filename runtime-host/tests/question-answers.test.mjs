/**
 * 用户回答的整理与校验（question-answers.mjs）：纯函数，不起宿主。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AnswerError, checkAnswer, parseAnswer } from '../plugins/dsh-host/src/question-answers.mjs'

const QUESTIONS = [
  { id: 'fmt', question: '导出成什么格式？', options: [{ label: 'Excel (Recommended)' }, { label: 'PDF' }] },
  { id: 'cols', question: '要哪些列？', multiSelect: true, options: [{ label: '金额' }, { label: '日期' }, { label: '客户' }] },
  { id: 'note', question: '还有什么要求？' },
]

test('回答形状：去掉空白的 custom，只留官方字段', () => {
  const answer = parseAnswer({ answers: [{ id: 'fmt', selected: ['PDF'], custom: '   ', extra: 1 }] })
  assert.deepEqual(answer, { answers: [{ id: 'fmt', selected: ['PDF'] }] })
})

test('回答形状不对一律 AnswerError', () => {
  for (const raw of [
    undefined,
    {},
    { answers: [] },
    { answers: ['x'] },
    { answers: [{ id: '', selected: [] }] },
    { answers: [{ id: 'fmt' }] },
    { answers: [{ id: 'fmt', selected: [1] }] },
    { answers: [{ id: 'fmt', selected: [], custom: 'x'.repeat(4001) }] },
    { answers: Array.from({ length: 65 }, (_, i) => ({ id: `q${i}`, selected: [] })) },
  ]) {
    assert.throws(() => parseAnswer(raw), AnswerError, JSON.stringify(raw)?.slice(0, 80))
  }
})

test('对得上问题的回答通过：单选、多选、文字、跳过', () => {
  checkAnswer(QUESTIONS, parseAnswer({
    answers: [
      { id: 'fmt', selected: ['Excel (Recommended)'] },
      { id: 'cols', selected: ['金额', '客户'], custom: '再加备注列' },
      { id: 'note', selected: [], custom: '按月汇总' },
    ],
  }))
  checkAnswer(QUESTIONS, parseAnswer({
    answers: [
      { id: 'fmt', selected: [], custom: 'CSV' },
      { id: 'cols', selected: [] },
      { id: 'note', selected: [] },
    ],
  }))
})

test('对不上问题的回答被拒：不存在的选项、单选多选、缺题、重复、未知题', () => {
  const base = [{ id: 'cols', selected: [] }, { id: 'note', selected: [] }]
  const cases = [
    [{ id: 'fmt', selected: ['Word'] }, ...base],
    [{ id: 'fmt', selected: ['PDF', 'Excel (Recommended)'] }, ...base],
    [{ id: 'fmt', selected: ['PDF'], custom: 'CSV' }, ...base],
    [{ id: 'cols', selected: ['金额', '金额'] }, { id: 'fmt', selected: [] }, { id: 'note', selected: [] }],
    base,
    [{ id: 'fmt', selected: [] }, { id: 'fmt', selected: [] }, ...base],
    [{ id: 'fmt', selected: [] }, ...base, { id: 'ghost', selected: [] }],
    [{ id: 'note', selected: ['随便'] }, { id: 'fmt', selected: [] }, { id: 'cols', selected: [] }],
  ]
  for (const answers of cases) {
    assert.throws(() => checkAnswer(QUESTIONS, parseAnswer({ answers })), AnswerError, JSON.stringify(answers))
  }
})
