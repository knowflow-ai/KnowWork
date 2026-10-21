---
name: office-docx
description: 处理 Word 文档（.docx / .dotx）时使用。包括：用户提到 Word、docx、Word 文件、Word 模板，或要一份带目录、标题层级、页码、页眉信头等格式的正式文档；要从 .docx 里提取或重组内容；要在 Word 文件里插图、换图、查找替换；要以修订模式（带修订痕迹、可逐条接受拒绝）改稿或加批注；要把内容整理成一份像样的 Word 文件。用户要的报告、纪要、函件、模板、通知等交付物明确是 Word 或 .docx 时也用它。PDF、表格、腾讯文档/飞书文档，或与生成文档无关的编程任务不用。
---

# DOCX：生成、修改与读取

`.docx` 是一个装着 XML 的压缩包。按任务选做法：

| 任务 | 做法 |
|---|---|
| **生成**新文档 | 写一个 `docx`（npm 包，即 docx-js）脚本——坑点见下 |
| **修改**已有文档 | 解压 → 改 `word/document.xml` → 打包（docx-js 打不开已有文件） |
| **读取**内容 | `pandoc -t markdown 文件.docx` |

> 下面的脚本都在 `/opt/knowwork/office/`。用户给的原文件在 `/workspace/input`（只读），
> 中间文件放 `/workspace/tmp`，要交给用户的放 `/workspace/output`，写好后调用 `present` 交付（例如 `present({"files":[{"path":"/workspace/output/报告.docx"}]})`）。知识库里的文档不是
> 这里的文件，改不了原件；用户要改它，请他把那份 .docx 作为附件上传。

## 用 docx-js 生成——坑点

`docx` 已经装好，直接 `require('docx')`，不要 `npm install`——沙箱没有网络，装不了。
API 你是知道的，要当心的是这些：

- **纸张默认 A4。** 要美式 Letter 就设 `page: { size: { width: 12240, height: 15840 } }`
  （单位 DXA，1440 = 1 英寸）。国内文档用默认 A4 即可。
- **横向：** 传纵向的宽高，再加 `orientation: PageOrientation.LANDSCAPE`——docx-js 会自己对调宽高。
- **表格要两处宽度：** 表格上设 `columnWidths`，**每个**单元格上也设 `width`，都用
  `WidthType.DXA`（PERCENTAGE 在部分软件里会错乱）。各列宽之和必须等于表格总宽。
- **单元格底色：** 用 `ShadingType.CLEAR`，不要用 `SOLID`（会渲染成全黑）。
- **列表：** 不要直接写 `•`；用 `numbering` 配置，`LevelFormat.BULLET`。
- **`ImageRun` 必须写 `type:`**（`"png"`、`"jpg"` 等）。
- **`PageBreak` 必须放在 `Paragraph` 里面。**
- **不要用 `\n` 换行**——每一段是一个单独的 `Paragraph`。
- **目录：** 标题必须用内置的 `HeadingLevel.*`；自定义标题样式要设 `outlineLevel`，否则进不了目录。
- **不要拿表格当横线用**——用段落的下边框。
- **点状前导符 / 同一行右对齐：** 在 `TextRun` 里用 `PositionalTab`
  （`alignment: PositionalTabAlignment.RIGHT`、`leader: PositionalTabLeader.DOT`），不要用一串 `.` 或空格去凑。
- **中文字体：** 只设 `font: "宋体"` 时 Word 会拿它当西文字体，中文仍回退到别的字体。要用
  `font: { ascii: "Times New Roman", eastAsia: "宋体", hAnsi: "Times New Roman" }` 这种写法，
  在默认样式（`styles.default.document.run`）和各级标题上都设。

## 检查输出

写完 `.docx` 之后先转成 PDF：

```bash
python3 /opt/knowwork/office/soffice.py --headless --convert-to pdf --outdir /workspace/tmp /workspace/output/文件.docx
```

**先看你的工具列表里有没有 `read_image`**，再决定怎么核对：

- **有 `read_image`**：转成图片逐页看版式。

  ```bash
  pdftoppm -jpeg -r 100 /workspace/tmp/文件.pdf /workspace/tmp/page
  ls /workspace/tmp/page-*.jpg   # 再用 read_image 查看这些图片
  ```

  `pdftoppm` 按总页数补零（`page-01.jpg`……`page-12.jpg`）。
- **没有 `read_image`**：不要转图片，直接 `pdftotext -layout /workspace/tmp/文件.pdf - | head -100`
  核对文字、顺序和分页。

## 修改已有文档

老的 `.doc` 要先转：`python3 /opt/knowwork/office/soffice.py --headless --convert-to docx --outdir /workspace/tmp /workspace/input/文件.doc`。

```bash
mkdir -p /workspace/tmp/unpacked && cd /workspace/tmp
unzip -q /workspace/input/原文件.docx -d unpacked/
find unpacked -type l -delete                                # 去掉符号链接——外来文件不可信
python3 /opt/knowwork/office/merge_runs.py unpacked/         # 合并碎片，让原文能搜到
# 原地改 unpacked/word/document.xml——不要重新排版或美化缩进
(cd unpacked && rm -f /workspace/output/原文件名.docx && zip -Xr /workspace/output/原文件名.docx .)
python3 /opt/knowwork/office/validate.py /workspace/output/原文件名.docx --original /workspace/input/原文件.docx
# 用了修订模式？加 --author "你写修订时用的名字"，检查每处改动都带了修订标记
```

Word 会因为修订号、拼写检查标记把一句话拆成很多个 `<w:r>`，所以你在文档里看到的一句话，
在 XML 里常常不是连续的一串。`merge_runs.py` 只合并 `word/document.xml`（以及页眉页脚、
脚注尾注）里格式完全相同的相邻片段，内容和显示都不变；它也能直接处理 `.docx`
（`merge_runs.py 文件.docx -o 合并后.docx`）。

**修订模式：** 用户要"修订 / 留痕 / 能逐条接受拒绝"时，改动必须包进修订标记，验证时加
`--author "你用的作者名"`（需要同时给 `--original`）——它会报出所有**改了字却没包进**
`<w:ins>`/`<w:del>` 的地方，这种错误很容易犯，而且在"接受后"的视图里完全看不出来。
`<w:ins>`/`<w:del>` 都要带 `w:id`、`w:author`、`w:date` 三个属性。`<w:del>` 里面的文字元素
是 `<w:delText>`，不是 `<w:t>`。被删除的段落标记（`<w:pPr><w:rPr><w:del w:id=".."
w:author=".." w:date=".."/></w:rPr></w:pPr>`）表示"这一段并入下一段"——所以整段删除等于
这个标记，再加上把段里每个 run 都包进 `<w:del>`。这个 `<w:del/>` 必须排在 rPr 其他子元素
之前，顺序由 schema 规定。

**只改几处文字时的简便做法：** 把改动写成清单交给 `docx_redline.py`，它按原文片段定位、
保留原格式，默认就是修订模式：

```bash
# /workspace/tmp/edits.json：[{"find": "原文里连续的一段", "replace": "新文字"},
#                             {"find": "...", "comment": "批注内容"}]
python3 /opt/knowwork/office/docx_redline.py /workspace/input/原文件.docx /workspace/tmp/edits.json /workspace/output/原文件名.docx
```

`find` 要从原文原样复制并能唯一定位（出现多次时加 `"occurrence": 第几处`）；只改需要改的
字，不要把整段当 `find` 重写——那样修订里全是删除线，用户没法逐条审。用户明确说"直接改好、
不要修订痕迹"时加 `--no-track`。任何一项失败它都不写文件。结构性改动（增删段落、改表格、
换图）还是走上面的解压改 XML。

要得到接受全部修订后的干净稿：`python3 /opt/knowwork/office/accept_changes.py 输入.docx 输出.docx`。

接受一个被删除的段落标记时，这一段应当并入下一段，所以**全部 run 都被删掉**的段落会整段
消失——Word 就是这样做的，`accept_changes.py` 也按这个规则处理。`pandoc --track-changes=accept`
不会合并段落，它读出来的结果里会留下一个空段落，自动编号时看上去像一个空的编号项。这是
那个视图的产物，不是文档的问题；段落删除对不对，看 XML。

## 批注

批注要好几个互相引用的部件一起对上，用脚本建：

```bash
# 对已经解压的目录（同时还要改 document.xml 时优先用这种）
python3 /opt/knowwork/office/comment.py unpacked/ "付款比例偏高，建议按纪要改为 20%"

# 直接对 .docx
python3 /opt/knowwork/office/comment.py 合同.docx "付款比例偏高" -o 带批注.docx
```

脚本会建好批注部件、关系和内容类型，自动分配编号，然后打印要放进 `word/document.xml` 的
`<w:commentRangeStart>`/`<w:commentRangeEnd>`/`<w:commentReference>` 三段——把它们放到要批注
的文字两边之前，批注虽然存在但看不见。

## 回答里说什么

说清楚改了哪几处（原文 → 新文字，一句话一处），是否为修订模式、在 Word 里可以逐条接受或
拒绝。不要写 `/workspace` 路径，用 `present` 交付的文件会作为附件随回答交给用户。

## 依赖

`docx`（npm，已装）· `pandoc` · LibreOffice（通过 `/opt/knowwork/office/soffice.py` 调用）·
`pdftoppm`（Poppler）· `lxml`、`python-docx`（Python，已装）
