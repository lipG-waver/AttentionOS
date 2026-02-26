#!/bin/bash

# 获取脚本所在目录（即项目根目录），无论从哪里调用都能正确定位
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 尝试初始化 conda（按常见安装路径顺序查找，找不到也继续运行）
CONDA_INIT_PATHS=(
    "$HOME/anaconda3/etc/profile.d/conda.sh"
    "$HOME/miniconda3/etc/profile.d/conda.sh"
    "$HOME/miniforge3/etc/profile.d/conda.sh"
    "/usr/local/anaconda3/etc/profile.d/conda.sh"
    "/opt/anaconda3/etc/profile.d/conda.sh"
    "/opt/conda/etc/profile.d/conda.sh"
)

CONDA_FOUND=false
for conda_init in "${CONDA_INIT_PATHS[@]}"; do
    if [ -f "$conda_init" ]; then
        source "$conda_init"
        conda activate base
        CONDA_FOUND=true
        break
    fi
done

if [ "$CONDA_FOUND" = false ]; then
    echo "[提示] 未找到 conda，使用系统 Python 启动"
fi

# 进入项目目录并运行
cd "$SCRIPT_DIR"
python run.py
