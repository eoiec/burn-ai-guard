#!/usr/bin/env bash
# 一键准备 Android 构建环境（命令行工具 + platform + build-tools）
set -e
SDK="C:/Users/郑宇航/android-sdk"
mkdir -p "$SDK"
cd "$SDK"

if [ ! -d "$SDK/cmdline-tools/latest/bin" ]; then
  echo "==> 下载 Android 命令行工具"
  curl -L --retry 3 -o cmdline.zip https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip
  python -c "import zipfile; zipfile.ZipFile('cmdline.zip').extractall('.')"
  mkdir -p cmdline-tools/latest
  cp -r cmdline-tools/bin cmdline-tools/lib cmdline-tools/source.properties cmdline-tools/notice.txt cmdline-tools/latest/ 2>/dev/null || true
  rm -f cmdline.zip
fi

export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
SM="$SDK/cmdline-tools/latest/bin/sdkmanager.bat"
echo "==> 接受许可"
yes | "$SM" --sdk_root="$SDK" --licenses > /dev/null 2>&1 || true
echo "==> 安装 platform-tools / android-34/36 / build-tools"
yes | "$SM" --sdk_root="$SDK" "platform-tools" "platforms;android-34" "build-tools;34.0.0" 2>&1 | tail -5
yes | "$SM" --sdk_root="$SDK" "platforms;android-36" "build-tools;36.0.0" 2>&1 | tail -5
echo "==> 完成"
ls "$SDK"
