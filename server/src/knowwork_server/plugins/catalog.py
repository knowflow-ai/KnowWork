"""预置插件清单：我们审过、随版本发布的 DSH 官方插件。键与宿主那份目录（runtime-host 的 plugin-catalog.mjs）是同一批：
这里决定「有哪些、叫什么、给谁看」，宿主那边决定「怎么装」。两边要一起改，宿主收到不认识的键会拒绝开会话。

预置插件跑在共享宿主里、用官方 Agent 预设按会话挂，只能是只影响单段会话的官方能力；上架前要过的检查见插件方案。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogCommand:
    """插件带的一个 / 命令（宿主那边由插件登记，同一份写在这里）：还没建对话的新对话页靠它列出命令。名字、说明、编号
    照官方插件登记的原样写；建了对话以后以宿主列的为准。"""

    name: str
    description: str
    hint: str | None = None
    definition_id: str | None = None


@dataclass(frozen=True)
class CatalogComponent:
    """插件详情里组件清单的一行：这个预置插件用到的一个官方包。标题、说明照抄官方包 locale/zh.json 的 meta
    （tests/test_plugins.py 对着宿主装的那一版核对，升级 DSH 时跟着改）。"""

    module: str
    title: str
    description: str


@dataclass(frozen=True)
class CatalogPlugin:
    # 小写字母、数字、连字符；存库、进合同。和上传插件的包名共用登记表与个人开关（同一个唯一约束），所以上传时不许用
    # 这些键当包名（uploads.install）。以后新加预置插件，先查空间里有没有已经上传的同名插件
    key: str
    name: str  # 界面上的名字
    description: str  # 一句话：开了以后对话里多出什么
    commands: tuple[CatalogCommand, ...] = ()
    detail: str = ""  # 插件详情里的说明文档（Markdown，我们写的使用说明）
    components: tuple[CatalogComponent, ...] = ()


_GOAL_DETAIL = """\
适合要做很久、一问一答做不完的事，比如把一套制度逐条核对一遍、把一批文件逐个整理。设好目标以后，智能体每做完一轮就自己接着做下一轮。

**怎么用**

- 在输入框打 `/goal`（也可以打 `/目标`），后面写目标和怎样算完成，回车就设好了。这一步不发给模型。
- 也可以直接跟智能体说「把这件事设成目标」，它会自己设。
- 设好以后，输入框上方显示目标和做到第几轮，能暂停、恢复、清除。

**什么时候停**

- 智能体认为目标完成了；
- 连续三轮卡在同一个问题上，它会把目标标成受阻，写明卡在哪；
- 做满 20 轮；
- 你暂停或清除了目标。

每一轮开始前，系统会核对这段对话你还能不能接着用、用量额度够不够，不够就停下并写明原因。对话重新打开或服务重启以后，自动接着做会先停下，点「恢复」接着做。
"""


CATALOG: tuple[CatalogPlugin, ...] = (
    CatalogPlugin(
        key="goal", name="长任务目标",
        description="要做很久的事记成目标、写明怎样算完成，智能体空下来就自己接着做下一轮，直到完成、卡住或做满 20 轮；"
                    "输入框打 /goal 设目标，进度显示在输入框上方",
        # 官方 dsh-command-goal 登记的（宿主的 plugin-catalog.mjs 把它放在同一个预设里）
        commands=(CatalogCommand(name="goal", description="Set or view the goal for a long-running task",
                                 hint="[<objective>|clear|edit <objective>|pause|resume]",
                                 definition_id="@deepseek-ai/dsh-command-goal"),),
        detail=_GOAL_DETAIL,
        # 前两个在这个插件的预设里（开了的会话才有），后两个挂在宿主全局（只管有目标的会话），见 plugin-catalog.mjs
        components=(
            CatalogComponent("@deepseek-ai/dsh-tool-goal", "目标管理工具", "让 AI 设置、查看和更新当前会话目标。"),
            CatalogComponent("@deepseek-ai/dsh-command-goal", "目标管理命令", "通过用户命令设置和管理当前会话目标。"),
            CatalogComponent("@deepseek-ai/dsh-goal", "会话目标服务", "记录会话目标，并管理目标的状态和生命周期。"),
            CatalogComponent("@deepseek-ai/dsh-goal-round-driver", "目标自动续行",
                             "在条件和额度允许时，让 AI 继续推进当前会话的目标。"),
        )),
)


def catalog_keys() -> frozenset[str]:
    return frozenset(plugin.key for plugin in CATALOG)
