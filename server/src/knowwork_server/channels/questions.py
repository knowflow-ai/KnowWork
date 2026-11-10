"""智能体的追问（官方 ask_user_question，kernel.contract.PendingQuestion）在 IM 里怎么问、怎么收回答。

网页上是一张能点选项的卡片；IM 里点不了，就把问题和带序号的选项发出去，同一个对话位置的下一条消息当回答：
- 只问了一个：回序号（多选的几个序号用空格、逗号隔开）就是选那几项，和某个选项一字不差也算选它，别的写法是他自己的说法；
- 一次问了几个：一条消息一起答，每题都交这段话（模型看得懂「按付款时间，不含退款」，逐题拆反而容易拆错）。
"""

import re

from ..kernel.contract import PendingQuestion, Question, QuestionAnswer, QuestionAnswerItem

_NUMBERS = re.compile(r"^\s*\d{1,2}(?:\s*[,，、\s]\s*\d{1,2})*\s*$")
MAX_CUSTOM = 4000  # 同合同 QuestionAnswerItem.custom 的上限


def render(pending: PendingQuestion) -> str:
    if len(pending.questions) == 1:
        question = pending.questions[0]
        lines = [f"**{question.question}**", *([question.detail] if question.detail else [])]
        lines += [f"{i}. {o.label}" + (f"（{o.description}）" if o.description else "")
                  for i, o in enumerate(question.options, 1)]
        if not question.options:
            return "\n".join([*lines, "直接回复就行。"])
        how = "回复序号（可以选几个，用空格隔开）" if question.multi_select else "回复序号"
        return "\n".join([*lines, f"{how}，或者直接写你的说法。"])
    lines = ["有几个问题要你补充，在一条消息里一起回答就行："]
    for i, question in enumerate(pending.questions, 1):
        choices = f"（可选：{'、'.join(o.label for o in question.options)}）" if question.options else ""
        lines.append(f"{i}. {question.question}{choices}")
    return "\n".join(lines)


def reply(pending: PendingQuestion, text: str) -> QuestionAnswer:
    text = text.strip()[:MAX_CUSTOM]
    if len(pending.questions) == 1:
        return QuestionAnswer(answers=(_one(pending.questions[0], text),))
    return QuestionAnswer(answers=tuple(QuestionAnswerItem(id=q.id, custom=text) for q in pending.questions))


def _one(question: Question, text: str) -> QuestionAnswerItem:
    labels = [option.label for option in question.options]
    if labels and _NUMBERS.fullmatch(text):
        picked = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", text)))
        if all(1 <= n <= len(labels) for n in picked) and (question.multi_select or len(picked) == 1):
            return QuestionAnswerItem(id=question.id, selected=tuple(labels[n - 1] for n in picked))
    if text in labels:
        return QuestionAnswerItem(id=question.id, selected=(text,))
    return QuestionAnswerItem(id=question.id, custom=text)
