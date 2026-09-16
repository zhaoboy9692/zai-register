# -*- coding: utf-8 -*-
"""
z.ai 注册机 — 命令行入口

用法:
  # ── 临时邮箱模式（推荐，全自动）──────────────────────
  # 自动创建临时邮箱 → 滑块 → 注册 → API 收验证邮件 → 完成
  python main.py --temp-mail --count 5 --password YourPass123

  # ── 自定义邮箱 + 临时邮箱 API ────────────────────────
  python main.py --temp-mail --mail-base https://mail.example.com --mail-pass yourcode --mail-domain example.com --count 3

  # ── 指定邮箱注册（传统模式）──────────────────────────
  python main.py --email user1@yourdomain.com --password YourPass123

  # ── 批量注册（指定邮箱列表）──────────────────────────
  python main.py --batch emails.txt --password YourPass123

  # ── IMAP 邮箱验证 ────────────────────────────────────
  python main.py --email user1@yourdomain.com --imap-host imap.yourdomain.com --imap-pass yourcode

  # ── 跳过邮箱验证 ─────────────────────────────────────
  python main.py --email user1@yourdomain.com --no-email
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zai import config
from zai.register import register_one, register_batch


def main():
    parser = argparse.ArgumentParser(
        description="z.ai 注册机 — Playwright 手动滑块 + API 注册",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 临时邮箱全自动
  python main.py --temp-mail --count 5 --password MyPass123

  # 指定邮箱
  python main.py --email user@example.com --password MyPass123

  # 批量
  python main.py --batch emails.txt --password MyPass123

  # 跳过邮箱验证
  python main.py --email user@example.com --no-email
        """,
    )
    # 邮箱模式
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--email", default=None, help="单个注册邮箱")
    g.add_argument("--batch", default=None, help="批量注册文件（每行一个邮箱）")
    g.add_argument("--temp-mail", action="store_true", help="使用临时邮箱 API 自动创建邮箱")

    # 临时邮箱参数
    parser.add_argument("--count", type=int, default=1, help="临时邮箱模式下的注册数量")
    parser.add_argument("--mail-base", default=None, help="临时邮箱 API 地址（覆盖 config.py）")
    parser.add_argument("--mail-pass", default=None, help="临时邮箱站点密码（覆盖 config.py）")
    parser.add_argument("--mail-domain", default=None, help="临时邮箱域名（覆盖 config.py）")

    # 通用参数
    parser.add_argument("--password", default=None, help="密码（默认: %s）" % config.DEFAULT_PASSWORD)
    parser.add_argument("--name", default=None, help="用户名（默认取邮箱前缀）")
    parser.add_argument("--proxy", default=None, help="HTTP 代理地址")
    parser.add_argument("--imap-host", default=None, help="IMAP 服务器地址")
    parser.add_argument("--imap-port", type=int, default=993, help="IMAP 端口")
    parser.add_argument("--imap-pass", default=None, help="邮箱密码/授权码（IMAP）")
    parser.add_argument("--no-email", action="store_true", help="跳过自动邮箱验证")
    parser.add_argument("--output", default=None, help="结果输出文件（默认: %s）" % config.OUTPUT_FILE)
    args = parser.parse_args()

    # 覆盖 config
    if args.proxy:
        config.PROXY = args.proxy
    if args.mail_base:
        config.MAIL_API_BASE = args.mail_base
    if args.mail_pass:
        config.MAIL_SITE_PASSWORD = args.mail_pass
    if args.mail_domain:
        config.MAIL_DOMAIN = args.mail_domain
    if args.imap_host and args.imap_pass:
        config.IMAP_HOST = args.imap_host
        config.IMAP_PORT = args.imap_port
        config.IMAP_PASS = args.imap_pass
    output_file = args.output or config.OUTPUT_FILE

    # 检查 playwright
    try:
        import playwright.sync_api
    except ImportError:
        print("[安装] 正在安装 playwright...")
        os.system(f'{sys.executable} -m pip install playwright')
        os.system(f'{sys.executable} -m playwright install chromium')
        print("[安装] 完成")

    # IMAP 配置
    imap_config = None
    if config.IMAP_HOST and config.IMAP_PASS:
        imap_config = {
            "host": config.IMAP_HOST,
            "port": config.IMAP_PORT,
            "password": config.IMAP_PASS,
        }

    results = []

    if args.temp_mail:
        # ── 临时邮箱模式 ──
        print(f"\n临时邮箱模式: 注册 {args.count} 个账号")
        print(f"  邮箱 API: {config.MAIL_API_BASE}")
        print(f"  域名: {config.MAIL_DOMAIN}")
        results = register_batch(
            emails=None,
            password=args.password,
            imap_config=imap_config,
            wait_email=not args.no_email,
            use_temp_mail=True,
            count=args.count,
        )
    elif args.batch:
        # ── 批量指定邮箱 ──
        if not os.path.exists(args.batch):
            print(f"文件不存在: {args.batch}")
            return
        with open(args.batch, "r", encoding="utf-8") as f:
            emails = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        results = register_batch(
            emails=emails,
            password=args.password,
            imap_config=imap_config,
            wait_email=not args.no_email,
            use_temp_mail=False,
        )
    elif args.email:
        # ── 单个邮箱 ──
        name = args.name or args.email.split("@")[0]
        result = register_one(
            email_addr=args.email,
            password=args.password,
            name=name,
            imap_config=imap_config,
            wait_email=not args.no_email,
            use_temp_mail=False,
        )
        results = [result]
    else:
        parser.print_help()
        return

    # 保存结果
    if results:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n{'='*60}")
        print(f"  结果已保存到 {output_file}")
        success = sum(1 for r in results if r.get("status") in ("complete", "verified"))
        print(f"  成功: {success} / {len(results)}")
        print(f"{'='*60}")
        for r in results:
            status = r.get("status", "?")
            email = r.get("email", "?")
            token_short = r.get("token", "")[:20] + "..." if r.get("token") else "无"
            print(f"  [{status:>13}] {email} / {r.get('password', '?')}  token: {token_short}")

        # ── 同时导出 txt 账号文件（邮箱|密码|状态|token）────
        txt_file = os.path.splitext(output_file)[0] + ".txt"
        lines = ["# z.ai 注册账号列表", "# 格式: 邮箱 | 密码 | 状态 | token", ""]
        for r in results:
            token = r.get("token", "") or ""
            lines.append(f"{r.get('email', '')} | {r.get('password', '')} | {r.get('status', '?')} | {token}")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  txt 账号已导出到 {txt_file}")
    else:
        print("没有注册结果")


if __name__ == "__main__":
    main()
