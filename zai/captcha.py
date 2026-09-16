# -*- coding: utf-8 -*-
"""
阿里云滑块验证码 — Playwright 手动完成模块 v3

改进点（v3）：
1. page.on("response") 作为唯一主通道：拦截浏览器发出的 signup 请求的完整 captcha_verify_param 和响应
2. 不再从 console 提取 captcha_verify_param（console 输出会截断参数，len=278 vs 正确 len=280）
3. XHR hook 保留作为 backup 参数来源
4. 滑块验证通过后自动点提交，让浏览器自己发 signup 请求（不用截断的参数单独发 API）
5. 自动重试提交按钮（10秒无响应则重新点击）
6. 修复 _check_captcha_passed 中 JS 变量作用域 bug（text 在 if(el) 内声明，popup 块中引用导致永远 false）
"""

import json
import sys
import time
import ctypes

from . import config


# ── 窗口置顶工具（Windows / macOS，纯体验优化，失败不影响流程）──
def _bring_window_to_front(title_keyword):
    """
    把浏览器窗口置前显示。
    Windows: ctypes + user32 枚举窗口标题
    macOS:   AppleScript 按进程名激活（首次可能弹自动化权限，允许即可）
    """
    try:
        if sys.platform == "darwin":
            import subprocess
            script = (
                'tell application "System Events" to set frontmost of '
                f'(first process whose name contains "{title_keyword}") to true'
            )
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                print(f"  [窗口] 已将 {title_keyword} 窗口置前 (macOS)")
                return True
            return False

        if sys.platform != "win32":
            return False

        user32 = ctypes.windll.user32
        found = []

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_keyword and title_keyword in buf.value:
                found.append(hwnd)
                return False  # 找到就停止枚举
            return True

        user32.EnumWindows(enum_proc(_cb), 0)
        if not found:
            return False
        hwnd = found[0]
        # HWND_TOPMOST = -1, SWP_NOMOVE=0x0002, SWP_NOSIZE=0x0001
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        print(f"  [窗口] 已置顶浏览器窗口 (hwnd={hwnd})")
        return True
    except Exception as e:
        print(f"  [窗口] 置顶失败: {e}")
        return False


# 注入到页面的 JS 代码：只存储数据 + 拦截 XHR（不 hook fetch，避免被 SPA 覆盖）
# fetch 拦截改用 Playwright page.on("response") 代替
_INJECT_JS = """
window.__captchaParam = null;
window.__signupResponse = null;

// hook XMLHttpRequest（SPA 通常不覆盖 XHR prototype）
var _origXHRSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function(body) {
    try {
        if (typeof body === 'string') {
            try {
                var parsed = JSON.parse(body);
                if (parsed.captcha_verify_param) {
                    window.__captchaParam = parsed.captcha_verify_param;
                    console.log('[HOOK] captcha_verify_param captured via XHR, len=' + parsed.captcha_verify_param.length);
                }
            } catch(e) {}
        }
    } catch(e) {}
    return _origXHRSend.apply(this, arguments);
};
console.log('[HOOK] XHR hook installed');
"""


class CaptchaSolver:
    """通过 Playwright 打开浏览器，让用户手动完成滑块并拦截验证 token。"""

    def __init__(self, proxy=None):
        self.proxy = proxy or config.PROXY
        self._pw = None
        self._browser = None

    # ── 核心：打开浏览器，填表，等滑块，拦截 token ─────
    def solve(self, email, password, name, mode="signup", timeout=300):
        """
        参数:
            mode: "signup" 或 "signin"
            timeout: 等待用户完成滑块的超时秒数（默认 5 分钟）
        返回:
            (captcha_param, api_response)
            captcha_param: str 或 None（拦截到的 captcha_verify_param）
            api_response: dict 或 None（浏览器发出的 signup/signin 请求的响应 JSON）
        """
        from playwright.sync_api import sync_playwright

        result = {"captcha": None, "response": None}
        target_path = "/auths/signup" if mode == "signup" else "/auths/signin"

        with sync_playwright() as p:
            launch_args = ["--disable-blink-features=AutomationControlled"]
            browser = p.chromium.launch(
                headless=False,
                proxy={"server": self.proxy} if self.proxy else None,
                args=launch_args,
            )
            ctx = browser.new_context(
                user_agent=config.UA,
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            # ── 注入 JS hook（在页面加载前）──────────────────
            page.add_init_script(_INJECT_JS)
            print("  [浏览器] 已注入 JS hook（fetch/XHR 拦截）")

            # ── 监听 console 日志（调试用）────────────────────
            def on_console(msg):
                text = msg.text
                if "验证" in text or "captcha" in text.lower() or "滑动" in text or "HOOK" in text:
                    print(f"  [CONSOLE] {text[:200]}")
            page.on("console", on_console)

            # ── Playwright response 拦截（主通道）────────────
            def on_response(resp):
                if target_path in resp.url and resp.request.method == "POST":
                    try:
                        post_data = resp.request.post_data
                        if post_data:
                            body = json.loads(post_data)
                            cap = body.get("captcha_verify_param")
                            if cap:
                                result["captcha"] = cap
                                print(f"  [拦截-PW] captcha_verify_param 已捕获 (len={len(cap)})")
                        try:
                            result["response"] = resp.json()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"  [拦截-PW] 解析异常: {e}")
            page.on("response", on_response)

            # ── 导航 ──────────────────────────────────────────
            action = "signup" if mode == "signup" else "signin"
            url = f"https://chat.z.ai/auth?action={action}"
            print(f"  [浏览器] 打开 {url} ...")
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(2)

            # ── 窗口置顶：确保用户能看到浏览器窗口 ──────────
            for kw in ["Chromium", "Google Chrome", "Z.ai", "chat.z.ai"]:
                if _bring_window_to_front(kw):
                    break
            time.sleep(0.5)

            # ── 自动填表 ──────────────────────────────────────
            self._fill_form(page, email, password, name, mode)
            time.sleep(1)

            # ── 点击提交按钮 ───────────────────────────────────
            self._click_submit(page)

            # ── 等待验证码区域出现，自动点击触发滑块 ──────────
            time.sleep(2)
            self._trigger_captcha(page)

            print()
            print("  ╔══════════════════════════════════════════════════╗")
            print("  ║  请在浏览器窗口中手动完成滑块验证码              ║")
            print("  ║  完成后脚本会自动拦截 token 并继续              ║")
            print("  ╚══════════════════════════════════════════════════╝")
            print()

            # ── 轮询等待：page.on("response") 主通道 ──────────
            submit_clicked = False
            submit_click_time = 0
            start = time.time()

            while True:
                elapsed = time.time() - start
                if elapsed > timeout:
                    print(f"  [超时] {timeout} 秒内未捕获到验证码 token")
                    break

                # 检查 JS hook（XHR）是否捕获到 captcha（backup）
                try:
                    hook_captcha = page.evaluate("() => window.__captchaParam")
                    if hook_captcha and not result["captcha"]:
                        result["captcha"] = hook_captcha
                        print(f"  [拦截-JS] captcha_verify_param 已捕获 (len={len(hook_captcha)})")
                except Exception:
                    pass

                # 拿到 response → 完成
                if result["response"]:
                    break

                # ── 检测"验证通过"文字 → 滑块已完成 → 自动点提交 ──
                if not submit_clicked:
                    passed = self._check_captcha_passed(page)
                    if passed:
                        print('  [验证码] 检测到验证通过！自动点提交按钮...')
                        time.sleep(1)
                        self._click_submit(page)
                        submit_clicked = True
                        submit_click_time = time.time()
                        time.sleep(2)
                    else:
                        # 每 15 秒打印一次当前页面状态，方便排查
                        if int(elapsed) % 15 == 0 and int(elapsed) > 0:
                            try:
                                bt = page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
                                print(f"  [状态] body 文本片段: {bt.replace(chr(10), ' | ')[:200]}")
                            except Exception:
                                pass
                else:
                    # ── 已点提交但还没拿到 response，10秒后重试 ──
                    if not result["response"] and time.time() - submit_click_time > 10:
                        print('  [重试] 10秒无响应，重新点击提交按钮...')
                        self._click_submit(page)
                        submit_click_time = time.time()
                        time.sleep(2)

                time.sleep(0.5)

            # 额外等 2 秒确保响应完整
            if result["captcha"] or result["response"]:
                time.sleep(2)
                # 最后再读一次 JS hook
                try:
                    if not result["captcha"]:
                        h = page.evaluate("() => window.__captchaParam")
                        if h:
                            result["captcha"] = h
                except Exception:
                    pass

            browser.close()

        return result["captcha"], result["response"]

    # ── 检测滑块是否已完成验证 ──────────────────────
    def _check_captcha_passed(self, page):
        """检测阿里云滑块验证是否已通过。"""
        try:
            result = page.evaluate("""() => {
                // 0. 最可靠的检测：整个页面 body 文本中是否出现"验证通过/验证成功"
                //    （绿色提示条可能不在 #captcha-element 内，class 也不含 aliyun/captcha）
                const bodyText = (document.body ? document.body.innerText : '') || '';
                if (bodyText.includes('验证通过') || bodyText.includes('验证成功') ||
                    bodyText.includes('Slide completed') || bodyText.includes('Verification successful')) {
                    return true;
                }
                // 1. 检查 captcha-element 的"验证通过"文字（中英文）
                const el = document.querySelector('#captcha-element');
                if (el) {
                    const text = (el.innerText || '').trim();
                    if (text.includes('验证通过') || text.includes('验证成功') ||
                        text.includes('Slide completed') || text.includes('Verification passed') ||
                        text.includes('Verification successful')) {
                        return true;
                    }
                }
                // 2. 检查所有 captcha 相关元素
                const all = document.querySelectorAll('[class*="aliyun"], [id*="aliyun"], [class*="captcha"], [id*="captcha"]');
                for (const e of all) {
                    const t = (e.innerText || '').trim();
                    if (t.includes('验证通过') || t.includes('验证成功') ||
                        t.includes('Slide completed')) {
                        return true;
                    }
                }
                // 3. 检查滑块 class 变化
                const slider = document.querySelector('#aliyunCaptcha-sliding-slider');
                if (slider) {
                    const cls = slider.className || '';
                    if (cls.includes('success') || cls.includes('done') || cls.includes('pass')) {
                        return true;
                    }
                }
                // 4. 检查 captcha popup 是否已关闭（验证通过后 popup 会消失）
                const popup = document.querySelector('#aliyunCaptcha-window-float');
                if (popup) {
                    const cls = popup.className || '';
                    const style = window.getComputedStyle(popup);
                    if (cls.includes('hide') || cls.includes('hidden') || cls.includes('close') ||
                        style.display === 'none' || style.visibility === 'hidden') {
                        // popup 关了，再检查 captcha-element 文本
                        const el2 = document.querySelector('#captcha-element');
                        if (el2) {
                            const t2 = (el2.innerText || '').trim();
                            if (t2 && !t2.includes('点击开始验证') && !t2.includes('请拖动') &&
                                !t2.includes('Click to start') && !t2.includes('Please complete')) {
                                return true;
                            }
                        }
                    }
                }
                // 5. 检查 captcha-element 是否为空或 captcha wrapper 消失
                if (el) {
                    const text = (el.innerText || '').trim();
                    if (text === '' || !el.querySelector('#aliyunCaptcha-captcha-wrapper')) {
                        return true;
                    }
                }
                return false;
            }""")
            return bool(result)
        except Exception:
            return False

    # ── 自动填表 ─────────────────────────────────────────
    def _fill_form(self, page, email, password, name, mode):
        """尝试自动填写注册/登录表单"""
        # 用户名（仅注册时有）
        if mode == "signup":
            try:
                el = page.wait_for_selector(
                    'input[name="name"], input[placeholder*="name" i], input[placeholder*="名称"]',
                    timeout=5000,
                )
                el.fill(name)
                print(f"  [填表] 用户名: {name}")
            except Exception:
                # fallback: 第一个 text input
                inputs = page.query_selector_all("input[type='text'], input:not([type])")
                if inputs:
                    inputs[0].fill(name)
                    print(f"  [填表] 用户名(fallback): {name}")

        # 邮箱
        try:
            el = page.wait_for_selector(
                'input[type="email"], input[name="email"], input[placeholder*="email" i], input[placeholder*="邮箱"]',
                timeout=5000,
            )
            el.fill(email)
            print(f"  [填表] 邮箱: {email}")
        except Exception:
            inputs = page.query_selector_all("input[type='text'], input:not([type])")
            target = inputs[1] if len(inputs) > 1 else (inputs[0] if inputs else None)
            if target:
                target.fill(email)
                print(f"  [填表] 邮箱(fallback): {email}")

        # 密码
        try:
            el = page.wait_for_selector(
                'input[type="password"], input[name="password"], input[name="new-password"]',
                timeout=5000,
            )
            el.fill(password)
            print(f"  [填表] 密码: {'*' * len(password)}")
        except Exception:
            print("  [填表] 未找到密码框")

    # ── 点击提交 ─────────────────────────────────────────
    def _click_submit(self, page):
        """点击注册/登录按钮，触发滑块验证码"""
        selectors = [
            'button[type="submit"]',
            'button:has-text("Sign up")',
            'button:has-text("注册")',
            'button:has-text("Create")',
            'button:has-text("创建")',
            'button:has-text("Sign in")',
            'button:has-text("登录")',
        ]
        for sel in selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    print(f"  [提交] 已点击按钮: {sel}")
                    return
            except Exception:
                continue
        print("  [提交] 未找到提交按钮，请手动点击")

    # ── 触发验证码 ───────────────────────────────────────
    def _trigger_captcha(self, page):
        """点击验证码触发区域，让滑块弹出来。"""
        selectors = [
            "#captcha-element",
            ".captcha-element",
            "#aliyunCaptcha-start-icon",
            ".aliyunCaptcha-start-icon",
            "#aliyunCaptcha-captcha-body",
            ".aliyunCaptcha-captcha-body",
        ]
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    print(f"  [验证码] 已点击触发区域: {sel}")
                    time.sleep(2)
                    return
            except Exception:
                continue
        print("  [验证码] 未找到触发区域，请手动点击验证")

    # ── 浏览器激活：打开激活链接 + 填密码 + 完成注册 ───────
    def finish_signup_via_browser(self, verify_url, password, timeout=60):
        """
        用浏览器打开激活链接，自动填写密码，点击"完成注册"按钮。
        拦截 finish_signup API 响应获取 JWT token。

        参数:
            verify_url: 邮件中的完整激活链接
            password:  要设置的密码
            timeout:   超时秒数

        返回:
            dict 或 None：finish_signup 的响应 JSON（包含 JWT）
        """
        from playwright.sync_api import sync_playwright

        result = {"response": None}

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                proxy={"server": self.proxy} if self.proxy else None,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=config.UA,
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            # 注入 JS hook
            page.add_init_script(_INJECT_JS)

            # 拦截 finish_signup 响应
            def on_response(resp):
                if "finish_signup" in resp.url and resp.request.method == "POST":
                    try:
                        result["response"] = resp.json()
                        print(f"  [拦截] finish_signup 响应已捕获")
                    except Exception:
                        pass

            page.on("response", on_response)

            print(f"  [浏览器] 打开激活链接...")
            page.goto(verify_url, wait_until="networkidle", timeout=60000)
            time.sleep(3)

            # 填写密码（两个密码框）
            pw_inputs = page.query_selector_all('input[type="password"]')
            if len(pw_inputs) >= 2:
                pw_inputs[0].fill(password)
                pw_inputs[1].fill(password)
                print(f"  [填表] 密码 + 确认密码已填写: {'*' * len(password)}")
            else:
                print(f"  [填表] 只找到 {len(pw_inputs)} 个密码框")
                for inp in pw_inputs:
                    inp.fill(password)

            time.sleep(1)

            # 点击"完成注册"按钮
            clicked = False
            selectors = [
                'button:has-text("完成注册")',
                'button:has-text("Finish")',
                'button:has-text("Complete")',
                'button:has-text("Sign up")',
                'button[type="submit"]',
            ]
            for sel in selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        print(f"  [提交] 已点击: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                print("  [提交] 未找到完成注册按钮，请手动点击")

            # 等待响应
            start = time.time()
            while not result["response"] and time.time() - start < timeout:
                # 也检查 JS hook
                try:
                    hook_resp = page.evaluate("() => window.__signupResponse")
                    if hook_resp:
                        result["response"] = hook_resp
                        print(f"  [拦截-JS] finish_signup 响应已捕获")
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if result["response"]:
                print(f"  [完成] 注册激活成功！")
                time.sleep(2)
            else:
                print(f"  [超时] {timeout} 秒内未捕获到 finish_signup 响应")
                # 截图供调试
                try:
                    page.screenshot(path=".temp/finish_signup_debug.png")
                    print("  [调试] 截图已保存到 .temp/finish_signup_debug.png")
                except Exception:
                    pass

            browser.close()

        return result["response"]
