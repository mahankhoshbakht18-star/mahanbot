from otp_relay_client import OtpRelayClient, OtpRelayConfig


class FakeResponse:
    ok = True
    status_code = 200
    def json(self): return {"ok": True, "session_id": "session-1", "status": "WAITING"}


class FakeSession:
    def __init__(self): self.calls = []
    def request(self, method, url, **kwargs): self.calls.append((method, url, kwargs)); return FakeResponse()


def test_client_uses_v2_session_contract_and_bearer_auth():
    transport = FakeSession()
    client = OtpRelayClient(OtpRelayConfig(bot_token="secret"), transport)
    result = client.create_session(job_id="job-1", national_id="0000000000", purpose="register")
    assert result["session_id"] == "session-1"
    method, url, kwargs = transport.calls[0]
    assert method == "POST" and url.endswith("/api/v2/bot/sessions")
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert "otp" not in str(kwargs)
