"""Protocol regressions: only mocked HTTP responses, never live requests."""
import unittest
from unittest.mock import Mock
from musepy.client import MuseClient, MuseError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = MuseClient()
        self.client.s.close()
        self.client.s = Mock()

    def test_redeem_parses_json_with_whitespace(self):
        r=Mock(status_code=200, text='{"success": true}')
        r.json.return_value={"success": True}
        self.client.s.post.return_value=r
        self.assertEqual(self.client.redeem("TEST"),(True,"redeemed"))

    def test_redeem_preserves_server_reason(self):
        r=Mock(status_code=200)
        r.json.return_value={"success":False,"reason":"used_up"}
        self.client.s.post.return_value=r
        self.assertEqual(self.client.redeem("TEST"),(False,"used_up"))

    def test_redeem_does_not_accept_truthy_string(self):
        r=Mock(status_code=200)
        r.json.return_value={"success":"true"}
        self.client.s.post.return_value=r
        self.assertEqual(self.client.redeem("TEST"),(False,"http-200"))

    def test_documented_redemption_status(self):
        r = Mock(status_code=200)
        r.json.return_value = {"redemptionStatus": "redeemed"}
        self.client.s.post.return_value = r
        self.assertEqual(self.client.redeem("TEST"), (True, "redeemed"))

    def test_conflicting_redemption_response_is_unknown(self):
        r = Mock(status_code=200)
        self.client.s.post.return_value = r
        for payload in ({"success": False, "redemptionStatus": "redeemed"},
                        {"success": True, "reason": "invalid"},
                        {"success": 1, "redemptionStatus": "redeemed"},
                        {"success": True, "redemptionStatus": "invalid"}):
            with self.subTest(payload=payload):
                r.json.return_value = payload
                self.assertEqual(self.client.redeem("TEST"), (False, "http-200"))

    def test_already_redeemed_from_status(self):
        r = Mock(status_code=200)
        r.json.return_value = {"redemptionStatus":"already_redeemed"}
        self.client.s.post.return_value = r
        self.assertEqual(self.client.redeem("TEST"), (False, "already_redeemed"))

    def test_server_error_does_not_claim_rejection_or_success(self):
        r = Mock(status_code=503)
        r.json.return_value = {"reason":"invalid"}
        self.client.s.post.return_value = r
        self.assertEqual(self.client.redeem("TEST"), (False, "http-503"))

    def test_register_http_error_has_status(self):
        from musepy.client import MuseHTTPError
        self.client.s.post.return_value = Mock(status_code=403)
        with self.assertRaises(MuseHTTPError) as ctx:
            self.client.register_completion("1990-01-01", "A", "B")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_vm_page_error(self):
        self.client.s.get.return_value=Mock(status_code=403)
        with self.assertRaises(MuseError): self.client.wake_vm()
        self.client.s.post.assert_not_called()

    def test_no_vm_id(self):
        self.client.s.get.return_value=Mock(status_code=200,text="No VM")
        self.assertIsNone(self.client.wake_vm())
        self.client.s.post.assert_not_called()

    def test_vm_wake_error_not_reported_as_success(self):
        self.client.s.get.return_value=Mock(status_code=200,text='vmId:"12345678-1234-1234-1234-123456789abc"')
        self.client.s.post.return_value=Mock(status_code=503)
        with self.assertRaises(MuseError): self.client.wake_vm()

    def test_vm_wake_success(self):
        self.client.s.get.return_value=Mock(status_code=200,text='vmId:"12345678-1234-1234-1234-123456789abc"')
        self.client.s.post.return_value=Mock(status_code=200)
        self.assertEqual(self.client.wake_vm(),"12345678-1234-1234-1234-123456789abc")


if __name__ == "__main__": unittest.main()
