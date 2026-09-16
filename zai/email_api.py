# -*- coding: utf-8 -*-
"""
Cloudflare 临时邮箱 API 客户端

对接 dreamhunter2333/cloudflare_temp_email 项目搭建的邮箱系统。
通过 REST API 创建邮箱地址、获取 JWT、轮询收件箱、提取验证链接。

API 汇总（已实测确认）：
  POST /api/new_address          创建邮箱地址，返回 JWT
  GET  /api/settings             验证 JWT + 获取邮箱地址信息
  GET  /api/parsed_mails         获取已解析的邮件列表
  GET  /api/parsed_mail/:id      获取单封已解析邮件
  GET  /api/mails                获取原始邮件列表（fallback）
  GET  /api/mail/:id            获取单封原始邮件（fallback）

认证方式：
  x-custom-auth: <站点密码>      站点级验证
  Authorization: Bearer <JWT>    邮箱地址级验证
"""

import re
import time
import requests
from urllib.parse import urlparse, parse_qs

from . import config


class TempMailClient:
    """Cloudflare 临时邮箱 API 客户端"""

    def __init__(self, base_url=None, site_password=None, proxy=None):
        self.base = base_url or config.MAIL_API_BASE
        self.site_pwd = site_password or config.MAIL_SITE_PASSWORD
        self.session = requests.Session()
        proxy = proxy or config.PROXY
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.session.headers.update({
            "User-Agent": config.UA,
            "x-custom-auth": self.site_pwd,
        })
        self.jwt = None
        self.address = None

    # ── 创建邮箱地址 ─────────────────────────────────
    def create_address(self, name=None, domain=None):
        """
        创建新的邮箱地址。
        不传 name/domain 则随机生成。
        返回: (address, jwt)
        """
        domain = domain or config.MAIL_DOMAIN
        body = {}
        if name:
            body["name"] = name
        if domain:
            body["domain"] = domain

        r = self.session.post(f"{self.base}/api/new_address", json=body, timeout=15)
        if r.status_code != 200:
            raise Exception(f"创建邮箱失败: {r.status_code} {r.text[:200]}")

        data = r.json()
        self.jwt = data.get("jwt")
        self.address = data.get("address")
        # 更新 session header
        self.session.headers["Authorization"] = f"Bearer {self.jwt}"
        print(f"  [邮箱] 创建成功: {self.address}")
        return self.address, self.jwt

    # ── 用已有地址登录（重新获取 JWT）──────────────────
    def login_address(self, address, password=None):
        """
        用已有地址登录。
        如果地址设了独立密码则需 password。
        """
        body = {"address": address}
        if password:
            body["password"] = password
        r = self.session.post(f"{self.base}/api/login", json=body, timeout=15)
        if r.status_code != 200:
            raise Exception(f"登录邮箱失败: {r.status_code} {r.text[:200]}")
        data = r.json()
        self.jwt = data.get("jwt")
        self.address = address
        self.session.headers["Authorization"] = f"Bearer {self.jwt}"
        return self.jwt

    # ── 验证 JWT ──────────────────────────────────────
    def verify_jwt(self):
        """检查 JWT 是否有效，返回地址信息。"""
        r = self.session.get(f"{self.base}/api/settings", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    # ── 获取已解析邮件列表 ─────────────────────────────
    def list_parsed_mails(self, limit=20, offset=0):
        """获取已解析的邮件列表。"""
        r = self.session.get(
            f"{self.base}/api/parsed_mails?limit={limit}&offset={offset}",
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("results", [])

    # ── 获取单封已解析邮件 ─────────────────────────────
    def get_parsed_mail(self, mail_id):
        """获取单封已解析邮件。"""
        r = self.session.get(f"{self.base}/api/parsed_mail/{mail_id}", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    # ── 轮询等待验证邮件 ──────────────────────────────
    def wait_for_email(
        self,
        sender_filter="z.ai",
        subject_filter=None,
        timeout=180,
        poll_interval=5,
        initial_wait=10,
    ):
        """
        轮询收件箱，等待符合条件的邮件到达。
        返回邮件 dict 或 None（超时）。
        """
        print(f"  [邮箱] 等待邮件（发件人含 '{sender_filter}'）...")
        print(f"  [邮箱] 先等 {initial_wait} 秒让邮件送达...")

        time.sleep(initial_wait)

        start = time.time()
        seen_ids = set()

        while time.time() - start < timeout:
            mails = self.list_parsed_mails(limit=20, offset=0)
            for mail in mails:
                mail_id = mail.get("id")
                if mail_id in seen_ids:
                    continue
                seen_ids.add(mail_id)

                sender = mail.get("sender", "") or mail.get("source", "")
                subject = mail.get("subject", "")

                # 检查发件人过滤
                if sender_filter and sender_filter.lower() not in sender.lower():
                    continue
                # 检查主题过滤
                if subject_filter and subject_filter.lower() not in subject.lower():
                    continue

                print(f"  [邮箱] 收到邮件: {subject[:50]}")
                # 获取完整邮件内容
                full = self.get_parsed_mail(mail_id)
                if full:
                    return full
                return mail

            elapsed = int(time.time() - start)
            print(f"  [邮箱] 暂无新邮件（{elapsed}s/{timeout}s），{poll_interval}s 后重试...")
            time.sleep(poll_interval)

        print(f"  [邮箱] 超时 {timeout}s，未收到符合条件的邮件")
        return None

    # ── 从邮件提取验证 token ──────────────────────────
    @staticmethod
    def extract_verify_token(mail_data):
        """
        从邮件数据中提取 z.ai 验证 token。
        优先从 HTML/文本正文中的链接提取，fallback 到纯 token 字符串。
        """
        html = mail_data.get("html", "") or ""
        text = mail_data.get("text", "") or ""
        content = html + "\n" + text

        # 模式 1: URL 中的 token 参数
        urls = re.findall(
            r'https?://[^\s"\'<>]+(?:verify|verify_email|auth/verify)[^\s"\'<>]*',
            content, re.I,
        )
        for url in urls:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "token" in params:
                return params["token"][0]

        # 模式 2: token=xxx 或 token: xxx
        m = re.search(r'token[=:]\s*([a-zA-Z0-9\-_.]+)', content, re.I)
        if m:
            return m.group(1)

        # 模式 3: 链接中没有 verify 但有 token 参数
        urls2 = re.findall(
            r'https?://[^\s"\'<>]+\?token=([a-zA-Z0-9\-_.]+)',
            content, re.I,
        )
        if urls2:
            return urls2[0]

        return None

    # ── 从邮件提取完整激活链接 ──────────────────────
    @staticmethod
    def extract_verify_url(mail_data):
        """
        从邮件数据中提取完整的激活链接 URL。
        返回: str 或 None
        """
        html = mail_data.get("html", "") or ""
        text = mail_data.get("text", "") or ""
        content = html + "\n" + text

        # 处理 HTML 实体 &amp;
        content_clean = content.replace("&amp;", "&")

        # 找 verify_email 链接
        urls = re.findall(
            r'https?://[^\s"\'<>]+verify_email[^\s"\'<>]*',
            content_clean, re.I,
        )
        if urls:
            return urls[-1]  # 取最后一个（通常是纯文本版，HTML 实体已处理）

        # fallback: 找包含 verify 和 token 的链接
        urls2 = re.findall(
            r'https?://[^\s"\'<>]+verify[^\s"\'<>]*token=[^\s"\'<>]+',
            content_clean, re.I,
        )
        if urls2:
            return urls2[-1]

        return None
