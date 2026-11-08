#!/usr/bin/env bash
# 发布到 GitHub：建仓 -> 推送 main -> 发 v1.0 Release（附 APK）
#
# 本机网络环境特殊（实测）：github.com:443 时通时断（TLS 握手偶发被重置），
# 但 api.github.com、uploads.github.com、github.com:22 均稳定。
# 因此登录改用带重试的 device flow（tools/gh_device_login.py），
# 推送走 HTTPS + 重试循环；若 HTTPS 持续失败，改用 SSH：
#     ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_github -N ""
#     gh api user/keys -f title="burn-ai-guard" -f key="$(cat ~/.ssh/id_ed25519_github.pub)"
#     git remote set-url origin git@github.com:<user>/burn-ai-guard.git

set -e
cd "$(dirname "$0")"
REPO="burn-ai-guard"
TAG="v1.0"
APK_SRC="apk/AI防护-烫伤判读-v1.0.apk"
APK_UPLOAD="apk/burn-ai-guard-v1.0.apk"   # ASCII 文件名：中文名上传后会被 GitHub 抹成 AI.-.-v1.0.apk

# 0) 登录（已登录则跳过）
if ! gh auth status >/dev/null 2>&1; then
    echo "==> 未登录，启动带重试的设备码登录（网络抖动不会作废授权）"
    python tools/gh_device_login.py
    gh auth login --hostname github.com --git-protocol https --with-token < gh_login_token.txt
    rm -f gh_login_token.txt
fi
USER=$(gh api user --jq .login)
echo "==> 已登录：$USER"

# 1) 建仓（已存在则跳过）
if ! gh repo view "$USER/$REPO" >/dev/null 2>&1; then
    gh repo create "$REPO" --public --description "AI 防护 · 家庭热液烫伤深度三分类与低置信度转人工机制（大创项目工程实现）"
fi

# 2) 推送（HTTPS 抖动重试最多 8 次）
git config http.postBuffer 52428800
git config http.lowSpeedLimit 2000
git config http.lowSpeedTime 15
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$USER/$REPO.git"
for i in 1 2 3 4 5 6 7 8; do
    echo "==> push 第 $i 次"
    if GIT_TERMINAL_PROMPT=0 git push -u origin main 2>&1 | tail -3; then
        git ls-remote --exit-code origin main >/dev/null 2>&1 && { echo "==> 推送成功"; break; }
    fi
    sleep 4
done

# 3) Release + APK
gh repo edit --add-topic medical-ai --add-topic burn-classification --add-topic pytorch \
             --add-topic resnet --add-topic temperature-scaling --add-topic human-in-the-loop \
             --add-topic flask >/dev/null 2>&1 || true
if [ -f "$APK_SRC" ]; then
    cp "$APK_SRC" "$APK_UPLOAD"
    gh release view "$TAG" >/dev/null 2>&1 \
        || gh release create "$TAG" --title "$TAG · 家庭热液烫伤深度三分类与低置信度转人工机制" --notes "见 README"
    gh release upload "$TAG" "$APK_UPLOAD" --clobber
fi

echo "==> 仓库：https://github.com/$USER/$REPO"
echo "==> 发布：https://github.com/$USER/$REPO/releases/tag/$TAG"
