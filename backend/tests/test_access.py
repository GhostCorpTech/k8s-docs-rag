from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app import cli, main
from app.access import AccessError, AccessStore, FailureThrottle, hash_key
from app.config import Settings
from app.main import app
from tests.test_api import FakeCloud, FakeRetriever


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock) -> AccessStore:
    return AccessStore(tmp_path / "access.db", clock=clock)


# ---------------------------------------------------------------- store
def test_key_is_shown_once_and_only_its_hash_is_stored(store, tmp_path):
    key, secret = store.create("hh: Acme", days=30, daily_limit=20)
    assert secret.startswith("k8s_") and len(secret) == 28
    raw = (tmp_path / "access.db").read_bytes()
    assert secret.encode() not in raw
    assert hash_key(secret).encode() in raw
    assert store.authenticate(secret).id == key.id
    assert store.authenticate(f"  {secret} ").id == key.id


@pytest.mark.parametrize(
    ("secret", "code"), [(None, "key_required"), ("", "key_required"), ("k8s_nope", "invalid_key")]
)
def test_missing_or_unknown_key_is_refused(store, secret, code):
    with pytest.raises(AccessError) as caught:
        store.authenticate(secret)
    assert (caught.value.status, caught.value.code) == (401, code)


def test_expired_and_revoked_keys_are_refused(store, clock):
    expiring, expiring_secret = store.create("a", days=1, daily_limit=5)
    revoked, revoked_secret = store.create("b", days=30, daily_limit=5)
    store.revoke(revoked.id)
    clock.now += timedelta(days=1)
    with pytest.raises(AccessError, match="expired"):
        store.authenticate(expiring_secret)
    with pytest.raises(AccessError, match="revoked"):
        store.authenticate(revoked_secret)
    assert store.extend(expiring.id, days=7).state(clock.now) == "active"
    assert store.authenticate(expiring_secret).id == expiring.id
    with pytest.raises(ValueError, match="revoked"):
        store.extend(revoked.id, days=7)
    with pytest.raises(KeyError):
        store.revoke(999)


def test_per_key_quota_resets_at_utc_midnight(store, clock):
    key, _ = store.create("a", days=30, daily_limit=2)
    store.reserve(key, global_daily_limit=100)
    store.reserve(key, global_daily_limit=100)
    with pytest.raises(AccessError) as caught:
        store.reserve(key, global_daily_limit=100)
    assert (caught.value.status, caught.value.code) == (429, "key_quota")
    assert "00:00 UTC" in caught.value.message
    clock.now += timedelta(hours=12)  # next UTC day
    store.reserve(key, global_daily_limit=100)
    assert store.used_today(key.id) == 1
    assert store.get(key.id).total_questions == 3


def test_global_quota_caps_all_keys_together(store):
    first, _ = store.create("a", days=30, daily_limit=10)
    second, _ = store.create("b", days=30, daily_limit=10)
    store.reserve(first, global_daily_limit=3)
    store.reserve(first, global_daily_limit=3)
    store.reserve(second, global_daily_limit=3)
    with pytest.raises(AccessError) as caught:
        store.reserve(second, global_daily_limit=3)
    assert caught.value.code == "global_quota"


def test_refund_returns_a_failed_question(store):
    key, _ = store.create("a", days=30, daily_limit=1)
    day = store.reserve(key, global_daily_limit=10)
    store.refund(key, day)
    store.refund(key, day)  # never below zero
    assert store.used_today(key.id) == 0
    store.reserve(key, global_daily_limit=10)
    listed = dict((item.id, today) for item, today in store.list())
    assert listed == {key.id: 1}


def test_failure_throttle_blocks_after_limit():
    throttle = FailureThrottle(limit=3, window_seconds=600)
    for _ in range(3):
        throttle.check("1.2.3.4")
        throttle.failed("1.2.3.4")
    with pytest.raises(AccessError) as caught:
        throttle.check("1.2.3.4")
    assert caught.value.code == "too_many_attempts"
    throttle.check("5.6.7.8")  # other clients are unaffected


# ---------------------------------------------------------------- API
@pytest.fixture
def demo(monkeypatch, tmp_path, clock):
    settings = Settings(
        _env_file=None,
        demo_mode=True,
        access_db_path=tmp_path / "access.db",
        demo_key_daily_limit=2,
        demo_global_daily_limit=3,
        demo_contact_email="tech@example.org",
        demo_contact_telegram="https://t.me/example",
    )
    fake = FakeCloud()
    store = AccessStore(settings.access_db_path, clock=clock)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "_access_store", lambda path: store)
    monkeypatch.setattr(main, "THROTTLE", FailureThrottle(limit=3))
    monkeypatch.setattr(main, "_require_index", lambda *args: None)
    monkeypatch.setattr(main, "_live_models", lambda *args: fake)
    monkeypatch.setattr(main, "Retriever", FakeRetriever)
    return store, fake


def _auth(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def test_demo_info_is_public():
    with TestClient(app) as client:
        info = client.get("/api/demo").json()
    assert info["enabled"] is False


def test_demo_mode_requires_a_key_for_paid_endpoints(demo):
    with TestClient(app) as client:
        assert client.get("/api/demo").json()["contact"]["email"] == "tech@example.org"
        for path, body in (
            ("/api/chat", {"question": "Что такое Pod?"}),
            ("/api/chat/stream", {"question": "Что такое Pod?"}),
            ("/api/search", {"query": "pod"}),
        ):
            response = client.post(path, json=body)
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "key_required"
            assert response.headers["www-authenticate"] == "Bearer"
        # Read-only pages stay public: they cost nothing.
        assert client.get("/api/eval/latest").status_code == 200
        assert client.get("/health").status_code == 200


def test_valid_key_answers_and_counts_quota(demo):
    store, _ = demo
    _, secret = store.create("tester", days=30, daily_limit=2)
    with TestClient(app) as client:
        session = client.get("/api/demo/session", headers=_auth(secret)).json()
        assert (session["daily_limit"], session["remaining_today"]) == (2, 2)
        assert "label" not in session
        ok = client.post("/api/chat", json={"question": "Что такое Pod?"}, headers=_auth(secret))
        assert ok.status_code == 200
        stream = client.post(
            "/api/chat/stream", json={"question": "Что такое Pod?"}, headers=_auth(secret)
        )
        assert "event: done" in stream.text
        limited = client.post(
            "/api/chat/stream", json={"question": "Что такое Pod?"}, headers=_auth(secret)
        )
        assert limited.status_code == 429
        assert limited.json()["detail"]["code"] == "key_quota"
        session = client.get("/api/demo/session", headers=_auth(secret)).json()
        assert session["remaining_today"] == 0


def test_failed_answer_is_refunded(demo):
    store, fake = demo
    key, secret = store.create("tester", days=30, daily_limit=2)
    fake.fail_stream = True
    with TestClient(app) as client:
        body = client.post(
            "/api/chat/stream", json={"question": "Что такое Pod?"}, headers=_auth(secret)
        ).text
    assert "event: error" in body
    assert store.used_today(key.id) == 0
    assert fake.log_context["access_key_id"] == key.id


def test_revoked_key_stops_working_immediately(demo):
    store, _ = demo
    key, secret = store.create("tester", days=30, daily_limit=2)
    with TestClient(app) as client:
        assert client.get("/api/demo/session", headers=_auth(secret)).status_code == 200
        store.revoke(key.id)
        response = client.get("/api/demo/session", headers=_auth(secret))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "key_revoked"


def test_invalid_keys_are_throttled_per_client(demo):
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/api/demo/session", headers=_auth("k8s_bad")).status_code == 401
        blocked = client.get("/api/demo/session", headers=_auth("k8s_bad"))
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "too_many_attempts"
        other = client.get(
            "/api/demo/session", headers={**_auth("k8s_bad"), "X-Real-IP": "203.0.113.9"}
        )
        assert other.status_code == 401


# ---------------------------------------------------------------- CLI
def test_cli_key_lifecycle(monkeypatch, tmp_path):
    settings = Settings(
        _env_file=None, access_db_path=tmp_path / "access.db", demo_public_url="https://demo.test/"
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    runner = CliRunner()
    created = runner.invoke(cli.app, ["create-key", "--label", "hh: Acme", "--days", "7"])
    assert created.exit_code == 0, created.output
    assert "https://demo.test/#/?key=k8s_" in created.output
    listed = runner.invoke(cli.app, ["list-keys"])
    assert "hh: Acme" in listed.output and "active" in listed.output and "0/20" in listed.output
    assert runner.invoke(cli.app, ["revoke-key", "1"]).exit_code == 0
    assert "revoked" in runner.invoke(cli.app, ["list-keys"]).output
    assert runner.invoke(cli.app, ["extend-key", "1"]).exit_code != 0
    assert runner.invoke(cli.app, ["revoke-key", "42"]).exit_code != 0
