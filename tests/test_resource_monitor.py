"""Resource alerts (services/resource_monitor_service.py): the rule engine, the stored rules and the readers, with nvidia-smi and the clock faked."""
import pytest

from services import resource_monitor_service as rms

MIN = 60.0
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    rms.reset_for_tests()
    yield
    rms.reset_for_tests()


def _feed(name, values, rule, start=T0, every=30.0, state=None):
    """Steps one rule through `values`, one reading per `every` seconds; returns the events."""
    state = state or {"alerting": False}
    events = []
    for i, value in enumerate(values):
        state, event = rms.step(name, rule, state, value, start + i * every)
        if event:
            events.append((i, event["kind"]))
    return events, state


RULE = {"enabled": True, "limit": 85, "minutes": 5}


def test_a_short_spike_does_not_alert():
    events, _ = _feed("gpu_temp", [90] * 9 + [70] * 5, RULE)   # 4 min over, then back
    assert events == []


def test_alerts_once_after_the_time_and_not_again_before_an_hour():
    events, state = _feed("gpu_temp", [90] * 100, RULE)        # 50 min
    assert events == [(10, "alert")]
    assert state["alerting"]


def test_repeats_hourly_while_it_lasts():
    events, _ = _feed("gpu_temp", [90] * 250, RULE)            # ~2 h
    assert [k for _, k in events] == ["alert", "repeat"]


def test_recovers_only_after_staying_clear_by_the_margin():
    hot = [90] * 11
    hovering = [84, 90, 84, 90] * 5          # under the limit but inside the margin: never recovers
    events, state = _feed("gpu_temp", hot + hovering, RULE)
    assert [k for _, k in events] == ["alert"] and state["alerting"]
    events, state = _feed("gpu_temp", hot + [70] * 8, RULE)    # 4 min clear
    assert [k for _, k in events] == ["alert", "recovered"] and not state["alerting"]


def test_a_short_dip_does_not_recover():
    events, state = _feed("gpu_temp", [90] * 11 + [70] * 3 + [90] * 3, RULE)
    assert [k for _, k in events] == ["alert"] and state["alerting"]


def test_unreadable_values_change_nothing():
    events, state = _feed("gpu_temp", [90] * 6 + [None] * 50 + [90] * 6, RULE)
    assert [k for _, k in events] == ["alert"]


def test_low_rule_alerts_below_the_limit():
    rule = {"enabled": True, "limit": 10, "minutes": 5}
    events, state = _feed("disk_free", [4.0] * 12 + [20.0] * 6, rule)
    assert [k for _, k in events] == ["alert", "recovered"]


def test_a_disabled_rule_never_alerts_and_ends_a_running_alert():
    events, _ = _feed("gpu_temp", [99] * 50, {**RULE, "enabled": False})
    assert events == []
    _, state = _feed("gpu_temp", [90] * 11, RULE)
    state, event = rms.step("gpu_temp", {**RULE, "enabled": False}, state, 90, T0 + 999)
    assert event is None and not state["alerting"]


def test_messages_state_value_limit_and_time():
    state = {"alerting": False}
    event = None
    for i in range(11):
        state, event = rms.step("gpu_temp", RULE, state, 88, T0 + i * 30)
    assert event["text"] == "GPU temperature is 88 °C, at or above 85 °C for 5 min."
    rule = {"enabled": True, "limit": 10, "minutes": 1}
    state, _ = rms.step("disk_free", rule, {"alerting": False}, 4.5, T0)
    _, event = rms.step("disk_free", rule, state, 4.5, T0 + 61)
    assert event["text"] == "Free disk space is 4.5 GB, under 10.0 GB for 1 min."


def test_defaults_match_what_was_agreed(isolated_db):
    rules = rms.get_rules()
    assert rules["ram"]["limit"] == 95 and rules["gpu_memory"]["limit"] == 100
    assert rules["gpu_memory"]["enabled"] is False and rules["cpu"]["enabled"] is False
    assert rules["gpu_temp"] == {"enabled": True, "limit": 85, "minutes": 5}


def test_stored_rules_override_and_bad_values_fall_back(isolated_db):
    import db
    db.set_app_setting(rms.RULES_SETTING, {"gpu_temp": {"limit": 80, "minutes": "x", "enabled": False},
                                           "ram": "junk", "cpu": {"enabled": True, "limit": 500}})
    rules = rms.get_rules()
    assert rules["gpu_temp"] == {"enabled": False, "limit": 80, "minutes": 5}
    assert rules["ram"]["limit"] == 95
    assert rules["cpu"] == {"enabled": True, "limit": 95, "minutes": 30}


def test_run_cycle_notifies_saves_state_and_survives_a_restart(isolated_db):
    got = []
    hot = {"gpu_temp": 90}
    for i in range(11):
        rms.run_cycle(notify=got.append, now=T0 + i * 30, readings=hot)
    assert [e["kind"] for e in got] == ["alert"]
    rms.reset_for_tests()                                       # a restart
    rms.run_cycle(notify=got.append, now=T0 + 400, readings=hot)
    assert [e["kind"] for e in got] == ["alert"]                # not announced again
    for i in range(6):
        rms.run_cycle(notify=got.append, now=T0 + 500 + i * 30, readings={"gpu_temp": 60})
    assert [e["kind"] for e in got] == ["alert", "recovered"]


def test_a_failing_notify_does_not_raise(isolated_db):
    def boom(event):
        raise RuntimeError("down")
    for i in range(11):
        rms.run_cycle(notify=boom, now=T0 + i * 30, readings={"gpu_temp": 90})


class _Run:
    def __init__(self, out, code=0):
        self.stdout, self.returncode, self.timed_out = out, code, False


def _fake_smi(monkeypatch, answers):
    monkeypatch.setattr(rms.shutil, "which", lambda name: "nvidia-smi")

    def fake(cmd, timeout):
        field = cmd[1].split("=", 1)[1]
        return answers.get(field, _Run("", 2))
    monkeypatch.setattr(rms, "run_captured", fake)


def test_gpu_reader_takes_the_worst_card_and_rounds_memory(monkeypatch):
    _fake_smi(monkeypatch, {
        "temperature.gpu,memory.used,memory.total": _Run("61, 100, 32000\n83, 31950, 32000\n"),
        "clocks_throttle_reasons.active": _Run("0x0000000000000000\n0x0000000000000008\n")})
    assert rms._read_gpu() == {"gpu_temp": 83.0, "gpu_memory": 100, "gpu_throttle": 1.0}


def test_the_routine_power_cap_is_not_a_throttle(monkeypatch):
    _fake_smi(monkeypatch, {
        "temperature.gpu,memory.used,memory.total": _Run("70, 1, 100\n"),
        "clocks_throttle_reasons.active": _Run("0x0000000000000004\n")})
    assert rms._read_gpu()["gpu_throttle"] == 0.0


def test_gpu_reader_uses_the_newer_field_name_and_copes_with_na(monkeypatch):
    _fake_smi(monkeypatch, {
        "temperature.gpu,memory.used,memory.total": _Run("[N/A], [N/A], [N/A]\n"),
        "clocks_event_reasons.active": _Run("0x0000000000000040\n")})
    assert rms._read_gpu() == {"gpu_temp": None, "gpu_memory": None, "gpu_throttle": 1.0}


def test_gpu_reader_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr(rms.shutil, "which", lambda name: None)
    assert rms._read_gpu() == {"gpu_temp": None, "gpu_memory": None, "gpu_throttle": None}


def test_cpu_percent_comes_from_the_change_between_samples(monkeypatch):
    seq = iter([(100, 1000), (150, 1100)])
    monkeypatch.setattr(rms, "_cpu_times", lambda: next(seq))
    monkeypatch.setattr(rms, "_read_gpu", lambda: {})
    monkeypatch.setattr(rms, "_read_ram_percent", lambda: 10)
    monkeypatch.setattr(rms, "_disk_free_gb", lambda: 50.0)
    assert rms.sample()["cpu"] is None
    assert rms.sample()["cpu"] == 50


# --- the bell and the push channels --------------------------------------------

@pytest.fixture
def notify_env(tmp_path, monkeypatch, isolated_db):
    from services import notification_service as ns
    from services import settings_service
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    for name in (ns.DISABLED_ENV, *ns.ENV_VARS["discord"], *ns.ENV_VARS["ntfy"]):
        monkeypatch.delenv(name, raising=False)

    class _Timer:
        def cancel(self):
            pass
    monkeypatch.setattr(ns, "_schedule_flush", lambda *_: _Timer())
    ns.reset_for_tests()
    yield ns, path
    ns.reset_for_tests()


_DISCORD = ("BAIHE_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/"
            "123456789012345678/SECRETtokenABCDEFGHIJKLMNOPQRSTUVWXYZ_-0123\n")


def test_a_resource_alert_reaches_the_bell_and_always_pushes(notify_env):
    ns, env = notify_env
    env.write_text(_DISCORD)
    ns.set_categories(jobs=False, chapters=False, remote=False)   # no category can switch it off
    ns.notify_resource_alert("GPU temperature is 88 °C, at or above 85 °C for 5 min.")
    text = "This PC: GPU temperature is 88 °C, at or above 85 °C for 5 min."
    assert ns._pending == [("resource", text, "resource")]
    assert [(e["kind"], e["text"]) for e in ns.list_recent(None)] == [("resource", text)]
    member = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    assert ns.list_recent(member) == []   # PC owner and admins only


def test_a_resource_alert_without_a_channel_stays_in_the_bell(notify_env):
    ns, _ = notify_env
    ns.notify_resource_alert("RAM use is back to normal (60%).")
    assert ns._pending == [] and len(ns.list_recent(None)) == 1


def test_a_resource_alert_is_never_folded_into_a_job_count(notify_env):
    ns, env = notify_env
    env.write_text(_DISCORD)
    ns.notify_resource_alert("Free disk space is 4.5 GB, under 10.0 GB for 5 min.")
    assert not ns._folds_into_count(ns._pending[0])


def test_the_default_notify_logs_and_reaches_the_bell(notify_env):
    ns, _ = notify_env
    for i in range(11):
        rms.run_cycle(now=T0 + i * 30, readings={"gpu_temp": 90})
    kinds = [(e["kind"], e["text"]) for e in ns.list_recent(None)]
    assert kinds == [("resource", "This PC: GPU temperature is 90 °C, at or above 85 °C for 5 min.")]
