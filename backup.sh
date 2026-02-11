#!/bin/bash
# vConfig 代码备份脚本 - 创建 Git 提交和标签，用于 RESTORE.md 一键恢复

set -e

cd "$(dirname "$0")"

# 检查是否已初始化 Git
if [ ! -d .git ]; then
    echo "初始化 Git 仓库..."
    git init
    git config user.name "Backup Script"
    git config user.email "backup@local"
fi

# 添加所有文件
echo "添加文件到 Git..."
git add -A

# 检查是否有变更
if git diff --cached --quiet && git diff --quiet; then
    echo "没有变更需要提交"
    exit 0
fi

# 创建提交
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
COMMIT_MSG="vConfig 代码备份 - $TIMESTAMP"

echo "创建提交..."
git commit -m "$COMMIT_MSG"

# 创建备份标签
TAG_NAME="backup-$(date +%Y%m%d-%H%M%S)"
echo "创建备份标签: $TAG_NAME"
git tag -a "$TAG_NAME" -m "vConfig 代码备份 - $TIMESTAMP"

echo ""
echo "✅ 备份完成！"
echo "📌 备份标签: $TAG_NAME"
echo ""
echo "恢复方法："
echo "  git checkout $TAG_NAME"
echo ""
echo "查看所有备份标签："
echo "  git tag -l"
