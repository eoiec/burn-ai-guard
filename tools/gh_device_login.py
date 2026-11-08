"""带重试的 GitHub Device Flow 登录。

本机到 github.com:443 时通时断（TLS 握手偶发失败），gh 自带的 web 登录
只要有一次网络抖动就整体失败、且已授权的 code 也随之作废。
这里把 device/code 与 access_token 两步都做成"网络错误自动重试、只在
业务错误时退出"，从而在抖动网络下也能把授权走完。

用法：
    python tools/gh_device_login.py            # 输出 CODE=，然后持续轮询
授权成功后把 token 写到 gh_login_token.txt（权限 600），由调用方喂给
`gh auth login --with-token`，token 不会打印到日志。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI 官方 client id（公开值）
SCOPE = "repo read:org gist workflow"
BASE = "https://github.com"

# 直连（不走系统代理：本机代理对 github.com 返回 Bad Gateway）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_json(path: str, data: dict, tries: int = 60, timeout: int = 20) -> dict:
    body = urllib.parse.urlencode(data).encode()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                BASE + path,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "gh-device-login/1.0",
                },
            )
            with _OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 网络层错误：重试
            last = e
            sys.stderr.write(f"[net-retry {i + 1}] {path}: {type(e).__name__}\n")
            sys.stderr.flush()
            time.sleep(3)
    raise SystemExit(f"网络持续失败，放弃：{last}")


def main() -> None:
    dc = post_json("/login/device/code", {"client_id": CLIENT_ID, "scope": SCOPE})
    if "device_code" not in dc:
        raise SystemExit(f"申请设备码失败：{dc}")
    print("CODE=" + dc["user_code"], flush=True)
    print("URL=" + dc.get("verification_uri", "https://github.com/login/device"), flush=True)

    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 900))
    poll = {
        "client_id": CLIENT_ID,
        "device_code": dc["device_code"],
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    while time.time() < deadline:
        time.sleep(interval)
        try:
            res = post_json("/login/oauth/access_token", poll, tries=1, timeout=20)
        except SystemExit:
            # 单次网络错误：继续轮询，不要作废整个流程
            sys.stderr.write("[poll] 网络抖动，继续等待\n")
            sys.stderr.flush()
            continue
        if res.get("access_token"):
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gh_login_token.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(res["access_token"])
            try:
                os.chmod(out, 0o600)
            except OSError:
                pass
            print("OK=1 scope=" + str(res.get("scope")), flush=True)
            return
        err = res.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 2
            continue
        if err in ("expired_token", "access_denied", "incorrect_device_code"):
            raise SystemExit(f"授权失败：{err}")
        sys.stderr.write(f"[poll] {res}\n")
    raise SystemExit("设备码已过期")


if __name__ == "__main__":
    main()
