"""Single-user, loopback-only Web UI for the existing Muse protocol client."""
from __future__ import annotations

import argparse
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from musepy.client import MuseClient, MuseError

TTL = 30 * 60
MAX_SESSIONS = 64
STEPS = {"empty": 0, "code_sent": 1, "verified": 2, "registered": 3,
         "active": 4, "ready": 5, "redeemed": 6, "uncertain": -1}


@dataclass
class Flow:
    client: object | None = None
    stage: str = "empty"
    touched: float = field(default_factory=time.monotonic)
    sent_at: float = 0
    own_code: str | None = None
    email: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self):
        if self.client:
            self.client.close()
        self.client = None


class InputError(ValueError):
    pass


def text(data, name, limit=200, required=True):
    value = data.get(name, "")
    if not isinstance(value, str) or len(value) > limit:
        raise InputError("输入格式不正确或内容过长，请检查后重试。")
    value = value.strip()
    if required and not value:
        raise InputError("请填写所有必填项。")
    return value


def create_app(client_factory=MuseClient):
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=8192,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    flows: dict[str, Flow] = {}
    registry_lock = threading.Lock()
    app.extensions["muse_flows"] = flows

    def flow():
        now = time.monotonic()
        with registry_lock:
            for key, item in list(flows.items()):
                if now - item.touched > TTL and item.lock.acquire(blocking=False):
                    try:
                        item.close()
                        del flows[key]
                    finally:
                        item.lock.release()
            sid = session.get("sid")
            if sid not in flows:
                if len(flows) >= MAX_SESSIONS:
                    raise InputError("本地会话数已满，请稍后重试或重启 Web UI。")
                sid = secrets.token_urlsafe(32)
                session["sid"] = sid
                flows[sid] = Flow()
            f = flows[sid]
            f.touched = now
            return f

    @app.before_request
    def guard():
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="拒绝跨站请求，请从本地操作台重试。"), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="拒绝跨站请求。"), 403
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return jsonify(error="页面会话已过期，请刷新页面。"), 403
            if not request.is_json:
                return jsonify(error="请求必须使用 JSON。"), 415

    @app.after_request
    def headers(response):
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.get("/")
    def index():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        flow()
        return render_template("index.html", csrf=session["csrf"])

    @app.get("/api/status")
    def status():
        f = flow()
        return jsonify(stage=f.stage, own_code=f.own_code, email=f.email,
                       resend_after=max(0, int(60 - (time.monotonic() - f.sent_at))))

    def require(f, *stages):
        if f.stage not in stages:
            raise InputError("当前步骤不适用，请按页面流程继续；如需重来，请先清除会话。")

    @app.post("/api/<action>")
    def act(action):
        if action not in {"send-otp", "confirm-otp", "register", "activate", "wake", "redeem", "own-code", "reset"}:
            return jsonify(error="操作不存在。"), 404
        f = flow()
        if not f.lock.acquire(blocking=False):
            return jsonify(error="上一步还在处理中，请稍等。"), 409
        try:
            data = request.get_json()
            if not isinstance(data, dict):
                raise InputError("请求内容必须是对象。")
            message = ""
            if action == "reset":
                f.close()
                f.stage, f.own_code, f.sent_at, f.email = "empty", None, 0, ""
                message = "本地会话已清除。服务器上的账号不会被删除。"
            elif action == "send-otp":
                require(f, "empty", "code_sent")
                if time.monotonic() - f.sent_at < 60:
                    raise InputError("请等待 60 秒后再发送验证码。")
                if f.stage == "empty":
                    email = text(data, "email", 254)
                    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                        raise InputError("请输入有效邮箱地址。")
                    proxy = text(data, "proxy", 1000, False)
                    if proxy:
                        parsed = urlsplit(proxy)
                        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
                            raise InputError("代理地址应以 http://、https:// 或 socks5:// 开头。")
                    f.close()
                    f.client = client_factory(proxy=proxy or None, lang="ja", timeout=30)
                    f.email = email
                    home = f.client.open_home()
                    if home.status_code != 200:
                        raise MuseError(f"home {home.status_code}")
                    f.client.restart()
                # Resend keeps the same client and recipient, including after a page refresh.
                # Do not retry automatically: a timeout may still mean the OTP was sent.
                f.sent_at = time.monotonic()
                f.client.send_otp(f.email)
                f.stage = "code_sent"
                message = "验证码发送请求已成功，请查看邮箱。"
            elif action == "confirm-otp":
                require(f, "code_sent")
                code = text(data, "code", 6)
                if not re.fullmatch(r"[0-9]{6}", code):
                    raise InputError("验证码应为 6 位数字。")
                result = f.client.confirm_otp(code)
                # Existing-account login must not be treated as a new registration.
                if result.get("registration_state") == "completion_required":
                    f.stage = "verified"
                    message = "邮箱验证通过，请填写真实姓名和生日。"
                elif f.client.is_live():
                    f.stage = "active"
                    message = "已登录现有账号，可以准备兑换。"
                else:
                    raise MuseError("unsupported confirmation state")
            elif action == "register":
                require(f, "verified")
                first = text(data, "first_name", 100)
                last = text(data, "last_name", 100)
                try:
                    dob = date.fromisoformat(text(data, "date_of_birth", 10))
                except ValueError:
                    raise InputError("请填写有效生日。") from None
                if not date(1900, 1, 1) <= dob <= date.today():
                    raise InputError("请填写真实且有效的生日。")
                # A failed/ambiguous write must not be blindly replayed by the UI.
                f.stage = "uncertain"
                f.client.register_completion(dob.isoformat(), first, last)
                f.stage = "registered"
                message = "注册接口已成功，下一步激活会话。"
            elif action == "activate":
                require(f, "registered")
                response = f.client.accept_tos()
                if response.status_code != 200 or not f.client.is_live():
                    raise MuseError(f"activation {response.status_code}")
                f.stage = "active"
                message = "会话已激活。"
            elif action == "wake":
                require(f, "active")
                vm = f.client.wake_vm()
                if not vm:
                    raise MuseError("VM not found")
                response = f.client.stateful_sharing()
                if response.status_code != 200:
                    raise MuseError(f"sharing {response.status_code}")
                f.stage = "ready"
                message = "已取得 VM 标识并发送唤醒请求，可以尝试兑换；不代表 VM 已运行。"
            elif action == "redeem":
                require(f, "ready")
                invite = text(data, "invite_code", 128)
                f.stage = "uncertain"
                ok, reason = f.client.redeem(invite)
                if not ok:
                    if reason in {"already_redeemed", "used_up", "invalid"}:
                        f.stage = "ready"
                    reasons = {"already_redeemed": "该账号已经兑换过邀请码。", "used_up": "邀请码的使用次数已用尽。",
                               "invalid": "服务端认为邀请码无效，请核对邀请码和 VM 状态。"}
                    return jsonify(error=reasons.get(reason, "兑换结果不明确，请到官网确认，避免重复提交。"), stage=f.stage), 422
                f.stage = "redeemed"
                message = "服务端已确认兑换成功。"
            elif action == "own-code":
                require(f, "redeemed")
                own = f.client.scratch_code()
                if not own:
                    raise MuseError("invite code unavailable")
                f.own_code = own
                message = "已取得你的邀请码。"
            return jsonify(stage=f.stage, message=message, own_code=f.own_code)
        except InputError as exc:
            return jsonify(error=str(exc), stage=f.stage), 400
        except Exception as exc:
            # Raw protocol errors can contain response bodies, proxy credentials or cookies.
            # Never return them or log them. Only a whitelisted HTTP status is exposed.
            status_match = re.search(r"\b(400|401|403|404|409|429|500|502|503|504)\b", str(exc))
            suffix = f"（HTTP {status_match.group(1)}）" if status_match else ""
            uncertain = "结果可能已生效，请到官网确认后再继续。" if f.stage == "uncertain" else "请检查网络或代理后再手动操作。"
            return jsonify(error=f"服务请求未能完成{suffix}。{uncertain}", stage=f.stage), 502
        finally:
            f.touched = time.monotonic()
            f.lock.release()

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error="请求被拒绝，请检查地址、内容和本地服务状态。"), exc.code

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Muse Protocol 本地 Web UI（只监听回环地址）")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    from waitress import serve
    print(f"Muse Protocol → http://127.0.0.1:{args.port}  按 Ctrl+C 停止", flush=True)
    serve(create_app(), host="127.0.0.1", port=args.port, threads=4)
