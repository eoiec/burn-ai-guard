#!/usr/bin/env bash
# 一键打包 Android APK（debug 签名，可直接安装）
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/apk"

# 1) 同步 Web 资源
rm -rf www
mkdir -p www
cp -r ../frontend/app/* www/
cat > www/api-default.js <<EOF
// APK 内默认服务地址：同一 WiFi 下运行后端的电脑 IP（可在 App「我的」页修改）
window.__DEFAULT_API__ = "http://10.167.188.32:5000";
EOF
sed -i 's#<script src="/app/app.js"></script>#<script src="api-default.js"></script>\n<script src="app.js"></script>#' www/index.html
sed -i 's#/app/app.css#app.css#g; s#/app/manifest.webmanifest#manifest.webmanifest#g; s#/app/icons/#icons/#g' www/index.html
sed -i 's#"/app/sw.js", { scope: "/app/" }#"sw.js", { scope: "./" }#; s#"/app/index.html"#index.html#; s#/app/icons/#icons/#g; s#"/app/"#"."#g' www/app.js www/sw.js www/manifest.webmanifest
python - <<'EOF'
import json, io, re, os
# manifest 相对化
p = 'www/manifest.webmanifest'
m = json.load(open(p, encoding='utf-8'))
m['start_url'] = './'
m['scope'] = './'
m['id'] = './'
for ic in m['icons']:
    ic['src'] = ic['src'].replace('/app/', './')
for sc in m.get('shortcuts', []):
    sc['url'] = './'
json.dump(m, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
# sw 相对化
p = 'www/sw.js'
s = open(p, encoding='utf-8').read()
s = s.replace('"/app/index.html"', '"./index.html"').replace('"/app/"', '"./"').replace('/app/', './')
open(p, 'w', encoding='utf-8').write(s)
print('www patched')
EOF

# 2) Capacitor 工程
if [ ! -d android ]; then
  npx cap add android
fi
npx cap sync android

# 3) manifest：允许明文 HTTP + 相机权限；Gradle/依赖国内镜像加速
MF=android/app/src/main/AndroidManifest.xml
grep -q usesCleartextTraffic "$MF" || sed -i 's#<application#<application\n        android:usesCleartextTraffic="true"#' "$MF"
grep -q "android.permission.CAMERA" "$MF" || sed -i 's#<uses-permission android:name="android.permission.INTERNET" />#<uses-permission android:name="android.permission.INTERNET" />\n    <uses-permission android:name="android.permission.CAMERA" />#' "$MF"
sed -i 's#services.gradle.org/distributions#mirrors.cloud.tencent.com/gradle#' android/gradle/wrapper/gradle-wrapper.properties
sed -i 's#^        mavenCentral()$#        maven { url '"'"'https://maven.aliyun.com/repository/public'"'"' }\n        mavenCentral()#' android/build.gradle
grep -q "maven.aliyun.com" android/build.gradle || echo "WARN: aliyun mirror not applied"
grep -q "overridePathCheck" android/gradle.properties 2>/dev/null || echo "android.overridePathCheck=true" >> android/gradle.properties

# 4) 图标
python ../tools/patch_android_icons.py

# 5) 构建
export ANDROID_HOME="C:/Users/郑宇航/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
cd android
./gradlew assembleDebug --no-daemon -q 2>&1 | tail -8 || ./gradlew assembleDebug --no-daemon 2>&1 | tail -30
cp app/build/outputs/apk/debug/app-debug.apk ../AI防护-烫伤判读-v1.0.apk
echo "==> APK: $ROOT/apk/AI防护-烫伤判读-v1.0.apk"
ls -la ../AI防护-烫伤判读-v1.0.apk
