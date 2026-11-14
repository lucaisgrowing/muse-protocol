"""Offline tests only. FakeClient never connects to muse.ai."""
import re
import time
import unittest
from unittest.mock import Mock
from webui import create_app, TTL


class FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.calls = []
        self.live = False
        self.closed = False
        self.__class__.instances.append(self)

    def open_home(self): return Mock(status_code=200)
    def restart(self): self.calls.append("restart")
    def send_otp(self, email): self.calls.append(("send", email))
    def confirm_otp(self, code): return {"registration_state": "completion_required"}
    def register_completion(self, *args): self.calls.append(("register", args)); return {}
    def accept_tos(self): self.live = True; return Mock(status_code=200)
    def is_live(self): return self.live
    def wake_vm(self): return "00000000-0000-0000-0000-000000000000"
    def stateful_sharing(self): return Mock(status_code=200)
    def redeem(self, code): return True, "redeemed"
    def scratch_code(self): return "TEST-INVITE"
    def close(self): self.closed = True


class WebUITests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        self.app = create_app(FakeClient)
        self.app.testing = True
        self.client = self.app.test_client()
        self.home = self.client.get("/")
        self.csrf = re.search(r'name="csrf-token" content="([^"]+)"', self.home.text)[1]
        self.headers = {"X-CSRF-Token": self.csrf}

    def post(self, action, data=None, **kwargs):
        return self.client.post("/api/" + action, json=data or {}, headers=self.headers, **kwargs)

    def current_flow(self):
        return next(iter(self.app.extensions["muse_flows"].values()))

    def send(self):
        r = self.post("send-otp", {"email":"person@example.com"})
        self.assertEqual(r.status_code, 200, r.json)
        return FakeClient.instances[-1]

    def verified(self):
        client = self.send()
        self.assertEqual(self.post("confirm-otp", {"code":"123456"}).json["stage"], "verified")
        return client

    def registered(self):
        client = self.verified()
        self.assertEqual(self.post("register", {"first_name":"真实名", "last_name":"真实姓", "date_of_birth":"1995-06-18"}).json["stage"], "registered")
        return client

    def ready(self):
        client = self.registered()
        self.assertEqual(self.post("activate").json["stage"], "active")
        self.assertEqual(self.post("wake").json["stage"], "ready")
        return client

    def test_home_and_assets(self):
        self.assertEqual(self.home.status_code, 200)
        for path in ["style.css","app.js","mark.svg"]:
            response=self.client.get("/static/"+path)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_security_headers(self):
        self.assertEqual(self.home.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", self.home.headers["Content-Security-Policy"])
        self.assertIn("HttpOnly", self.home.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", self.home.headers["Set-Cookie"])

    def test_missing_csrf(self):
        self.assertEqual(self.client.post("/api/reset",json={}).status_code,403)

    def test_bad_csrf(self):
        self.assertEqual(self.client.post("/api/reset",json={},headers={"X-CSRF-Token":"wrong"}).status_code,403)

    def test_cross_origin(self):
        h = dict(self.headers, Origin="https://evil.example")
        self.assertEqual(self.client.post("/api/reset",json={},headers=h).status_code,403)

    def test_cross_site(self):
        h = dict(self.headers, **{"Sec-Fetch-Site":"cross-site"})
        self.assertEqual(self.client.post("/api/reset",json={},headers=h).status_code,403)

    def test_host_rebinding(self):
        self.assertEqual(self.client.get("/", headers={"Host":"evil.example"}).status_code,400)

    def test_not_json(self):
        self.assertEqual(self.client.post("/api/reset",data="a",headers=self.headers).status_code,415)

    def test_not_object(self):
        self.assertEqual(self.client.post("/api/reset",json=[],headers=self.headers).status_code,400)

    def test_unknown_action(self):
        self.assertEqual(self.post("not-a-thing").status_code,404)

    def test_no_skip(self):
        self.assertEqual(self.post("redeem", {"invite_code":"ABC"}).status_code,400)
        self.assertFalse(FakeClient.instances)

    def test_invalid_email(self):
        self.assertEqual(self.post("send-otp", {"email":"not-an-email"}).status_code,400)
        self.assertFalse(FakeClient.instances)

    def test_invalid_proxy(self):
        self.assertEqual(self.post("send-otp", {"email":"a@b.com", "proxy":"file:///etc/passwd"}).status_code,400)

    def test_invalid_otp(self):
        self.send()
        self.assertEqual(self.post("confirm-otp", {"code":"abcdef"}).status_code,400)

    def test_cooldown(self):
        self.send()
        self.assertEqual(self.post("send-otp", {"email":"other@example.com"}).status_code,400)

    def test_resend_existing_client_and_email(self):
        c = self.send()
        self.current_flow().sent_at -= 61
        self.assertEqual(self.post("send-otp").status_code,200)
        self.assertEqual(len(FakeClient.instances),1)
        self.assertEqual(c.calls[-1], ("send","person@example.com"))
        self.assertEqual(self.client.get("/api/status").json["email"], "person@example.com")

    def test_invalid_dob(self):
        self.verified()
        self.assertEqual(self.post("register", {"first_name":"X", "last_name":"Y", "date_of_birth":"2099-01-01"}).status_code,400)
        self.assertEqual(self.current_flow().stage,"verified")

    def test_no_default_identity(self):
        self.verified()
        self.assertEqual(self.post("register", {"date_of_birth":"1990-01-01"}).status_code,400)

    def test_full_flow(self):
        self.ready()
        self.assertEqual(self.post("redeem", {"invite_code":"ABC"}).json["stage"], "redeemed")
        self.assertEqual(self.post("own-code").json["own_code"], "TEST-INVITE")
        self.assertEqual(self.client.get("/api/status").json["stage"], "redeemed")

    def test_no_duplicate_registration(self):
        self.registered()
        self.assertEqual(self.post("register", {"first_name":"X", "last_name":"Y", "date_of_birth":"1990-01-01"}).status_code,400)

    def test_registration_timeout_is_uncertain(self):
        c = self.verified()
        c.register_completion = Mock(side_effect=TimeoutError("proxy://secret-secret"))
        r=self.post("register", {"first_name":"X", "last_name":"Y", "date_of_birth":"1990-01-01"})
        self.assertEqual(r.status_code,502)
        self.assertEqual(r.json["stage"],"uncertain")
        self.assertNotIn("secret-secret",r.text)
        self.assertEqual(self.post("register").status_code,400)

    def test_activation_checks_live(self):
        c=self.registered()
        c.is_live=lambda:False
        self.assertEqual(self.post("activate").status_code,502)
        self.assertEqual(self.current_flow().stage,"registered")

    def test_missing_vm(self):
        c=self.registered()
        self.post("activate")
        c.wake_vm=lambda:None
        self.assertEqual(self.post("wake").status_code,502)
        self.assertEqual(self.current_flow().stage,"active")

    def test_failed_sharing(self):
        c=self.registered()
        self.post("activate")
        c.stateful_sharing=lambda:Mock(status_code=403)
        self.assertEqual(self.post("wake").status_code,502)

    def test_known_redeem_failure(self):
        c=self.ready()
        c.redeem=lambda code:(False,"used_up")
        r=self.post("redeem",{"invite_code":"ABC"})
        self.assertEqual(r.status_code,422)
        self.assertEqual(r.json["stage"],"ready")

    def test_unknown_redeem_failure(self):
        c=self.ready()
        c.redeem=lambda code:(False,"http-502")
        r=self.post("redeem",{"invite_code":"ABC"})
        self.assertEqual(r.status_code,422)
        self.assertEqual(r.json["stage"],"uncertain")

    def test_own_code_failure_keeps_success(self):
        c=self.ready()
        self.post("redeem",{"invite_code":"ABC"})
        c.scratch_code=lambda:None
        r=self.post("own-code")
        self.assertEqual(r.status_code,502)
        self.assertEqual(r.json["stage"],"redeemed")

    def test_session_isolation(self):
        self.send()
        other=self.app.test_client()
        other.get("/")
        self.assertEqual(other.get("/api/status").json["stage"],"empty")

    def test_reset_clears(self):
        c=self.send()
        self.assertEqual(self.post("reset").json["stage"],"empty")
        self.assertTrue(c.closed)
        self.assertEqual(self.client.get("/api/status").json["email"],"")

    def test_expiry(self):
        c=self.send()
        self.current_flow().touched-=TTL+1
        self.assertEqual(self.client.get("/api/status").json["stage"],"empty")
        self.assertTrue(c.closed)

    def test_busy_lock(self):
        f=self.current_flow()
        f.lock.acquire()
        try:self.assertEqual(self.post("reset").status_code,409)
        finally:f.lock.release()

    def test_home_failure_no_otp(self):
        with unittest.mock.patch.object(FakeClient,"open_home",return_value=Mock(status_code=403)):
            self.assertEqual(self.post("send-otp",{"email":"person@example.com"}).status_code,502)
        self.assertNotIn(("send","person@example.com"),FakeClient.instances[-1].calls)

    def test_existing_account(self):
        c=self.send()
        c.confirm_otp=lambda code:{}
        c.live=True
        self.assertEqual(self.post("confirm-otp",{"code":"123456"}).json["stage"],"active")

    def test_unrecognized_confirmation(self):
        c=self.send()
        c.confirm_otp=lambda code:{}
        self.assertEqual(self.post("confirm-otp",{"code":"123456"}).status_code,502)
        self.assertEqual(self.current_flow().stage,"code_sent")


if __name__ == "__main__":
    unittest.main()
