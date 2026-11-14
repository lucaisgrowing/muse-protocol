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
