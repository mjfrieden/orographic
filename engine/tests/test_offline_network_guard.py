"""The normal test boundary must fail locally before an outbound request."""
import socket

import pytest
import requests
from curl_cffi import requests as curl_requests


@pytest.mark.parametrize("operation", [
    lambda: socket.create_connection(("synthetic.invalid", 443)),
    lambda: requests.get("https://synthetic.invalid"),
    lambda: curl_requests.Session().get("https://synthetic.invalid"),
])
def test_unmocked_network_fails_before_io(operation):
    with pytest.raises(pytest.fail.Exception, match="Offline regression attempted"):
        operation()


def test_urllib_transport_is_guarded():
    # Import after the fixture patches the transport, rather than retaining an
    # old function alias. A retained alias is still covered by socket guards.
    import urllib.request
    with pytest.raises(pytest.fail.Exception, match="Offline regression attempted"):
        urllib.request.urlopen("https://synthetic.invalid")
