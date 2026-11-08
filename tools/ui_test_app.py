"""手机端 App 端到端 UI 自测（Playwright / iPhone 15 视口）并输出截图。"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
BASE = "http://127.0.0.1:5000"
SAMPLE = os.path.join(REPORTS, "demo", "sample_full_1.jpg")

errors = []


def shot(page, name, full=True):
    p = os.path.join(REPORTS, name)
    page.screenshot(path=p, full_page=full)
    print("  shot ->", name)


def main():
    with sync_playwright() as pw:
        dev = pw.devices["iPhone 15"]
        browser = pw.chromium.launch()
        ctx = browser.new_context(**dev)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

        print("1) 打开 App 首页")
        page.goto(BASE + "/app", wait_until="networkidle")
        assert page.locator(".hero h2").is_visible(), "首页 Hero 未渲染"
        assert page.locator(".tab").count() == 4, "底部导航应有 4 个标签"
        assert page.locator(".flag").count() == 7, "红旗项应为 7 条"
        shot(page, "app_home.png")

        print("2) 选填信息 + 上传照片")
        page.locator('.chips[data-key="site"] button[data-v="arm_leg"]').click()
        page.locator('.chips[data-key="area_hand"] button[data-v="3"]').click()
        page.locator('.chips[data-key="age_group"] button[data-v="child"]').click()
        page.locator("#fileInput").set_input_files(SAMPLE)
        page.wait_for_selector("#previewCard:not([hidden])", timeout=8000)
        assert not page.locator("#btnAnalyze").is_disabled(), "选图后判读按钮应可用"
        shot(page, "app_preview.png", full=False)

        print("3) 触发 AI 判读")
        page.locator("#btnAnalyze").click()
        page.wait_for_selector(".res-name", timeout=60000)
        name = page.locator(".res-name").inner_text()
        ring = page.locator(".ring-txt").inner_text()
        print(f"   结果：{name} {ring}")
        page.wait_for_timeout(900)
        shot(page, "app_result.png")

        print("4) 记录页")
        page.locator('.tab[data-tab="history"]').click()
        page.wait_for_timeout(400)
        n = page.locator(".hitem").count()
        print(f"   历史记录 {n} 条")
        assert n >= 1, "判读后应写入本机记录"
        shot(page, "app_history.png", full=False)

        print("5) 医生端登录与工单")
        page.locator('.tab[data-tab="doctor"]').click()
        page.wait_for_timeout(300)
        if page.locator("#docLogin").is_visible():
            page.locator("#docUser").fill("doctor")
            page.locator("#docPass").fill("burn2026")
            page.locator("#btnLogin").click()
            page.wait_for_timeout(1500)
        assert page.locator("#docList").is_visible(), "医生端登录后应显示工单列表"
        print(f"   工单 {page.locator('.tk').count()} 条")
        shot(page, "app_doctor.png", full=False)

        print("6) 设置页与模型信息")
        page.locator('.tab[data-tab="me"]').click()
        page.wait_for_timeout(1200)
        shot(page, "app_me.png")

        print("7) 桌面端回归：/app 在窄屏外是否可用")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(BASE + "/app", wait_until="networkidle")
        assert page.locator(".tabbar").is_visible()
        shot(page, "app_wide.png", full=False)

        browser.close()

    print("\n控制台错误：", len(errors))
    for e in errors[:12]:
        print("  ", e)
    if errors:
        sys.exit(1)
    print("✅ App UI 自测全部通过")


if __name__ == "__main__":
    main()
