#!/bin/sh
# 昇腾 MinerU 镜像的冒烟（deploy/docker/mineru-ascend.Dockerfile）：在构建机上跑，没有 NPU。
# 看三件事：MinerU 是 3.x、模型都在镜像里；mineru-api 起得来，有 KnowWork 先用的异步接口 /tasks；
# 用 CPU 跑 pipeline 解析一页 PDF，拿得到里面的文字。NPU 上的推理（torch_npu、vLLM）这里测不到，要在昇腾机器上看。
# 用法：.github/scripts/mineru-ascend-smoke.sh <镜像>
set -eu

image="${1:?用法：mineru-ascend-smoke.sh <镜像>}"
name="mineru-ascend-smoke-$$"
port=18000
work="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" > /dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() {
  printf '[smoke] 失败：%s\n' "$*" >&2
  docker logs --tail 200 "$name" >&2 2>&1 || true
  exit 1
}

echo "[smoke] 版本与模型"
# 构建机没有 NPU 和驱动：不自动加载 torch_npu
docker run --rm -e TORCH_DEVICE_BACKEND_AUTOLOAD=0 "$image" python3 -c '
import importlib.metadata, json, os
version = importlib.metadata.version("mineru")
print("mineru", version)
assert version.startswith("3."), "KnowWork 只认 MinerU 3.x"
config = json.load(open(os.path.expanduser("~/mineru.json")))
for kind, path in config["models-dir"].items():
    size = sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)
    print(kind, path, round(size / 1e9, 2), "GB")
    assert size > 100_000_000, kind + " 模型是空的"
'

echo "[smoke] 用 CPU 起 mineru-api"
# transformers 只要找得到 torch_npu 就会 import 它（is_torch_npu_available 不看 TORCH_DEVICE_BACKEND_AUTOLOAD），没有驱动的
# libascend_hal.so 就失败；这个一次性的容器里把它挪开，版面、OCR 这些走 CPU。昇腾机器上驱动是挂进来的，照常用 NPU
# shellcheck disable=SC2016  # 单引号里的是容器里的 bash 展开
docker run -d --name "$name" -p "127.0.0.1:$port:8000" \
  -e TORCH_DEVICE_BACKEND_AUTOLOAD=0 -e MINERU_DEVICE_MODE=cpu \
  "$image" bash -c 'mv "$(python3 -c "import importlib.util as u; print(u.find_spec(\"torch_npu\").submodule_search_locations[0])")" /tmp/ \
    && exec mineru-api --host 0.0.0.0 --port 8000' > /dev/null
i=0
until curl -fsS "http://127.0.0.1:$port/health" > /dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -le 60 ] || fail "mineru-api 5 分钟没起来"
  [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || fail "mineru-api 退出了"
  sleep 5
done
curl -fsS "http://127.0.0.1:$port/openapi.json" | grep -q '"/tasks"' || fail "没有 /tasks 接口（KnowWork 先用它）"

echo "[smoke] 解析一页 PDF（pipeline、CPU）"
python3 - "$work/smoke.pdf" << 'EOF'
import sys

text = b"KnowWork smoke test"
content = b"BT /F1 24 Tf 72 720 Td (" + text + b") Tj ET"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
pdf = bytearray(b"%PDF-1.4\n")
offsets = []
for number, body in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
xref = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
open(sys.argv[1], "wb").write(pdf)
EOF
curl -fsS --max-time 900 "http://127.0.0.1:$port/file_parse" \
  -F "files=@$work/smoke.pdf;type=application/pdf" -F backend=pipeline -F parse_method=auto \
  -F lang_list=en -F return_md=true -o "$work/result.json" || fail "解析请求失败"
python3 - "$work/result.json" << 'EOF' || fail "解析结果里没有那句话"
import json, sys

results = json.load(open(sys.argv[1]))["results"]
markdown = "".join(item.get("md_content") or "" for item in results.values())
print(markdown.strip()[:200])
assert "smoke test" in markdown.lower()
EOF
echo "[smoke] 通过"
