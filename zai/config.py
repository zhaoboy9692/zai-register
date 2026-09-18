# -*- coding: utf-8 -*-
"""z.ai 注册机 — 配置文件"""

# ── 网络 ──────────────────────────────────────────────
# 代理地址（留空则不走代理）
PROXY = "http://127.0.0.1:7897"

# None: Playwright 安装的 Chromium；"chrome" / "msedge": 本机已安装的浏览器。
BROWSER_CHANNEL = None

# API 基地址（已确认：前端 Mn="" 用相对路径，浏览器 origin 解析为 chat.z.ai）
API_BASE = "https://chat.z.ai/api/v1"

# 浏览器 User-Agent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── 临时邮箱 API（cloudflare_temp_email）─────────────────
# 用于自动创建邮箱、接收 z.ai 验证邮件、提取 token
# 对接 dreamhunter2333/cloudflare_temp_email 项目
# 自部署教程: https://github.com/dreamhunter2333/cloudflare_temp_email
MAIL_API_BASE = ""    # 你的临时邮箱 API 地址，例: https://mail.yourdomain.com
MAIL_SITE_PASSWORD = ""   # 临时邮箱站点密码（前端设置里的 site password）
MAIL_DOMAIN = ""      # 临时邮箱域名，例: yourdomain.com

# ── IMAP 邮箱验证（备用方案）──────────────────────────
# 如果临时邮箱 API 不可用，可用传统 IMAP 方式
# 留空则跳过 IMAP 验证
IMAP_HOST = ""        # 例: imap.yourdomain.com
IMAP_PORT = 993
IMAP_USER = ""        # 邮箱地址
IMAP_PASS = ""        # 邮箱密码 / 授权码

# ── 注册默认值 ────────────────────────────────────────
# 密码默认与邮箱地址相同（用户要求所有密码统一）
DEFAULT_PASSWORD = None  # None 表示用邮箱地址作为密码
DEFAULT_SSO_REDIRECT = "https://chat.z.ai/"

# ── 限流 ──────────────────────────────────────────────
# z.ai 按邮箱限流 60 秒，批量注册间隔（秒）
BATCH_INTERVAL = 65

# ── 输出 ──────────────────────────────────────────────
OUTPUT_FILE = "accounts.json"
