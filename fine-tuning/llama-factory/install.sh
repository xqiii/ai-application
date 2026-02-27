#!/bin/bash

# LLaMA-Factory 自动安装脚本
# 注意：此脚本在执行时，所有相对路径都以脚本所在目录为根目录

set -e  # 遇到错误立即退出

echo "=== 开始安装 LLaMA-Factory 环境 ==="

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "当前工作目录: $(pwd)"

# 安装系统级依赖
echo "=== 安装系统级依赖 ==="
apt-get update
apt-get install -y python3-distutils

# 克隆 LLaMA-Factory
echo "=== 克隆 LLaMA-Factory 仓库 ==="
if [ ! -d "LLaMA-Factory" ]; then
    git clone https://gitee.com/hiyouga/LLaMA-Factory.git
else
    echo "LLaMA-Factory 目录已存在，跳过克隆"
fi

# 安装 uv 和创建虚拟环境
echo "=== 安装 uv 和创建虚拟环境 ==="
pip install uv -i https://mirrors.aliyun.com/pypi/simple

if [ ! -d "finetune" ]; then
    uv venv finetune
fi

# 激活虚拟环境并安装依赖
echo "=== 激活虚拟环境并安装依赖 ==="
# 使用 . 命令替代 source，兼容性更好
. finetune/bin/activate

cd LLaMA-Factory
echo "当前目录: $(pwd)"

# 安装 LLaMA-Factory
echo "=== 安装 LLaMA-Factory 及其依赖 ==="
uv pip install -e ".[torch,metrics,bitsandbytes]" -i https://mirrors.aliyun.com/pypi/simple

# 返回脚本目录
cd "$SCRIPT_DIR"

# 验证安装
echo "=== 验证安装 ==="
. finetune/bin/activate
cd LLaMA-Factory

echo "1. 验证 LLaMA-Factory 安装:"
llamafactory-cli version

echo "2. 验证 GPU 是否可用:"
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"