"""端到端自测：用户判读 -> 转人工 -> 医生复核 -> 回传 -> SUS。"""
import io
import json
import os
import random
import sys
import urllib.request

BASE = "http://127.0.0.1:5000"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def post(path, data=None, token=None, raw=None, content_type=None):
    url = BASE + path
    headers = {}
    if token:
        headers["X-Token"] = token
    if raw is not None:
        headers["Content-Type"] = content_type or "application/octet-stream"
        req = urllib.request.Request(url, raw, headers, method="POST")
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, json.dumps(data).encode(), headers, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get(path, token=None):
    headers = {"X-Token": token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=headers)) as r:
        return json.loads(r.read())


def multipart(path, fields, file_field, file_bytes, filename="w.jpg"):
    b = "----burn" + str(random.randint(1, 10**9))
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    body.write(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
               f'Content-Type: image/jpeg\r\n\r\n'.encode())
    body.write(file_bytes)
    body.write(f'\r\n--{b}--\r\n'.encode())
    req = urllib.request.Request(BASE + path, body.getvalue(), {
        "Content-Type": f"multipart/form-data; boundary={b}"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    assert cond, name


def make_bad_image():
    """模糊+过暗图像 -> 触发质量转人工。"""
    import numpy as np
    from PIL import Image, ImageFilter

    arr = (np.random.default_rng(7).normal(60, 8, (300, 300, 3))).clip(0, 200).astype("uint8")
    img = Image.fromarray(arr).filter(ImageFilter.GaussianBlur(6))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def main():
    h = get("/api/health")
    check("health", h["ok"] and h["model_ready"], h.get("model_error"))

    s = get("/api/samples")
    check("samples", s["ok"] and len(s["samples"]) >= 4, len(s.get("samples", [])))
    good_url = BASE + s["samples"][0]["url"]
    img_bytes = urllib.request.urlopen(good_url).read()

    # --- 正常判读 ---
    a = multipart("/api/analyze", {
        "meta": json.dumps({"age_group": "adult", "site": "limb", "area_hand": "1",
                            "cause": "hot_liquid", "red_flags": []}),
        "device_id": "e2e"}, "image", img_bytes)
    check("analyze ok", a["ok"], a.get("error"))
    print(f"  判读={a['ai']['class_name']} conf={a['ai']['confidence']} 需复核={a['ai']['need_review']} "
          f"质量={a['ai']['quality']['score']} 耗时={a['ai']['latency_ms']}ms")
    cid1 = a["case_id"]

    # --- 红旗症状 -> 建议升级 ---
    a2 = multipart("/api/analyze", {
        "meta": json.dumps({"age_group": "infant", "site": "face_neck", "area_hand": "3",
                            "cause": "hot_liquid", "red_flags": ["breath"]}),
        "device_id": "e2e"}, "image", img_bytes)
    adv = a2["advice"]
    check("红旗升级", adv["level"] == 2 and adv["escalated"], adv["level"])
    check("红旗转人工", a2["ai"]["need_review"] and a2["ai"]["priority"] == "high")

    # --- 低质量图像 -> 质量转人工 ---
    a3 = multipart("/api/analyze", {"meta": "{}", "device_id": "e2e"}, "image", make_bad_image())
    codes = [r["code"] for r in a3["ai"]["review_reasons"]]
    check("低质量转人工", "image_quality" in codes, codes)
    print(f"  质量分={a3['ai']['quality']['score']} flags={a3['ai']['quality']['flags'][:2]}")

    # --- 医生复核流程 ---
    login = post("/api/doctor/login", {"username": "doctor", "password": "burn2026"})
    check("login", login["ok"], login.get("error"))
    tk = login["token"]

    tickets = get("/api/doctor/tickets?status=pending", tk)["tickets"]
    check("tickets", len(tickets) >= 2, len(tickets))
    # 选无红旗症状的质量转人工病例，验证"修正为深二度 -> 尽快就医"
    target = next(t for t in tickets
                  if not ((t.get("user_meta") or {}).get("red_flags")))
    check("claim", post(f"/api/doctor/tickets/{target['id']}/claim", {}, tk)["ok"])
    rv = post(f"/api/doctor/tickets/{target['id']}/review",
              {"decision": "correct", "corrected_class": 1, "note": "基底苍白潮湿，考虑深二度"}, tk)
    check("review", rv["ok"] and rv["final_class"] == 1, rv)

    cd = get(f"/api/case/{target['case_id']}")
    check("回传用户端", cd["case"]["status"] == "reviewed" and cd["case"]["final_class"] == 1)
    check("复核建议=尽快就医", cd["advice"]["label"] == "尽快就医", cd["advice"]["label"])

    try:
        dup = post(f"/api/doctor/tickets/{target['id']}/review", {"decision": "confirm"}, tk)
        check("防重复复核", False, dup)
    except urllib.error.HTTPError as e:
        check("防重复复核", e.code == 409, e.code)

    st = get("/api/doctor/stats", tk)["stats"]
    check("stats", st["tickets_done"] >= 1, st)

    sus = post("/api/sus", {"answers": [5, 1, 5, 1, 4, 2, 5, 1, 5, 2],
                            "completed": True, "task_time_s": 180, "device_id": "e2e"})
    check("sus", sus["ok"] and sus["total"] == 92.5, sus["total"])

    m = get("/api/model")["card"]
    check("model card", m["ready"] and m["calibrated"], m.get("error"))
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
