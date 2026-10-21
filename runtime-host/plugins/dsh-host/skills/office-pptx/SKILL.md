---
name: office-pptx
description: 只要涉及 .pptx / .potx 文件就用——不论它是输入、输出还是两者。包括：做演示稿、汇报 PPT、路演材料、幻灯片；读取、解析或提取任何 .pptx 里的文字（哪怕提取出来是为了写邮件或摘要）；修改、更新已有的演示稿；合并或拆分幻灯片；套用模板、版式，处理演讲者备注或批注。用户说"PPT""幻灯片""演示稿""deck""汇报材料"，或提到某个 .pptx 文件名时，无论之后要拿内容做什么，都加载它。
---

# PPTX：生成、修改与读取

`.pptx` 是一个装着 XML 的压缩包。按任务选做法：

| 任务 | 做法 |
|---|---|
| **生成**新演示稿 | 写一个 `pptxgenjs` 脚本——坑点见下 |
| **修改**已有演示稿，或基于模板制作 | 解压 → 改 `ppt/slides/slideN.xml` → 打包 |
| **读取**内容 | `markitdown 文件.pptx`（每页一段，以 `<!-- Slide number: N -->` 分隔）；看版式：`python3 /opt/knowwork/office/thumbnail.py 文件.pptx 前缀` |

> 用户给的原文件在 `/workspace/input`（只读），中间文件放 `/workspace/tmp`，要交给用户的
> 放 `/workspace/output`，写好后调用 `present` 交付（例如 `present({"files":[{"path":"/workspace/output/演示稿.pptx"}]})`）。

## 脚本

都在 `/opt/knowwork/office/`，其余都是普通的 Python、`node` 或 shell。

| 脚本 | 作用 |
|---|---|
| `thumbnail.py 文件.pptx 前缀` | 把每页渲染成带页码的缩略图网格，用来挑模板版式。只接受 `.pptx`。**一定要传前缀**，用文件名命名它——两份演示稿用同一个前缀，后一份的网格会覆盖前一份 |
| `add_slide.py 目录/ slide2.xml [--after slideN.xml]` | 复制一页（或从 `slideLayoutN.xml` 新建一页），把包里要登记的地方都登记好。也能直接对文件：`-o 输出.pptx` |
| `clean.py 目录/` | 删掉已经没人引用的幻灯片、图片、关系。**`<p:sldIdLst>` 定稿之后**再跑 |
| `validate.py 文件.pptx [--render]` | 检查 XML、关系、内容类型、幻灯片列表；`--render` 再让 LibreOffice 打开一次。每个问题都附修法 |
| `soffice.py --headless --convert-to pdf 文件.pptx` | LibreOffice 的封装——沙箱里直接跑 `soffice` 会卡住 |

## 用 pptxgenjs 生成——坑点

`pptxgenjs` 已经装好，直接 `require('pptxgenjs')`，不要 `npm install`——沙箱没有网络。
API 你是知道的，要当心的是这些：

- **先设 `pres.layout` 再加页。** 默认画布 `LAYOUT_16x9` 是 **10 × 5.625 英寸**，不是 13.3 英寸宽。
  超出边界的坐标照样写进去、不会被截断——形状只是不在页面上。（`LAYOUT_WIDE` 是 13.3 × 7.5 英寸。）
- **颜色：不带 `#`，不要 8 位。** 写 `color: "FF0000"`。`"#FF0000"` 和把透明度写进颜色
  （`"00000020"`）**都会让文件损坏**。要半透明：填充和图片用 `transparency: 0-100`，阴影用
  `opacity: 0.0-1.0`——用错地方会被默默忽略。
- **pptxgenjs 会原地改你传进去的选项对象**（第一次使用时把数值换算成 EMU）。不要让两次
  `add*` 调用共用同一个 `shadow` 或选项对象，每次新建一个。
- **阴影 `offset` 必须 ≥ 0**——负值会让文件损坏。要往上投影，用 `angle: 270` 配正的 offset。
- **`letterSpacing` 会被默默忽略**——真正的选项是 `charSpacing`。
- **列表：** 每项设 `bullet: true`，不要直接写 `•`（会出现两个项目符号）。数组里除最后一项外
  每项都设 `breakLine: true`。列表项之间的距离用 `paraSpaceAfter`，不要用 `lineSpacing`（间距会大得离谱）。
- **一个输出文件一个 `new pptxgen()`**——不要复用实例。
- **`rectRadius` 只对 `ROUNDED_RECTANGLE` 有效**，对 `RECTANGLE` 无效。
- **不支持渐变填充**——要渐变就用一张渐变图片做背景。
- **文本框自带内边距**——文字要和形状、线条、图标在同一个 x 上对齐时，设 `margin: 0`。
- **演讲者备注用 `slide.addNotes("...")`**（纯文本，每页一次），不要放在页面上的文本框里。
- **图表保持原生。** PowerPoint 能画的图一律用 `addChart()`（组合图传 `{type, data, options}` 数组）。
  库没暴露的原生功能（趋势线、误差线）自己算出额外的系列，或者事后改生成的 OOXML——**不要退回
  用图片**。只有 PowerPoint 没有原生形式的图（桑基图、网络图、弦图）才放图片。
- **默认图表很简陋**——没标题、没数据标签、配色过时。设 `showTitle` + `title`、`showValue: true`
  + `dataLabelPosition`、用你配色里的 `chartColors: [...]`，并把框架调淡
  （`catAxisLabelColor`/`valAxisLabelColor`、`valGridLine: { color, size }`、
  `catGridLine: { style: "none" }`，单系列时 `showLegend: false`）。
- **堆积柱状图/条形图的 `dataLabelPosition` 只能是 `ctr`、`inEnd` 或 `inBase`。** `outEnd`
  **会让文件损坏**。
- **组合图里用了 `secondaryValAxis`/`secondaryCatAxis` 的系列，图表选项上必须同时有
  `valAxes` 和 `catAxes`，各两项。** 不写的话 pptxgenjs 会引用它没声明的坐标轴编号，PowerPoint
  **丢掉这张图**并报文件损坏。只给 `valAxes` 不够。
- **上面这几条图表错误，只有 PowerPoint 会拒绝**——LibreOffice 能渲染、python-pptx 能打开、
  我们的 `validate.py` 也查不出来（它只查结构）。所以写图表时逐条对照上面的规则，在生成脚本里
  改对，不要指望事后发现。
- **写完 `writeFile()` 之后跑 `validate.py`。** 报出的问题在生成脚本里修，不要手改打包后的 XML。
- **不要调整 `<p:presentation>` 下子元素的顺序。** pptxgenjs 把 `<p:notesMasterIdLst>` 紧跟在
  `<p:sldIdLst>` 后面写，并让两个母版指向同一个主题部件。PowerPoint 能正常读——挪动这个元素，
  同一份文件就打不开了。
- **图标：** 用 `react-icons` 渲染成 SVG（`ReactDOMServer.renderToStaticMarkup`），用 `sharp`
  栅格化到 ≥256px，再用 `addImage({ data: "image/png;base64," + buf.toString("base64") })` 插入——
  `image/png;base64,` 前缀必须有（`react-icons`、`react`、`react-dom`、`sharp` 都已装好）。

## 修改已有演示稿与模板

先挑版式：`python3 /opt/knowwork/office/thumbnail.py 模板.pptx /workspace/tmp/模板名-thumbs` 生成
一张带页码的网格图，并打印生成了哪些文件——超过 12 页时拆成 `…-thumbs-1.jpg`、`…-thumbs-2.jpg`。
**第二个参数一定要给，用文件名命名。** 它只用于挑版式——检查成品要看"转成图片"一节的全尺寸渲染；
它只接受 `.pptx`，`.potx` 先复制成 `.pptx` 的名字。配合 `markitdown` 把每部分内容对应到模板里的
某一页，并且换着用不同版式——不要每一节都放在同一种"标题 + 要点"页上。

```bash
cd /workspace/tmp
python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall('unpacked')" /workspace/input/原文件.pptx
python3 /opt/knowwork/office/add_slide.py unpacked/ slide2.xml --after slide2.xml   # 复制一页；打印新页路径
# 调整顺序 / 删页 = 改 ppt/presentation.xml 里的 <p:sldIdLst>
python3 /opt/knowwork/office/clean.py unpacked/                                     # 删完之后：清掉孤立的页、图片、关系
# 改页面内容：ppt/slides/slideN.xml
(cd unpacked && rm -f /workspace/output/原文件名.pptx && zip -Xr /workspace/output/原文件名.pptx .)   # 在目录里面打包；先 rm，不然删掉的部件还会留在包里
python3 /opt/knowwork/office/validate.py /workspace/output/原文件名.pptx --render
```

- **所有结构性操作——加页、删页、调顺序——都在改内容之前做完。** `add_slide.py` 原样复制页面文件，
  改完内容再复制就会连内容一起复制；`clean.py` 会删掉所有不在 `<p:sldIdLst>` 里的页，包括你刚写好的那页。
- **不要手工复制页面文件**——`add_slide.py` 会做完一页新幻灯片需要的全部登记，并告诉你它建了什么
  （`Created ppt/slides/slide17.xml from slide2.xml`）。它也能直接处理文件：
  `add_slide.py 文件.pptx slide2.xml -o 输出.pptx`——**记得给 `-o`，否则直接改原文件。** 复制出来的页
  仍然**引用**原页的图表、SmartArt、嵌入对象部件，而不是各自一份——改其中一页的图表，另一页也会变。
- **如果用 `python-pptx`**，它有三件事做不到：复制一页（它唯一的入口是 `add_slide(layout)`）；通过
  `text_frame.text = "..."` 保留格式（那会把整段压成一个没有样式的片段——要给 `run.text` 赋值）；
  读取模板里常见的 SVG/EMF 图（`add_picture` 会抛 `UnidentifiedImageError`）。
- 老的 `.ppt` 要先转：`python3 /opt/knowwork/office/soffice.py --headless --convert-to pptx --outdir /workspace/tmp 文件.ppt`。
  `.potx` 模板解压、打包的方式完全一样——输出保持 `.potx` 扩展名。
- 要复用模板里的图标或图片，复制一页或一个已经含有它的版式。

套模板时：

- 用脚本改 XML 时用 `defusedxml.minidom` 解析——用 `xml.etree.ElementTree` 读写一遍会改写命名空间前缀，
  文件就坏了。
- **模板的格子数 ≠ 你的内容条数。** 模板放了 4 个成员而你只有 3 个，就把第 4 个成员的**整组**
  （图片 + 文本框）删掉，而不只是清空文字——然后在质检时检查有没有残留的孤立图形。
- 一个列表项一个 `<a:p>`——不要把几项拼进同一段。复制相邻段的 `<a:pPr>` 来保留间距；标题、小节
  标题、行内标签（`状态：`、`负责人：`）在 `<a:rPr>` 上加 `b="1"`。
- 项目符号让它从版式继承；只有要覆盖时才加 `<a:buChar>`、`<a:buAutoNum>`（编号）或 `<a:buNone>`——
  不要把 `•` 写进文字里。
- 文字前后有空格时，`<a:t>` 上要加 `xml:space="preserve"`。

## 设计

**不要做无聊的幻灯片。** 白底加一排项目符号打动不了任何人。每一页都从下面的思路里挑一些用。

### 开始之前

- **选一套大胆、贴合内容的配色**：配色要像是为**这个主题**设计的。把你的颜色换到一份完全不同的
  演示稿里也照样"成立"，说明你选得还不够具体。
- **主次分明，不要平均**：一个颜色占主导（60–70% 的视觉比重），配 1–2 个辅助色和一个醒目的强调色。
  不要让所有颜色分量相同。
- **深浅对比**：标题页和结尾页用深色背景，内容页用浅色（"三明治"结构）；或者全程深色，显得高级。
- **定一个视觉母题**：选**一个**有辨识度的元素反复使用——圆角图片框、彩色圆圈里的图标——贯穿每一页。
  **不要拿色条或强调条当母题**（见下面"避免"）。

### 配色参考

按主题选色——不要默认用通用的蓝色。下面这些可以作为灵感：

| 主题 | 主色 | 辅助色 | 强调色 |
|------|------|--------|--------|
| **午夜商务** | `1E2761`（藏青） | `CADCFC`（冰蓝） | `FFFFFF`（白） |
| **森林苔藓** | `2C5F2D`（森林绿） | `97BC62`（苔绿） | `F5F5F5`（乳白） |
| **珊瑚活力** | `F96167`（珊瑚） | `F9E795`（金） | `2F3C7E`（藏青） |
| **陶土暖调** | `B85042`（陶土） | `E7E8D1`（沙） | `A7BEAE`（鼠尾草） |
| **海洋渐层** | `065A82`（深蓝） | `1C7293`（青蓝） | `21295C`（午夜蓝） |
| **炭灰极简** | `36454F`（炭灰） | `F2F2F2`（米白） | `212121`（黑） |
| **青绿信赖** | `028090`（青绿） | `00A896`（海沫绿） | `02C39A`（薄荷） |
| **莓果奶油** | `6D2E46`（莓红） | `A26769`（灰玫瑰） | `ECE2D0`（奶油） |
| **鼠尾草静谧** | `84B59F`（鼠尾草） | `69A297`（桉叶） | `50808E`（石板蓝） |
| **樱桃醒目** | `990011`（樱桃红） | `FCF6F5`（米白） | `2F3C7E`（藏青） |

### 每一页

**每一页都要有一个视觉元素**——图片、图表、图标或形状。纯文字页让人记不住。

**版式选择：**
- 两栏（左文字、右插图）
- 图标 + 文字行（彩色圆圈里的图标、加粗小标题、下面一行说明）
- 2×2 或 2×3 网格（一侧图片，另一侧内容块网格）
- 半出血图片（占满左侧或右侧）叠加内容

**数据展示：**
- 大号数字（60–72pt 的数字，下面配小标签）
- 对比栏（前后对比、优缺点、并列选项）
- 时间线或流程（编号步骤、箭头）

**视觉细节：**
- 小节标题旁放小的彩色圆圈图标
- 关键数字或标语用斜体强调

### 字体

**你写进 .pptx 的字体名，是由用户的 PowerPoint 渲染的，不是这个环境。** 质检用 LibreOffice 渲染，
它会替换自己没有的字体——有些替代字体宽度不同，于是质检预览里的溢出（或不溢出）在真正的演示稿里
并不存在。为了让质检可信：

- **安全字体**（质检里宽度准确，Office 也自带）：**Arial、Calibri、Cambria、Times New Roman、
  Courier New**。正文和所有需要保证放得下的地方都用这些。
- **想让标题有个性又不冒质检风险**：安全字体里的衬线字体（Cambria）做标题，配无衬线正文
  （Calibri 或 Arial）。有对比，又不失去可靠的溢出检查。
- **用户指定了安全字体以外的字体**（如 Georgia、Trebuchet MS）：用在用户要求的地方，但给那些文本框
  多留约 10% 的余量，并且不要相信质检里这些元素的"放得下"——预览只是近似。用户没指定时，正文优先用安全字体。
- **质检不可靠的字体**（替代字体宽度不同，溢出判断可能是错的）：Georgia、Trebuchet MS、Impact、
  Arial Black、Garamond、Consolas、Palatino Linotype、Calibri Light。用于标题、点缀可以，但要留余量，
  不要相信质检的放得下。
- **不要默认用 Aptos**——这里没有与它等宽的替代字体，老版本 Office 里也没有它，两头都不可靠。
- **中文字体：** 写 `微软雅黑`（Windows 与新版 Office 都有）。质检环境没有它，用思源黑体
  （Noto Sans CJK）替代，宽度相近但不完全一致——中文文本框同样留约 10% 余量。

| 元素 | 字号 |
|------|------|
| 页面标题 | 36–44pt 加粗 |
| 小节标题 | 20–24pt 加粗 |
| 正文 | 14–16pt |
| 图注 | 10–12pt 浅色 |

### 间距

- 页边距至少 0.5 英寸
- 内容块之间 0.3–0.5 英寸
- 留白——不要把每一寸都塞满

### 避免（常见错误）

- **不要重复同一种版式**——在各页之间换着用分栏、卡片、大数字
- **不要居中正文**——段落和列表左对齐，只有标题居中
- **字号对比要够**——标题要 36pt 以上，才能从 14–16pt 的正文里跳出来
- **不要默认用蓝色**——选能反映具体主题的颜色
- **间距不要随意混用**——选定 0.3 或 0.5 英寸，全程一致
- **不要只精心设计一页、其余草草了事**——要么全部用心，要么全部保持简洁
- **不要做纯文字页**——加图片、图标、图表或其他视觉元素；避免"标题 + 要点"
- **别忘了文本框内边距**——把线条或形状与文字边缘对齐时，文本框设 `margin: 0`，或者把形状挪开补偿内边距
- **不要用低对比元素**——图标和文字都要和背景有足够对比；避免浅底浅字、深底深字
- **绝不要在标题下面加强调线**——这是 AI 生成幻灯片的典型特征；用留白或背景色代替
- **绝不要加装饰性色条或强调条**——包括：横跨页面的页眉/页脚色条、沿页面一侧的竖条、沿卡片或内容块
  一边的细强调条、矩形上的"单边边框"。这些看起来就是 AI 凑出来的。要把一张卡片区分出来，用淡淡的
  背景色、投影或图标——不要用边条。
- **不要默认用奶油色/米色背景**——没有指定背景时用白色（`FFFFFF`）或用户的品牌色；避免
  `F5F5DC`、`FAF0E6`、`FAEBD7`、`FFF8E1` 这类暖中性色
- **不要交付文字溢出形状的页面**——放不下就减小字号、拆成两页或放大容器；绝不留下被截断或溢出边界的内容

## 质检（必做）

第一次渲染通常会有几处真问题——重叠、溢出、没对齐。找出来修掉，只重新渲染改过的页，然后停下。

### 内容质检

```bash
markitdown /workspace/output/文件.pptx
```

检查缺内容、错别字、顺序错误。

**用了模板时，检查有没有残留的占位文字：**

```bash
markitdown /workspace/output/文件.pptx | grep -iE "\bx{3,}\b|lorem|ipsum|\bTODO|\[insert|单击此处|点击添加|此处输入"
```

有输出就先修掉，再宣布完成。

### 文件质检（必做）

```bash
python3 /opt/knowwork/office/validate.py /workspace/output/文件.pptx --render
```

每个问题都附了修法。在生成脚本里修好再重新生成。它不查图表 XML——图表对照上面 pptxgenjs 坑点里的图表规则。

### 视觉质检

把每一页转成图片（见"转成图片"）并逐页查看。盯着生成代码看久了，你看到的往往是你以为会渲染出来
的样子，而不是真正渲染出来的样子——所以要用新鲜的眼光看图片。用户会看到的缺陷：

- **文字溢出，或在文本框、页面边界被截断——最先查这一条。** 它最常见，而且用户一定看得到。
  （对于"字体"一节里说预览不可靠的字体，预览只是近似：相信你留的约 10% 余量，不要相信预览里的放得下。）
- 元素重叠（文字穿过形状、线条穿过文字、元素叠在一起）
- 来源注释或页脚撞到上面的内容
- 元素挨得太近（间距 < 0.3 英寸），卡片或区块几乎贴在一起
- 间距不均（一处大片空白、另一处拥挤）
- 离页面边缘太近（< 0.5 英寸）
- 分栏或同类元素没有对齐
- 低对比文字（例如米色背景上的浅灰字）
- 替换文字后模板装饰错位——例如标题下划线按一行标题摆放，替换后的标题却折成了两行
- 低对比图标（例如深色背景上的深色图标，没有加对比色圆圈）
- 文本框太窄，导致过度换行
- 残留的占位内容

## 转成图片

**先看你的工具列表里有没有 `read_image`。** 有它才能看图，上面那些版式问题只能看图发现。

先转成 PDF：

```bash
cd /workspace/tmp
python3 /opt/knowwork/office/soffice.py --headless --convert-to pdf --outdir /workspace/tmp /workspace/output/文件.pptx
```

- **有 `read_image`**：逐页转成图片，把打印出的绝对路径交给 `read_image` 查看。

  ```bash
  rm -f slide-*.jpg
  pdftoppm -jpeg -r 150 /workspace/tmp/文件.pdf slide
  ls -1 "$PWD"/slide-*.jpg
  ```

  `rm` 用来清掉上一次的旧图。`pdftoppm` 按页数补零：不到 10 页是 `slide-1.jpg`，10–99 页是 `slide-01.jpg`，
  100 页以上是 `slide-001.jpg`。
- **没有 `read_image`**：不要转图片，用 `pdftotext -layout /workspace/tmp/文件.pdf -` 核对每页的文字、顺序和
  有没有残留占位内容。重叠、对齐、对比度这类版式问题看文字发现不了，回答里如实说明只核对了文字。

**修完之后从改过的 `.pptx` 重新生成 PDF 再核对**，否则看到的还是旧版。

## 回答里说什么

说清楚做了几页、每页讲什么，或者改了哪几页、改了什么。不要写 `/workspace` 路径，用 `present` 交付的文件
会作为附件随回答交给用户。

## 依赖

`pptxgenjs`、`react-icons`、`react`、`react-dom`、`sharp`（npm，已装）· `markitdown[pptx]`、`Pillow`、
`defusedxml`、`lxml`、`python-pptx`（Python，已装——读取、缩略图、清理、校验）· LibreOffice（通过
`/opt/knowwork/office/soffice.py` 调用）· `pdftoppm`（Poppler）
