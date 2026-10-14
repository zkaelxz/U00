"""Backoff timing and which provider errors move the chain to the next engine."""
from types import SimpleNamespace

import pytest

from engine_backends import shared
from engine_backends.fallback import is_fallback_error, is_transient_fallback_error


class _HttpError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"http {status}")
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(shared, "_cancellable_sleep", waits.append)
    monkeypatch.setattr(shared, "charge_rate_limit_wait", lambda s: None)
    monkeypatch.setattr(shared, "_jitter", lambda: 1.0)
    return waits


def test_no_sleep_after_the_final_rate_limited_attempt(slept):
    def always_429():
        raise _HttpError(429)

    with pytest.raises(_HttpError):
        shared.call_with_backoff(always_429, max_retries=5)
    assert slept == [2, 4, 8, 16]


def test_retry_after_longer_than_the_backoff_is_honoured_up_to_the_cap(slept):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise _HttpError(429, {"retry-after": "30"})
        if len(calls) == 2:
            raise _HttpError(429, {"retry-after": "9999"})
        return "ok"

    assert shared.call_with_backoff(flaky, max_delay=60.0) == "ok"
    assert slept == [30, 60]


@pytest.mark.parametrize("status", [500, 502, 504])
def test_a_provider_outage_switches_engines_and_is_worth_retrying(status):
    err = _HttpError(status)
    assert is_fallback_error(err) and is_transient_fallback_error(err)


def test_501_and_a_plain_400_do_not_switch_engines():
    assert not is_fallback_error(_HttpError(501))
    assert not is_fallback_error(_HttpError(400))


def test_backoff_waits_are_jittered_up_to_a_quarter_longer(monkeypatch):
    waits = []
    monkeypatch.setattr(shared, "_cancellable_sleep", waits.append)
    monkeypatch.setattr(shared, "charge_rate_limit_wait", lambda s: None)

    def always_429():
        raise _HttpError(429)

    for _ in range(20):
        with pytest.raises(_HttpError):
            shared.call_with_backoff(always_429, max_retries=2)
    firsts = waits[::1]
    assert all(2 <= w <= 2.5 for w in firsts) and len(set(firsts)) > 1
