# -*- coding: utf-8 -*-
"""
Playwright 人工滑块流程：响应监听、明确成功信号、有限触发重试。
浏览器关闭、页面跳转、缺少提交按钮或等待超时都会终止当前尝试。
"""

import json
import sys
import time
import ctypes
from contextlib import ExitStack

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
        from ctypes import wintypes
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
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

        result = {"captcha": None, "response": None, "error": None}
        target_path = "/auths/signup" if mode == "signup" else "/auths/signin"

        with sync_playwright() as p, ExitStack() as cleanup:
            launch_args = ["--disable-blink-features=AutomationControlled"]
            browser = p.chromium.launch(
                headless=False,
                channel=config.BROWSER_CHANNEL,
                proxy={"server": self.proxy} if self.proxy else None,
                args=launch_args,
            )
            cleanup.callback(browser.close)
            ctx = browser.new_context(
                user_agent=config.UA,
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            # ── 注入 JS hook（在页面加载前）──────────────────
            page.add_init_script(_INJECT_JS)
            print("  [浏览器] 已安装 XHR hook 和响应监听")

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
                            data = resp.json()
                            if resp.status >= 400 or not isinstance(data, dict) or data.get('success') is False or data.get('detail') or data.get('error'):
                                result['error'] = f'注册接口拒绝请求（HTTP {resp.status}），请查看页面提示'
                            else:
                                result["response"] = data
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"  [拦截-PW] 解析异常: {e}")
            page.on("response", on_response)

            # ── 导航 ──────────────────────────────────────────
            action = "signup" if mode == "signup" else "signin"
            url = f"https://chat.z.ai/auth?action={action}"
            print(f"  [浏览器] 打开 {url} ...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)

            page.bring_to_front()

            # ── 窗口置顶：确保用户能看到浏览器窗口 ──────────
            for kw in ["Chromium", "Google Chrome", "Z.ai", "chat.z.ai"]:
                if _bring_window_to_front(kw):
                    break
            page.wait_for_timeout(500)

            # ── 自动填表 ──────────────────────────────────────
            self._fill_form(page, email, password, name, mode)
            page.wait_for_timeout(1000)

            # ── 点击提交按钮 ───────────────────────────────────
            self._click_submit(page)

            # ── 等待验证码区域出现，自动点击触发滑块 ──────────
            page.wait_for_timeout(2000)
            if result['response'] is None and not self._captcha_visible(page):
                self._trigger_captcha(page)

            print()
            print("  ╔══════════════════════════════════════════════════╗")
            print("  ║  请在浏览器窗口中手动完成滑块验证码              ║")
            print("  ║  完成后脚本会自动拦截 token 并继续              ║")
            print("  ╚══════════════════════════════════════════════════╝")
            print()

            # Playwright 的等待会处理浏览器事件；time.sleep 会阻塞事件分发。
            start = time.monotonic()
            next_trigger = start + 10
            next_submit = start
            trigger_attempts = 0
            missing_submit_since = None
            while time.monotonic() - start < timeout:
                if result['error']:
                    raise RuntimeError(result['error'])
                if result['response'] is not None:
                    break
                if page.is_closed() or not browser.is_connected():
                    raise RuntimeError('验证窗口已关闭，当前账号已停止')
                if '/auth' not in page.url:
                    raise RuntimeError('页面已离开登录/注册页，请重新运行当前账号')
                now = time.monotonic()
                if self._check_captcha_passed(page):
                    if now >= next_submit:
                        if self._click_submit(page):
                            missing_submit_since = None
                        elif missing_submit_since is None:
                            missing_submit_since = now
                        elif now - missing_submit_since >= 15:
                            raise RuntimeError('验证后找不到提交按钮，停止当前账号，避免无限重试')
                        next_submit = now + 10
                elif now >= next_trigger and not self._captcha_visible(page):
                    if trigger_attempts < 3:
                        page.bring_to_front()
                        self._trigger_captcha(page)
                        trigger_attempts += 1
                        print(f'  [验证码] 已重新检查触发入口（{trigger_attempts}/3），请手动拖动滑块')
                        next_trigger = now + 15
                    else:
                        raise RuntimeError('验证码弹窗仍未出现，可能未加载或被站点限制；请检查浏览器后重试')
                page.wait_for_timeout(500)
            else:
                raise TimeoutError(f'{timeout} 秒内未完成验证或未收到注册响应')

        return result["captcha"], result["response"]

    def _check_captcha_passed(self, page):
        """只接受可见元素中的明确成功信号；空容器或关闭弹窗不算通过。"""
        return page.evaluate("""() => {
            const visible = e => !!e.getClientRects().length &&
                getComputedStyle(e).visibility !== 'hidden';
            const labels = ['验证通过', '验证成功', '滑动成功',
                'Slide completed', 'Verification passed', 'Verification successful'];
            const nodes = document.querySelectorAll(
                '[class*="aliyun"], [id*="aliyun"], [class*="captcha"], [id*="captcha"]');
            return [...nodes].some(e => visible(e) &&
                labels.some(label => (e.innerText || '').includes(label)));
        }""")

    def _captcha_visible(self, page):
        return any(frame.locator(
            '#aliyunCaptcha-window-float:visible, #aliyunCaptcha-sliding-slider:visible'
        ).count() for frame in page.frames)

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
                    btn.click(timeout=3000)
                    print(f"  [提交] 已点击按钮: {sel}")
                    return True
            except Exception:
                continue
        print("  [提交] 未找到提交按钮，请手动点击")
        return False

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
                    el.click(timeout=3000)
                    print(f"  [验证码] 已点击触发区域: {sel}")
                    page.wait_for_timeout(2000)
                    return True
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

        with sync_playwright() as p, ExitStack() as cleanup:
            browser = p.chromium.launch(
                headless=False,
                channel=config.BROWSER_CHANNEL,
                proxy={"server": self.proxy} if self.proxy else None,
                args=["--disable-blink-features=AutomationControlled"],
            )
            cleanup.callback(browser.close)
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
            page.goto(verify_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

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

            page.wait_for_timeout(1000)

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
                        btn.click(timeout=3000)
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
                page.wait_for_timeout(500)

            if result["response"]:
                print(f"  [完成] 注册激活成功！")
                page.wait_for_timeout(2000)
            else:
                print(f"  [超时] {timeout} 秒内未捕获到 finish_signup 响应")
                # 截图供调试
                try:
                    page.screenshot(path=".temp/finish_signup_debug.png")
                    print("  [调试] 截图已保存到 .temp/finish_signup_debug.png")
                except Exception:
                    pass

        return result["response"]
