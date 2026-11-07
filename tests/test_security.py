import unittest

from backend.app.security import mutation_is_authorized


class MutationAuthorizationTests(unittest.TestCase):
    def test_reads_are_always_allowed(self):
        self.assertTrue(self._allowed(method="GET", host="203.0.113.7"))

    def test_loopback_write_is_allowed_without_configured_token(self):
        self.assertTrue(self._allowed(host="127.0.0.1"))
        self.assertTrue(self._allowed(host="::1"))

    def test_remote_write_is_denied_without_configured_token(self):
        self.assertFalse(self._allowed(host="203.0.113.7"))

    def test_configured_token_is_required_even_on_loopback(self):
        self.assertFalse(self._allowed(host="127.0.0.1", token="secret"))
        self.assertTrue(
            self._allowed(
                host="203.0.113.7",
                token="secret",
                headers={"authorization": "Bearer secret"},
            )
        )

    def _allowed(
        self,
        *,
        method="POST",
        host="127.0.0.1",
        token="",
        headers=None,
    ):
        return mutation_is_authorized(
            method=method,
            path="/api/data/reload",
            client_host=host,
            headers=headers or {},
            configured_token=token,
        )
