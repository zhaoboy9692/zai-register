# -*- coding: utf-8 -*-
"""
邮箱验证模块 — 通过 IMAP 自动接收 z.ai 验证邮件并提取 token

支持两种验证链接格式：
  1. https://chat.z.ai/auth/verify?token=xxx
  2. https://chat.z.ai/auth/verify_email?token=xxx
  3. 邮件正文中直接包含 token=xxx
"""

import imaplib
import email as email_module
import re
import time
from email.header import decode_header
from urllib.parse import urlparse, parse_qs

from . import config


def fetch_verify_token(
    imap_host=None,
    imap_port=None,
    email_addr=None,
    email_pass=None,
    sender_filter="z.ai",
    wait_seconds=15,
    max_retries=6,
    poll_interval=10,
):
    """
    轮询 IMAP 收件箱，等待 z.ai 验证邮件并提取 token。

    参数:
        wait_seconds: 首次等待秒数（让邮件送达）
        max_retries: 轮询次数
        poll_interval: 每次轮询间隔秒数
    返回:
        token 字符串 或 None
    """
    host = imap_host or config.IMAP_HOST
    port = imap_port or config.IMAP_PORT
    user = email_addr or config.IMAP_USER
    pwd = email_pass or config.IMAP_PASS

    if not host or not user or not pwd:
        print("  [IMAP] 未配置 IMAP，跳过自动邮箱验证")
        return None

    print(f"  [IMAP] 等待 {wait_seconds} 秒让邮件送达...")
    time.sleep(wait_seconds)

    for attempt in range(1, max_retries + 1):
        print(f"  [IMAP] 第 {attempt}/{max_retries} 次轮询 {host}:{port} ...")
        token = _try_fetch(host, port, user, pwd, sender_filter)
        if token:
            return token
        if attempt < max_retries:
            print(f"  [IMAP] 未找到验证邮件，{poll_interval} 秒后重试...")
            time.sleep(poll_interval)

    print("  [IMAP] 超时，未能获取验证 token")
    return None


def _try_fetch(host, port, user, pwd, sender_filter):
    """连接 IMAP，搜索最新来自 sender 的邮件，提取 token。"""
    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pwd)
        mail.select("INBOX")

        status, data = mail.search(None, f'(FROM "{sender_filter}")')
        if status != "OK":
            mail.logout()
            return None

        ids = data[0].split()
        if not ids:
            # 也试试 SUBJECT 搜索
            status, data = mail.search(None, f'(SUBJECT "verify")')
            if status == "OK":
                ids = data[0].split()

        if not ids:
            mail.logout()
            return None

        # 取最新一封
        latest_id = ids[-1]
        status, msg_data = mail.fetch(latest_id, "(RFC822)")
        mail.logout()

        if status != "OK" or not msg_data or not msg_data[0]:
            return None

        raw = msg_data[0][1]
        msg = email_module.message_from_bytes(raw)

        # 提取正文
        body_text = _get_body(msg)

        # 提取验证链接中的 token
        return _extract_token(body_text)

    except Exception as e:
        print(f"  [IMAP] 错误: {type(e).__name__}: {e}")
        return None


def _get_body(msg):
    """从邮件对象提取文本正文。"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body += payload.decode(charset, errors="replace")
                    except Exception:
                        body += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, errors="replace")
            except Exception:
                body = payload.decode("utf-8", errors="replace")
    return body


def _extract_token(body_text):
    """从邮件正文提取验证 token。"""
    # 模式 1：URL 中的 token 参数
    urls = re.findall(r'https?://[^\s"\'<>]+(?:verify|verify_email)[^\s"\'<>]*', body_text, re.I)
    for url in urls:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "token" in params:
            return params["token"][0]

    # 模式 2：token=xxx 或 token: xxx
    m = re.search(r'token[=:]\s*([a-zA-Z0-9\-_.]+)', body_text, re.I)
    if m:
        return m.group(1)

    # 模式 3：链接中没有 verify 但有 token 参数
    urls2 = re.findall(r'https?://[^\s"\'<>]+\?token=([a-zA-Z0-9\-_.]+)', body_text, re.I)
    if urls2:
        return urls2[0]

    return None
