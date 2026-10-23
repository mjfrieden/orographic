"""Regression tests are offline; live provider calls require a separate workflow."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def deny_unmocked_network():
    def blocked(*args, **kwargs):
        # pytest.fail escapes broad `except Exception` provider fallback logic.
        # Do not include arguments: they could contain credential-bearing URLs.
        pytest.fail("Offline regression attempted an unmocked network request", pytrace=False)

    with (
        patch("socket.socket.connect", side_effect=blocked),
        patch("socket.socket.connect_ex", side_effect=blocked),
        patch("socket.create_connection", side_effect=blocked),
        patch("urllib.request.urlopen", side_effect=blocked),
        patch("requests.sessions.Session.request", side_effect=blocked),
        patch("curl_cffi.requests.Session.request", side_effect=blocked),
    ):
        yield
