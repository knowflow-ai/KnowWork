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
| **读取**内容 | `markitdown 文件.docx`；要看修订痕迹用 `pandoc --track-changes=all -t markdown 文件.docx`（云端对话才有 pandoc） |

> 命令里的 `office/…` 是本技能资源目录（加载技能时给出的 Base directory）下的辅助脚本，运行时按资源目录写成完整路径；其余相对路径都相对当前工作目录。
> **解释器与库：** 工具列表里有 `load_workspace_dependencies`（本机对话）就先调一次：Python 脚本（包括 `office/` 下的）都用它返回的 `python` 跑，`markitdown` 写成 `"<python>" -m markitdown`；Node 脚本用它返回的 `node` 跑，`NODE_PATH` 设成它返回的 `nodePackages`。没有这个工具（云端对话的沙箱）就直接用 `python3`、`node`、`markitdown`，下面的命令按这种写法写。用到的库都已带好，不要 `pip install`、`npm install`，也不要另找系统里的解释器。
> **文件放哪：** 用户给的原文件只读、不要原地改（云端对话的附件在 `input/`；本机对话的原文件就在工作文件夹里，用用户说的路径）。成品存进当前工作目录（用户指定了位置就照用户说的），按内容起名、不和已有文件重名；生成用的脚本也放工作目录，按用途起名（如 `生成报告.js`；Python 脚本别叫 `docx.py`、`pptx.py` 这类会挡住库的名字）。
> **中间文件：** 解压出来的部件、渲染的页面图这类一次性的东西，放进这次新建的隐藏临时目录：`mktemp -d ./.dsh-office.XXXXXXXX` 会打印出目录名（下文写作 `<临时目录>`），之后的命令都直接写这个名字（每条命令是新的 shell，变量留不下来）。用完——失败了也一样——只删这个目录（`rm -rf <临时目录>`）；不要用固定的目录名，不要删工作目录本身，回答里不要链接已经删掉的图片。
> 写好后调用 `present` 交付成品（例如 `present({"files":[{"path":"报告.docx"}]})`），中间文件不交付。
> 知识库里的文档不是这里的文件，改不了原件；用户要改它，请他把那份 .docx 给你（云端对话作为附件上传，本机对话放进工作文件夹）。

## 用 docx-js 生成——坑点

`docx` 已经带好，直接 `require('docx')`（本机对话照开头的写法设 `NODE_PATH`）。
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

**先看你的工具列表里有没有 `read_image`**，再决定怎么核对：

- **有 `read_image`**：逐页渲染成图片看版式。输出目录用 `<临时目录>` 下一个还不存在的子目录，每次换个新名字（已有东西的目录不渲，免得看到上一次的旧图）：

  ```bash
  python3 office/render.py 文件.docx <临时目录>/第1版   # 打印每页图片的绝对路径，交给 read_image 查看
  ```

  只看几页加 `--pages 1,3`。看完删掉 `<临时目录>`。
- **没有 `read_image`**：不要渲染图片，直接 `python3 office/render.py 文件.docx --text | head -100`
  核对文字、顺序和分页。

## 修改已有文档

老的 `.doc` 要先转：`python3 office/soffice.py --headless --convert-to docx --outdir <临时目录> input/文件.doc`，再从转出来的 `.docx` 改。

```bash
mktemp -d ./.dsh-office.XXXXXXXX                                # 打印出 <临时目录> 的名字
unzip -q input/原文件.docx -d <临时目录>/unpacked/
find <临时目录>/unpacked -type l -delete                         # 去掉符号链接——外来文件不可信
python3 office/merge_runs.py <临时目录>/unpacked/               # 合并碎片，让原文能搜到
# 原地改 <临时目录>/unpacked/word/document.xml——不要重新排版或美化缩进
(cd <临时目录>/unpacked && rm -f ../../原文件名-修改版.docx && zip -Xr ../../原文件名-修改版.docx .)
python3 office/validate.py 原文件名-修改版.docx --original input/原文件.docx
# 用了修订模式？加 --author "你写修订时用的名字"，检查每处改动都带了修订标记
rm -rf <临时目录>
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
# <临时目录>/edits.json：[{"find": "原文里连续的一段", "replace": "新文字"},
#                         {"find": "...", "comment": "批注内容"}]
python3 office/docx_redline.py input/原文件.docx <临时目录>/edits.json 原文件名-修改版.docx
```

`find` 要从原文原样复制并能唯一定位（出现多次时加 `"occurrence": 第几处`）；只改需要改的
字，不要把整段当 `find` 重写——那样修订里全是删除线，用户没法逐条审。用户明确说"直接改好、
不要修订痕迹"时加 `--no-track`。任何一项失败它都不写文件。结构性改动（增删段落、改表格、
换图）还是走上面的解压改 XML。

要得到接受全部修订后的干净稿：`python3 office/accept_changes.py 输入.docx 输出.docx`。

接受一个被删除的段落标记时，这一段应当并入下一段，所以**全部 run 都被删掉**的段落会整段
消失——Word 就是这样做的，`accept_changes.py` 也按这个规则处理。`pandoc --track-changes=accept`
不会合并段落，它读出来的结果里会留下一个空段落，自动编号时看上去像一个空的编号项。这是
那个视图的产物，不是文档的问题；段落删除对不对，看 XML。

## 批注

批注要好几个互相引用的部件一起对上，用脚本建：

```bash
# 对已经解压的目录（同时还要改 document.xml 时优先用这种）
python3 office/comment.py <临时目录>/unpacked/ "付款比例偏高，建议按纪要改为 20%"

# 直接对 .docx
python3 office/comment.py 合同.docx "付款比例偏高" -o 带批注.docx
```

脚本会建好批注部件、关系和内容类型，自动分配编号，然后打印要放进 `word/document.xml` 的
`<w:commentRangeStart>`/`<w:commentRangeEnd>`/`<w:commentReference>` 三段——把它们放到要批注
的文字两边之前，批注虽然存在但看不见。

## 回答里说什么

说清楚改了哪几处（原文 → 新文字，一句话一处），是否为修订模式、在 Word 里可以逐条接受或
拒绝。用 `present` 交付的文件会以文件卡片随回答交给用户，回答里不用再写文件路径。

## 依赖

`docx`（npm）· `markitdown`、`lxml`、`python-docx`（Python）· Office 引擎（经 `office/soffice.py`、`office/render.py` 调用：
云端是沙箱里的原生 LibreOffice，本机是随桌面端带的官方 LibreOffice Kit）；云端对话的沙箱与本机对话的随包运行时都已带好，云端还有 `pandoc`
