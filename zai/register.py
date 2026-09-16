# -*- coding: utf-8 -*-
"""
注册流程编排 — 串联滑块验证 → API 注册 → 邮箱验证 → 浏览器激活 → 完成

完整注册链路（已通过逆向确认）：
  1. signup      → POST /api/v1/auths/signup   {name, email, password, captcha_verify_param}
  2. 收验证邮件   → 临时邮箱 API 轮询 或 IMAP 轮询，提取完整激活链接
  3. 浏览器激活   → 打开激活链接 → 自动填密码 → 点"完成注册" → 拦截 JWT
  4. (可选) 登录  → 如果激活阶段没拿到 JWT，再尝试 API 登录

邮箱验证支持三种模式（按优先级自动选择）：
  A. 临时邮箱 API（cloudflare_temp_email）— 全自动，自动创建邮箱 + API 收信
  B. IMAP — 传统方式，需提供 IMAP 服务器信息
  C. 跳过 — 注册后手动点验证链接
"""

import json
import time
import random
import string
from urllib.parse import urlparse, parse_qs

from . import config
from .api import ZaiAPI
from .captcha import CaptchaSolver
from .email_api import TempMailClient
from .email_verify import fetch_verify_token


def _random_name(length=10):
    """生成随机邮箱前缀。"""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _pure_protocol_verify(api, email_addr, password, name, verify_url):
    """
    纯协议方式完成邮箱验证（不开浏览器）。

    激活链接格式: https://chat.z.ai/auth/verify_email?token=verify-xxx
    直接从链接提取 token，走两个 API:
      1. POST /api/v1/auths/verify_email    {username, email, token}
      2. POST /api/v1/auths/finish_signup   {username, email, token, password, ...}

    返回:
        dict 或 None: finish_signup 的响应 JSON（含 JWT），失败返回 None
    """
    # 从链接提取 token（保留完整 token，包括 verify- 前缀）
    parsed = urlparse(verify_url)
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]
    if not token:
        print("  [协议] 激活链接中没有 token 参数")
        return None

    print(f"  [协议] 提取到 token: {token[:24]}...（不开浏览器，纯 API 验证）")

    r = api.verify_email(name, email_addr, token)
    print(f"  [协议] verify_email: {r.status_code} {r.text[:150]}")
    if r.status_code != 200:
        return None

    r = api.finish_signup(name, email_addr, token, password)
    print(f"  [协议] finish_signup: {r.status_code} {r.text[:150]}")
    if r.status_code != 200:
        return None

    try:
        fin_data = r.json()
    except Exception:
        return None

    # 提取 JWT（finish_signup 响应: {user: {token}} 或 {token}）
    jwt = ""
    if isinstance(fin_data, dict):
        if fin_data.get("user"):
            jwt = fin_data["user"].get("token", "")
        else:
            jwt = fin_data.get("token", "")
    return fin_data if jwt else (fin_data or None)


def register_one(
    email_addr=None,
    password=None,
    name=None,
    imap_config=None,
    wait_email=True,
    mail_client=None,
    use_temp_mail=False,
):
    """
    注册单个 z.ai 账号。

    参数:
        email_addr:    注册邮箱（use_temp_mail=True 时可省略，自动创建）
        password:      密码（默认用邮箱地址作为密码）
        name:          用户名（默认取邮箱前缀）
        imap_config:   dict {host, port, password} 或 None（IMAP 模式）
        wait_email:    是否等待并自动完成邮箱验证
        mail_client:   TempMailClient 实例（临时邮箱 API 模式）
        use_temp_mail: 是否使用临时邮箱 API 自动创建邮箱

    返回:
        dict: {email, password, name, token?, status, ...}
    """
    # ── 临时邮箱模式：自动创建邮箱 ──────────────────
    if use_temp_mail and mail_client:
        print("\n[0/5] 创建临时邮箱地址...")
        try:
            name = name or _random_name()
            email_addr, mail_jwt = mail_client.create_address(name=name, domain=config.MAIL_DOMAIN)
        except Exception as e:
            print(f"  [失败] 创建临时邮箱失败: {e}")
            return {
                "email": None,
                "password": None,
                "name": name,
                "status": "failed",
                "error": str(e),
            }
    else:
        name = name or (email_addr.split("@")[0] if email_addr else _random_name())

    # 密码默认 = 邮箱地址
    password = password or email_addr

    print(f"\n{'='*60}")
    print(f"  注册: {email_addr} / {password} / {name}")
    print(f"{'='*60}")

    api = ZaiAPI()
    result = {
        "email": email_addr,
        "password": password,
        "name": name,
        "status": "pending",
    }

    # ── Step 1: 浏览器手动滑块 ───────────────────────
    print("\n[1/5] 打开浏览器，手动完成滑块验证码...")
    solver = CaptchaSolver()
    captcha_param, browser_response = solver.solve(email_addr, password, name, mode="signup")

    if not captcha_param and not browser_response:
        result["status"] = "failed"
        result["error"] = "未获取到验证码 token"
        print("[失败] 未能获取验证码 token")
        return result

    # ── Step 2: API 注册 ─────────────────────────────
    signup_data = browser_response
    if not signup_data:
        print("\n[2/5] 用 API 发送注册请求...")
        r = api.signup(name, email_addr, password, captcha_param)
        try:
            signup_data = r.json()
        except Exception:
            signup_data = None
        print(f"  响应: {r.status_code} {r.text[:200]}")
    else:
        print("\n[2/5] 浏览器已完成注册请求")
        print(f"  响应: {json.dumps(signup_data, ensure_ascii=False)[:200]}")

    if not signup_data:
        result["status"] = "failed"
        result["error"] = "注册请求失败"
        return result

    result["signup_response"] = signup_data
    result["status"] = "signup_done"

    # ── Step 3: 邮箱验证 ─────────────────────────────
    if not wait_email:
        print("\n[3/5] 跳过邮箱验证（--no-email）")
        result["status"] = "email_pending"
    elif use_temp_mail and mail_client:
        # 模式 A：临时邮箱 API
        print("\n[3/5] 通过临时邮箱 API 等待验证邮件...")
        mail_data = mail_client.wait_for_email(
            sender_filter="z.ai",
            timeout=180,
            poll_interval=5,
            initial_wait=10,
        )
        if mail_data:
            # 提取完整激活链接
            verify_url = TempMailClient.extract_verify_url(mail_data)
            if verify_url:
                print(f"  激活链接: {verify_url[:80]}...")
                # ── Step 4: 纯协议验证（不开浏览器）──────────────
                print("\n[4/5] 纯协议方式验证邮箱 + 完成注册...")
                fin_resp = _pure_protocol_verify(api, email_addr, password, name, verify_url)
                if not fin_resp:
                    # 协议失败时 fallback 到浏览器方式
                    print("  [fallback] 纯协议验证失败，改用浏览器方式...")
                    solver2 = CaptchaSolver()
                    fin_resp = solver2.finish_signup_via_browser(verify_url, password, timeout=60)
                if fin_resp:
                    result["finish_signup_response"] = fin_resp
                    # 提取 JWT
                    if isinstance(fin_resp, dict):
                        if fin_resp.get("user"):
                            jwt = fin_resp["user"].get("token", "")
                        else:
                            jwt = fin_resp.get("token", "")
                        if jwt:
                            result["token"] = jwt
                            result["status"] = "complete"
                            print(f"  注册完成！JWT: {jwt[:30]}...")
                        else:
                            result["status"] = "verified"
                            print("  完成注册，但响应中未找到 JWT")
                    else:
                        result["status"] = "verified"
                else:
                    result["status"] = "verify_failed"
                    print("  [警告] 浏览器激活未捕获到响应")
            else:
                # fallback: 用 API 方式
                print("  [fallback] 未提取到完整链接，尝试用 token API 验证...")
                token = TempMailClient.extract_verify_token(mail_data)
                if token:
                    print(f"  验证 token: {token[:20]}...")
                    r = api.verify_email(name, email_addr, token)
                    print(f"  verify_email: {r.status_code} {r.text[:200]}")
                    r = api.finish_signup(name, email_addr, token, password)
                    print(f"  finish_signup: {r.status_code} {r.text[:200]}")
                    if r.status_code == 200:
                        result["status"] = "verified"
                        try:
                            fin_data = r.json()
                            if isinstance(fin_data, dict) and fin_data.get("user"):
                                jwt = fin_data["user"].get("token", "")
                            else:
                                jwt = fin_data.get("token", "")
                            if jwt:
                                result["token"] = jwt
                                result["status"] = "complete"
                                print(f"  邮箱验证完成！JWT: {jwt[:30]}...")
                        except Exception:
                            print("  邮箱验证完成！")
                    else:
                        result["status"] = "verify_failed"
                else:
                    print("  [警告] 邮件中未找到验证 token 或链接")
                    result["status"] = "email_pending"
                    print(f"  请检查 {email_addr} 收件箱并手动验证")
        else:
            result["status"] = "email_pending"
            print(f"  请检查 {email_addr} 收件箱并手动验证")
    elif imap_config:
        # 模式 B：IMAP
        print("\n[3/5] 通过 IMAP 等待验证邮件...")
        token = fetch_verify_token(
            imap_host=imap_config.get("host"),
            imap_port=imap_config.get("port", 993),
            email_addr=email_addr,
            email_pass=imap_config.get("password"),
        )
        if token:
            print(f"  验证 token: {token[:20]}...")
            print("\n[4/5] 验证邮箱 + 完成注册...")
            r = api.verify_email(name, email_addr, token)
            print(f"  verify_email: {r.status_code} {r.text[:200]}")
            r = api.finish_signup(name, email_addr, token, password)
            print(f"  finish_signup: {r.status_code} {r.text[:200]}")
            if r.status_code == 200:
                result["status"] = "verified"
                try:
                    fin_data = r.json()
                    if isinstance(fin_data, dict) and fin_data.get("user"):
                        jwt = fin_data["user"].get("token", "")
                    else:
                        jwt = fin_data.get("token", "")
                    if jwt:
                        result["token"] = jwt
                        result["status"] = "complete"
                        print(f"  邮箱验证完成！JWT: {jwt[:30]}...")
                    else:
                        print("  邮箱验证完成！")
                except Exception:
                    print("  邮箱验证完成！")
            else:
                result["status"] = "verify_failed"
        else:
            result["status"] = "email_pending"
            print("  未能自动获取验证 token，请手动验证邮箱")
    else:
        print("\n[3/5] 跳过邮箱验证（未配置任何邮箱验证方式）")
        result["status"] = "email_pending"

    # ── Step 5: 登录测试（如果已有 token 则跳过）────────
    if result.get("token"):
        print("\n[5/5] 已从完成注册获取到 JWT，跳过登录测试")
    else:
        print("\n[5/5] 登录测试...")
        r = api.signin(email_addr, password)
        if r.status_code == 200:
            try:
                data = r.json()
                jwt = data.get("token", "")
                if jwt:
                    result["token"] = jwt
                    result["status"] = "complete"
                    print(f"  登录成功！JWT: {jwt[:30]}...")
                else:
                    print(f"  登录返回 200 但无 token: {r.text[:200]}")
            except Exception:
                print(f"  登录响应解析失败: {r.text[:200]}")
        else:
            print(f"  登录返回 {r.status_code}（登录需要验证码，账号已注册成功）")
            print(f"  {r.text[:200]}")

    return result


def register_batch(
    emails=None,
    password=None,
    imap_config=None,
    wait_email=True,
    use_temp_mail=False,
    count=None,
):
    """
    批量注册。

    参数:
        emails:        邮箱列表 [str]（传统模式）
        password:      统一密码（不传则每个账号用各自邮箱地址作为密码）
        imap_config:   IMAP 配置 dict
        wait_email:    是否等待邮箱验证
        use_temp_mail: 是否使用临时邮箱 API 自动创建邮箱
        count:         使用临时邮箱时的注册数量

    返回:
        list[dict]: 每个账号的注册结果
    """
    results = []

    # 确定注册数量
    if use_temp_mail:
        total = count or len(emails or [])
        mail_client = TempMailClient()
    else:
        total = len(emails or [])
        mail_client = None

    print(f"批量注册 {total} 个账号")

    for i in range(total):
        if use_temp_mail:
            # 临时邮箱模式：每个号自动创建新邮箱，密码 = 邮箱地址
            pwd = password  # None → register_one 里会用邮箱地址
            print(f"\n[{i+1}/{total}] 自动创建临时邮箱...")
            result = register_one(
                password=pwd,
                imap_config=imap_config,
                wait_email=wait_email,
                mail_client=mail_client,
                use_temp_mail=True,
            )
        else:
            email_addr = emails[i]
            name = email_addr.split("@")[0]
            pwd = password or email_addr  # 密码默认 = 邮箱地址
            print(f"\n[{i+1}/{total}] {email_addr}")
            result = register_one(
                email_addr=email_addr,
                password=pwd,
                name=name,
                imap_config=imap_config,
                wait_email=wait_email,
                use_temp_mail=False,
            )

        results.append(result)

        # 批量间隔（避免限流）
        if i < total - 1:
            wait = config.BATCH_INTERVAL
            print(f"\n等待 {wait} 秒（避免限流）...")
            time.sleep(wait)

    return results
