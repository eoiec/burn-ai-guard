#!/usr/bin/env bash
# 登录 gh 之后执行：建仓 -> 推送 -> 发 Release（附 APK）
set -e
REPO_NAME="${1:-burn-ai-guard}"
APK="apk/AI防护-烫伤判读-v1.0.apk"

gh auth status >/dev/null || { echo "请先执行 gh auth login"; exit 1; }
USER=$(gh api user --jq .login)
echo "==> 账号：$USER"

# 1) 建仓（已存在则跳过）
if gh repo view "$USER/$REPO_NAME" >/dev/null 2>&1; then
  echo "==> 仓库已存在：$USER/$REPO_NAME"
else
  gh repo create "$REPO_NAME" --public \
    --description "AI 防护 · 家庭热液烫伤深度三分类与低置信度转人工机制（Flask + PyTorch + PWA/APK）" \
    --source . --remote origin --push
  echo "==> 已创建并推送"
fi

# 2) 确保 remote 与推送
git remote get-url origin >/dev/null 2>&1 || \
  git remote add origin "https://github.com/$USER/$REPO_NAME.git"
git branch --show-current | grep -q main || git branch -M main
git push -u origin main

# 3) Release 附 APK
if [ -f "$APK" ]; then
  TAG="v1.0"
  if gh release view "$TAG" >/dev/null 2>&1; then
    gh release upload "$TAG" "$APK" --clobber
  else
    gh release create "$TAG" "$APK" \
      --title "AI 防护 v1.0 · 演示版" \
      --notes "Android 安装包（debug 签名，直接安装）。安装后需与运行后端的电脑连同一 WiFi，在 App「我的」页填写电脑局域网地址。

> 演示模型由合成创面图像训练，不具临床效力。" || true
  fi
  echo "==> Release: https://github.com/$USER/$REPO_NAME/releases/tag/$TAG"
fi

echo "==> 仓库：https://github.com/$USER/$REPO_NAME"
