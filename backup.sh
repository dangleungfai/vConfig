#!/bin/bash
# 代码备份脚本 - 创建 Git 提交和标签用于一键恢复

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
COMMIT_MSG="备份：功能完整版本 - $TIMESTAMP

已完成功能：
- 全局页脚（当前时间、来访者IP、自定义文案）
- 时间显示时区修复（UTC+Z格式）
- 设备管理与已备份配置列表同步
- 配置文件下载功能
- 删除设备确认流程优化
- SSH连接测试修复
- 添加设备表单顺序调整（主机名、管理IP、设备类型）"

echo "创建提交..."
git commit -m "$COMMIT_MSG"

# 创建备份标签
TAG_NAME="backup-$(date +%Y%m%d-%H%M%S)"
echo "创建备份标签: $TAG_NAME"
git tag -a "$TAG_NAME" -m "代码备份：功能完整版本 - $TIMESTAMP"

echo ""
echo "✅ 备份完成！"
echo "📌 备份标签: $TAG_NAME"
echo ""
echo "恢复方法："
echo "  git checkout $TAG_NAME"
echo ""
echo "查看所有备份标签："
echo "  git tag -l"
