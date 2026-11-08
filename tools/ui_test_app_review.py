"""App 内「低质量照片 → 转人工 → 医生复核 → 用户端更新」闭环自测。"""
import os

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:5000"
BLUR = os.path.join(ROOT, "reports", "demo", "_blur.jpg")

errors = []


def main():
    with sync_playwright() as pw:
        dev = pw.devices["iPhone 15"]
        browser = pw.chromium.launch()
        ctx = browser.new_context(**dev)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        print("1) 上传模糊照片（图像质量差 → 应转人工）")
        page.goto(BASE + "/app", wait_until="networkidle")
        page.locator("#fileInput").set_input_files(BLUR)
        page.wait_for_selector("#previewCard:not([hidden])")
        page.locator("#btnAnalyze").click()
        page.wait_for_selector(".res-name", timeout=60000)
        page.wait_for_selector("#reviewBanner", timeout=10000)
        banner = page.locator("#reviewBanner").inner_text()
        assert "转烧伤科医生复核" in banner, banner
        print("   已转人工 ✓ ->", banner.splitlines()[1][:40])
        case_id = page.locator("#resultBody .kv .v").first.inner_text()
        print("   病例号:", case_id)

        print("2) 同一 App 内切医生端复核")
        page.locator('.tab[data-tab="doctor"]').click()
        if page.locator("#docLogin").is_visible():
            page.locator("#docUser").fill("doctor")
            page.locator("#docPass").fill("burn2026")
            page.locator("#btnLogin").click()
            page.wait_for_selector("#docList:not([hidden])")
        page.locator('.tabs-mini button[data-st="pending"]').click()
        page.wait_for_timeout(900)
        cnt = page.locator(".tk").count()
        assert cnt >= 1, "应出现待复核工单"
        print(f"   待复核工单 {cnt} 条")
        page.locator(".tk").first.click()
        page.wait_for_selector("#btnClaim", timeout=8000)
        page.locator("#btnClaim").click()
        page.wait_for_selector("#btnSubmit:not([disabled])", timeout=8000)
        page.locator("#rvNote").fill("图像不清且创面边界不明，建议清洗后重新拍照并到烧伤科面诊")
        page.locator('.radio[data-v="correct1"]').click()
        page.locator("#btnSubmit").click()
        page.wait_for_timeout(1200)
        print("   已提交复核（修正为深二度）✓")

        print("3) 回到记录页打开该病例，确认复核结论已回传")
        page.locator('.tab[data-tab="history"]').click()
        page.wait_for_timeout(500)
        page.locator(f'.hitem[data-id="{case_id}"]').click()
        page.wait_for_selector(".res-name", timeout=8000)
        page.wait_for_selector("text=医生复核完成", timeout=20000)
        body = page.locator("#resultBody").inner_text()
        assert "医生复核完成" in body, "应显示复核完成"
        assert "深二度" in body, body[-200:]
        print("   复核结论已回传用户端 ✓")
        page.screenshot(path=os.path.join(ROOT, "reports", "app_reviewed.png"), full_page=False)
        browser.close()

    print("\n控制台错误:", len(errors), errors[:6])
    assert not errors
    print("✅ 转人工闭环自测通过")


if __name__ == "__main__":
    main()
