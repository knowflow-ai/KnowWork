"""在沙箱里运行的 POSIX sh 片段（busybox ash 与 dash 都能跑）。参数一律经位置参数传入（$1、$2…），不拼进脚本文本。

活动标记（ACTIVITY_MARKER）：Docker 没有 exec 时间戳、label 创建后不可改，所以每次 exec 顺手
touch 一个容器里的文件，空闲清扫读它的 mtime。它放在 /workspace 与 /tmp 之外，免得被当成数据或被 tmpfs 盖住。
"""

ACTIVITY_MARKER = "/var/lib/knowwork-sandbox-activity"

# 容器的 PID 1（前面有 Docker 的 init，即 tini，负责收僵尸进程）：建好活动标记与工作区目录后一直睡着。
# 工作区约定（Office 技能照此写）：input 是附件（只读）、tmp 放中间文件、output 放要交付的文件（交付靠 present）。
# KW_SESSION_ROOT（/sessions/<会话 id>）链接到 /workspace：宿主按这个路径认会话，命令里的绝对路径也能用。
_ENTRY = (f"touch {ACTIVITY_MARKER} 2>/dev/null; chmod 666 {ACTIVITY_MARKER} 2>/dev/null; "
          "mkdir -p /workspace/input /workspace/tmp /workspace/output 2>/dev/null; "
          'if [ -n "$KW_SESSION_ROOT" ]; then mkdir -p "$(dirname "$KW_SESSION_ROOT")" && '
          'ln -sfn /workspace "$KW_SESSION_ROOT"; fi; exec sleep infinity')
ENTRYPOINT = ["/bin/sh", "-c", _ENTRY]

# 流式执行（SSH 执行环境）用的几样东西，镜像见 deploy/docker/sandbox.Dockerfile：
# - 主机密钥在容器启动时现生成：镜像里不带私钥（开源镜像是公开的），每个容器各一把；
# - sshd 只以 `sshd -i` 方式读写 docker exec 的管道，不监听端口；
# - 登录用公钥：宿主为每个执行环境现生成一对，业务服务连接前把公钥写进 AUTHORIZED_KEYS_DIR/<账号>。
SSH_HOST_KEY = "/etc/ssh/ssh_host_ed25519_key"
SSHD_CONFIG = "/etc/ssh/sshd_config_kw"
SSHD_ARGV = ("/usr/sbin/sshd", "-i", "-f", SSHD_CONFIG)
AUTHORIZED_KEYS_DIR = "/etc/ssh/kw_authorized_keys"
ENTRYPOINT_SSH = ["/bin/sh", "-c",
                  (f'if command -v ssh-keygen >/dev/null 2>&1 && [ ! -s {SSH_HOST_KEY} ]; then '
                   f'ssh-keygen -q -t ed25519 -N "" -f {SSH_HOST_KEY} >/dev/null 2>&1; fi; ') + _ENTRY]

# 输出两行：登录账号名（执行账号的 uid 或 root 换成名字，与 exec 用同一个账号，文件属主才一致）、主机公钥。
# 容器刚建好时入口还在生成主机密钥，镜像带 sshd 就最多等 5 秒，免得把「还没生成完」当成「镜像不支持」。
# 退出码 3：镜像不带 sshd 或主机密钥；4：执行账号在镜像里没有名字
SSH_IDENTITY = rf'''
[ -x {SSHD_ARGV[0]} ] && [ -r {SSHD_CONFIG} ] || exit 3
i=0
while [ ! -s {SSH_HOST_KEY}.pub ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
[ -r {SSH_HOST_KEY}.pub ] || exit 3
name=$(getent passwd "$1" | cut -d: -f1)
[ -n "$name" ] || exit 4
printf '%s\n' "$name"
cut -d' ' -f1-2 {SSH_HOST_KEY}.pub
'''

# 写本次连接的公钥（先写临时文件再改名，读的一方不会看到半个文件）；目录与文件 root 所有，sshd 的 StrictModes 才认
SSH_AUTHORIZE = rf'''
umask 022
mkdir -p {AUTHORIZED_KEYS_DIR} || exit 6
printf '%s\n' "$2" > "{AUTHORIZED_KEYS_DIR}/.$1.tmp" && mv -f "{AUTHORIZED_KEYS_DIR}/.$1.tmp" "{AUTHORIZED_KEYS_DIR}/$1" || exit 6
'''

# 包装每一条命令：
# - touch 活动标记；
# - 命令放进独立进程组运行，组号写进 /tmp/.kw-exec-<令牌>，超时或取消时由 KILL_GROUP 整组杀掉；
# - 容器内看门狗（同样单独成组）在 $2 秒后整组 SIGKILL：业务服务中途退出、没人来杀时也停得下来
#   （不用容器内 timeout(1)：它只杀直接子进程，后台子进程会留下）。
EXEC_WRAPPER = rf'''
t="$1"; n="$2"; shift 2
touch {ACTIVITY_MARKER} 2>/dev/null || true
f="/tmp/.kw-exec-$t"; w=""
if command -v setsid >/dev/null 2>&1; then
  setsid "$@" &
  p=$!
  setsid sh -c 'sleep "$1"; kill -KILL -"$2" 2>/dev/null' kw-watchdog "$n" "$p" </dev/null >/dev/null 2>&1 &
  w=$!
else
  "$@" &
  p=$!
fi
echo "$p" > "$f"
wait "$p"; s=$?
[ -n "$w" ] && kill -KILL -"$w" 2>/dev/null
rm -f "$f"
exit "$s"
'''

KILL_GROUP = r'''
f="/tmp/.kw-exec-$1"; i=0
while [ ! -s "$f" ] && [ "$i" -lt 20 ]; do sleep 0.1; i=$((i + 1)); done
p=$(cat "$f" 2>/dev/null) || exit 0
kill -KILL -"$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null
rm -f "$f"
exit 0
'''

# 输出：每项三段（类型 f/d/l/o、字节数、名字），以 NUL 分隔
LIST_DIR = r'''
cd "$1" 2>/dev/null || exit 3
for f in * .[!.]* ..?*; do
  if [ -L "$f" ]; then t=l; s=0
  elif [ -d "$f" ]; then t=d; s=0
  elif [ -f "$f" ]; then t=f; s=$(stat -c %s -- "$f" 2>/dev/null) || s=0
  elif [ -e "$f" ]; then t=o; s=0
  else continue
  fi
  printf '%s\0%s\0%s\0' "$t" "$s" "$f"
done
'''

# 输出：第一行「字节数|修改时间（纳秒）|inode」（同 STAT_PATH，版本号一致），之后是从 offset 起最多 n 字节。
# 读的同时给出版本：宿主读文件不用先 stat，编辑时用它核对版本，少一次来回
READ_FILE = r'''
p="$1"
[ -e "$p" ] || exit 3
[ -d "$p" ] && exit 4
[ -f "$p" ] || exit 5
[ -r "$p" ] || exit 7
stat -L -c '%s|%.9Y|%i' -- "$p" 2>/dev/null || stat -L -c '%s|%Y|%i' -- "$p" || exit 6
tail -c +"$(($2 + 1))" -- "$p" | head -c "$3"
'''

# 交付的文件只收普通文件，最后一级是符号链接的不收（不跟随最后一级）
READ_DELIVERED = r'''
p="$1"
{ [ -f "$p" ] && [ ! -L "$p" ]; } || exit 3
s=$(stat -c %s -- "$p") || exit 6
[ "$s" -le "$2" ] || exit 9
cat -- "$p"
'''

# Office 格式转换：转 PDF（预览用），或把旧格式 Word 转成 docx（知识库按 docx 解析、在浏览器里渲染）。
# $1 私有临时目录（里面已放好 in.<扩展名>），$2 原生 LibreOffice 的封装脚本（/opt/knowwork/office/soffice.py：
# 每次独立的配置目录，并发不抢配置锁），$3 原扩展名，$4 结果字节上限，$5 目标格式（pdf、docx，和原扩展名不同）。
# 成功时 stdout 只有结果文件的字节；失败时转换日志写 stderr。无论成败都删掉临时目录。
# 退出码：9 结果超限，10 转换失败，11 转换进程被强制终止（多半是内存不够）。
CONVERT_OFFICE = r'''
d="$1"
trap 'rm -rf "$d"' EXIT
python3 "$2" --headless --convert-to "$5" --outdir "$d" "$d/in.$3" >"$d/log" 2>&1; c=$?
# 被强制终止（多半是容器内存不够）：sh 看到的是 137，封装脚本转述 soffice 被杀时是 247（-9）
[ "$c" -eq 137 ] || [ "$c" -eq 247 ] && { tail -c 2000 "$d/log" >&2; exit 11; }
[ "$c" -eq 0 ] || { tail -c 2000 "$d/log" >&2; exit 10; }
[ -f "$d/in.$5" ] || { tail -c 2000 "$d/log" >&2; exit 10; }
s=$(stat -c %s -- "$d/in.$5") || exit 10
[ "$s" -le "$4" ] || exit 9
cat -- "$d/in.$5"
'''

# 直接提取文字（会话附件用，不经版面模型）：有文字层的 PDF 用 pdftotext（-layout 保留表格的列对齐、页与页之间是换页符），
# docx 用 pandoc 转 Markdown（标题、列表、表格都在，正文不要内嵌 HTML），pptx 用 markitdown。$1 私有临时目录（里面已放好
# in.<扩展名>），$2 扩展名，$3 结果字节上限。成功时 stdout 只有提取出的文字（UTF-8）；失败时日志写 stderr。无论成败都删掉临时目录。
# 退出码：9 结果超限，10 提取失败（打不开、损坏、加密），11 进程被强制终止（多半是内存不够），12 不支持这种扩展名。
#
# docx 的表格：Markdown 表格只表达得了「每格一段、没有合并」的表。别的（合并单元格、一格多段或列表、格内换行、表中表）
# 关了内嵌 HTML 时 pandoc 只留一行 [TABLE]，内容整张丢掉，合并单元格的表则只在第一格写一次、合并关系丢了（合同、方案里
# 很常见）。由 tables.lua 把这些表渲染成 HTML 表格原样放进去（保留 rowspan、colspan），规整的表仍是 Markdown 表格、正文
# 照旧不出 HTML；再去掉 HTML 里的 colgroup 与 class、style，少占模型的字数。
EXTRACT_TEXT = r'''
d="$1"
trap 'rm -rf "$d"' EXIT
case "$2" in
  pdf) pdftotext -layout -enc UTF-8 "$d/in.pdf" "$d/out" ;;
  docx) cat >"$d/tables.lua" <<'LUA'
local function plain(cell)
  local blocks = cell.contents
  if #blocks == 0 then return true end
  if #blocks > 1 or (blocks[1].t ~= 'Plain' and blocks[1].t ~= 'Para') then return false end
  local broken = false
  blocks[1]:walk({ LineBreak = function() broken = true end })
  return not broken
end
local function simple(rows)
  for _, row in ipairs(rows) do
    for _, cell in ipairs(row.cells) do
      if cell.row_span > 1 or cell.col_span > 1 or not plain(cell) then return false end
    end
  end
  return true
end
function Table(t)
  local ok = simple(t.head.rows) and simple(t.foot.rows)
  for _, body in ipairs(t.bodies) do ok = ok and simple(body.head) and simple(body.body) end
  if not ok then
    return pandoc.RawBlock('gfm', pandoc.write(pandoc.Pandoc({t}), 'html', {wrap_text = 'wrap-none'}))
  end
end
LUA
    pandoc -f docx -t gfm-raw_html --wrap=none --lua-filter "$d/tables.lua" -o "$d/md" "$d/in.docx" \
      && sed -E -e '/^<colgroup>$/,/^<\/colgroup>$/d' -e ':a' -e 's/(<[a-z][^>]*) (class|style)="[^"]*"/\1/' \
           -e 'ta' "$d/md" >"$d/out" ;;
  pptx) markitdown "$d/in.pptx" >"$d/out" ;;
  *) exit 12 ;;
esac 2>"$d/log"; c=$?
[ "$c" -eq 137 ] && { tail -c 2000 "$d/log" >&2; exit 11; }
[ "$c" -eq 0 ] || { tail -c 2000 "$d/log" >&2; exit 10; }
s=$(stat -c %s -- "$d/out") || exit 10
[ "$s" -le "$3" ] || exit 9
cat -- "$d/out"
'''

# 重算 Excel 公式（知识库解析表格前用）：$1 私有临时目录（里面已放好 in.xlsx），$2 重算脚本
# （/opt/knowwork/office/recalc.py：经 LibreOffice 重算后原地写回，stdout 是 JSON 报告），$3 超时秒数，$4 字节上限。
# 成功时 stdout 只有重算后的 xlsx；报告里没有 status（打不开、有外部链接）算失败，报告与日志写 stderr。
# 退出码：9 结果超限，10 重算失败，11 进程被强制终止（多半是内存不够）。
RECALC_XLSX = r'''
d="$1"
trap 'rm -rf "$d"' EXIT
python3 "$2" "$d/in.xlsx" "$3" >"$d/report" 2>"$d/log"; c=$?
[ "$c" -eq 137 ] || [ "$c" -eq 247 ] && { tail -c 2000 "$d/log" >&2; exit 11; }
grep -q '"status"' "$d/report" || { cat "$d/report" "$d/log" | tail -c 2000 >&2; exit 10; }
s=$(stat -c %s -- "$d/in.xlsx") || exit 10
[ "$s" -le "$4" ] || exit 9
cat -- "$d/in.xlsx"
'''

# 写文件前的准备：目标不能是目录，建好上级目录，并确认执行账号对上级目录有写权限
PREPARE_WRITE = r'''
t="$1"; d=$(dirname -- "$t")
[ -d "$t" ] && exit 4
mkdir -p -- "$d" 2>/dev/null || exit 6
[ -w "$d" ] || exit 6
exit 0
'''

# 删一个文件（官方 apply_patch 的删除与移动）：不存在退出 3，是目录退出 4，不是普通文件退出 5，删不掉退出 6。
# 指向文件的符号链接删的是链接本身（容器里不跟随到工作区外）
DELETE_FILE = r'''
t="$1"
{ [ -e "$t" ] || [ -L "$t" ]; } || exit 3
[ -d "$t" ] && [ ! -L "$t" ] && exit 4
{ [ -f "$t" ] || [ -L "$t" ]; } || exit 5
rm -f -- "$t" 2>/dev/null || exit 6
{ [ -e "$t" ] || [ -L "$t" ]; } && exit 6
exit 0
'''

# 路径元数据：一行「类型|字节数|修改时间（纳秒）|inode」；$2 为 1 时跟随最后一级符号链接。不存在退出 3
# busybox 的 stat 不认 %.9Y，退回到秒（版本号照样带 inode 与字节数）
STAT_PATH = r'''
if [ "$2" = 1 ]; then [ -e "$1" ] || exit 3; f=-L; else { [ -e "$1" ] || [ -L "$1" ]; } || exit 3; f=; fi
stat $f -c '%F|%s|%.9Y|%i' -- "$1" 2>/dev/null || stat $f -c '%F|%s|%Y|%i' -- "$1"
'''

# 退出码 → 面向用户的说明
FILE_ERRORS = {
    3: "文件不存在",
    4: "该路径是目录",
    5: "不是普通文件",
    6: "没有权限或路径不可写",
    7: "没有读取权限",
    9: "文件超出大小上限",
}
