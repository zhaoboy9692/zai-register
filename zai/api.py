# -*- coding: utf-8 -*-
"""z.ai API 客户端 — 封装注册/登录/验证邮箱等 HTTP 调用"""

import json
import requests

from . import config


class ZaiAPI:
    def __init__(self, proxy=None, ua=None):
        self.session = requests.Session()
        proxy = proxy or config.PROXY
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.session.headers.update({
            "User-Agent": ua or config.UA,
            "Origin": "https://chat.z.ai",
            "Referer": "https://chat.z.ai/",
            "Content-Type": "application/json",
        })
        self.base = config.API_BASE

    # ── 注册 ─────────────────────────────────────────
    def signup(self, name, email, password, captcha_param=None):
        """
        POST /api/v1/auths/signup
        body: {name, email, password, profile_image_url, sso_redirect, captcha_verify_param}
        """
        body = {
            "name": name,
            "email": email,
            "password": password,
            "profile_image_url": "",
            "sso_redirect": config.DEFAULT_SSO_REDIRECT,
        }
        if captcha_param:
            body["captcha_verify_param"] = captcha_param
        r = self.session.post(f"{self.base}/auths/signup", json=body, timeout=30)
        return r

    # ── 登录 ─────────────────────────────────────────
    def signin(self, email, password, captcha_param=None):
        """
        POST /api/v1/auths/signin
        body: {email, password, captcha_verify_param}
        """
        body = {"email": email, "password": password}
        if captcha_param:
            body["captcha_verify_param"] = captcha_param
        r = self.session.post(f"{self.base}/auths/signin", json=body, timeout=30)
        return r

    # ── 验证邮箱 ─────────────────────────────────────
    def verify_email(self, username, email, token):
        """
        POST /api/v1/auths/verify_email
        body: {username, email, token}
        """
        body = {"username": username, "email": email, "token": token}
        r = self.session.post(f"{self.base}/auths/verify_email", json=body, timeout=30)
        return r

    # ── 完成注册 ─────────────────────────────────────
    def finish_signup(self, username, email, token, password):
        """
        POST /api/v1/auths/finish_signup
        body: {username, email, token, password, profile_image_url, sso_redirect}
        """
        body = {
            "username": username,
            "email": email,
            "token": token,
            "password": password,
            "profile_image_url": "",
            "sso_redirect": config.DEFAULT_SSO_REDIRECT,
        }
        r = self.session.post(f"{self.base}/auths/finish_signup", json=body, timeout=30)
        return r

    # ── 重发验证邮件 ─────────────────────────────────
    def resend_email(self, name, email):
        """
        POST /api/v1/auths/resend_email
        body: {name, email, sso_redirect}
        """
        body = {
            "name": name,
            "email": email,
            "sso_redirect": config.DEFAULT_SSO_REDIRECT,
        }
        r = self.session.post(f"{self.base}/auths/resend_email", json=body, timeout=30)
        return r

    # ── 登出 ─────────────────────────────────────────
    def signout(self):
        r = self.session.get(f"{self.base}/auths/signout", timeout=15)
        return r
