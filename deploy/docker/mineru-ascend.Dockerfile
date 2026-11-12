# syntax=docker/dockerfile:1.7
# 昇腾 NPU 上的 MinerU 3.x（文档解析服务，可选部署，编排见 deploy/docker-compose.mineru-ascend.yml）。
# 照 MinerU 官方的昇腾适配（仓库里 docker/china/npu.Dockerfile，3.4.5 版；说明在 docs/archive/platforms/Ascend.md）：
# 底座是昇腾的 vLLM 镜像（CANN、torch_npu、vllm-ascend 都在里面），装 MinerU、把全部模型下进镜像。
# 要求鲲鹏（ARM64）CPU + Atlas A2（910B）NPU；A3、300I Duo 要换底座（v0.11.0-a3、v0.10.0rc1-310p），用到时再加。
#
# 和官方那份的差别：
# - MinerU 钉在 3.4.5（3.x 最后一版）：KnowWork 只认 3.x 的接口，4.0 删了 /tasks、/file_parse；
# - 底座按摘要钉住；入口沿用底座的（先 source CANN 的 set_env.sh 再执行命令），官方那份换掉了它；
# - 默认走 PyPI 与 Hugging Face（发布时在 GitHub 的 ARM 构建机上构建）；国内自己构建时用构建参数换成镜像源与 ModelScope：
#   --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple --build-arg MODEL_SOURCE=modelscope
#
# 构建（ARM64 机器上，不用 NPU；几分钟，镜像解开约 24 GB，其中模型约 5 GB）：docker build -f deploy/docker/mineru-ascend.Dockerfile -t knowwork-mineru-ascend:dev .
ARG BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.11.0@sha256:320f7b34d9a4978ee32cd7b68d7d0fe5e11e60e1e949fd0ca79dcb7fc6104632

# 底座经 ARG 指定，版本钉在 ARG 默认值里；hadolint 看不穿 ARG
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}

# 改了 MinerU 版本，docker-compose.mineru-ascend.yml 里的镜像版本号要跟着改（发布流程核对它以这个版本开头）
ARG MINERU_VERSION=3.4.5
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG MODEL_SOURCE=huggingface

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# opencv 要 libgl；中文字体给版面里的中文
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-cjk fontconfig libgl1 libglib2.0-0 \
    && fc-cache -f \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# numpy、opencv 的版本照官方昇腾那份：底座里的 torch_npu、vllm-ascend 要 numpy 1.x
RUN python3 -m pip install --no-cache-dir -i "${PIP_INDEX_URL}" \
        "mineru[core]==${MINERU_VERSION}" numpy==1.26.4 opencv-python==4.11.0.86

# 模型下进镜像，运行时不联网（MINERU_MODEL_SOURCE=local）。构建机没有 NPU，下载时不加载 torch_npu。
# 和上面的安装分成两层：模型约几 GB，只改依赖时这一层不用重新下
# hadolint ignore=DL3059
RUN TORCH_DEVICE_BACKEND_AUTOLOAD=0 mineru-models-download -s "${MODEL_SOURCE}" -m all

ENV MINERU_MODEL_SOURCE=local \
    MINERU_LMDEPLOY_DEVICE=ascend \
    VLLM_WORKER_MULTIPROC_METHOD=spawn
