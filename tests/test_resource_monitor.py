"""Resource alerts (services/resource_monitor_service.py): the rule engine, the stored rules and the readers, with nvidia-smi and the clock faked."""
import pytest

import env_reader
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
    assert rules["gpu_memory_warn"] == {"enabled": True, "limit": 85, "minutes": 15}
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
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(env_reader, "default_env_path", lambda: str(path))
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
    assert ns._pending == [("resource", text, "resource", None, {"description": text, "color": ns.COLOR_WARN})]
    assert [(e["kind"], e["text"]) for e in ns.list_recent(None)] == [("resource", text)]
    member = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    assert ns.list_recent(member) == []   # PC owner and admins only


def test_a_recovery_card_is_green(notify_env):
    ns, env = notify_env
    env.write_text(_DISCORD)
    ns.notify_resource_alert("RAM use is back to normal (60%).", recovered=True)
    assert ns._pending[0][4]["color"] == ns.COLOR_OK
    assert ns._embed_for(ns._pending, ns._pending[0][1])["color"] == ns.COLOR_OK


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


def test_the_nearly_full_rule_alerts_at_85_while_the_full_rule_stays_quiet():
    warn = {"enabled": True, "limit": 85, "minutes": 15}
    events, _ = _feed("gpu_memory_warn", [88] * 40, warn)       # 20 min
    assert events == [(30, "alert")]
    full = {"enabled": False, "limit": 100, "minutes": 15}
    assert _feed("gpu_memory", [88] * 40, full)[0] == []


def test_both_gpu_memory_rules_read_the_same_sample(monkeypatch):
    monkeypatch.setattr(rms, "_read_gpu", lambda: {"gpu_memory": 91})
    monkeypatch.setattr(rms, "_read_ram_percent", lambda: 10)
    monkeypatch.setattr(rms, "_disk_free_gb", lambda: 50.0)
    monkeypatch.setattr(rms, "_cpu_times", lambda: None)
    readings = rms.sample()
    assert readings["gpu_memory"] == readings["gpu_memory_warn"] == 91


def test_the_snapshot_before_any_sample_has_every_rule_and_no_values(isolated_db):
    snap = rms.snapshot(now=T0)
    assert snap["sampled_seconds_ago"] is None
    assert [m["name"] for m in snap["metrics"]] == list(rms.METRICS)
    assert all(m["value"] is None and not m["alerting"] and m["past_seconds"] is None
               for m in snap["metrics"])


def test_the_snapshot_shows_the_last_cycle_without_sampling_again(isolated_db, monkeypatch):
    for i in range(11):
        rms.run_cycle(notify=lambda e: None, now=T0 + i * 30, readings={"gpu_temp": 90, "ram": 50})
    monkeypatch.setattr(rms, "sample", lambda: pytest.fail("the snapshot sampled"))
    by = {m["name"]: m for m in rms.snapshot(now=T0 + 330)["metrics"]}
    assert by["gpu_temp"]["value"] == 90 and by["gpu_temp"]["alerting"]
    assert by["gpu_temp"]["past_seconds"] == 330 and by["gpu_temp"]["unit"] == "°C"
    assert by["ram"]["value"] == 50 and by["ram"]["past_seconds"] is None
    assert by["disk_free"]["low"] and by["gpu_throttle"]["limit"] is None
    assert by["cpu"]["enabled"] is False


def test_the_snapshot_after_a_restart_still_times_a_running_alert(isolated_db):
    for i in range(11):
        rms.run_cycle(notify=lambda e: None, now=T0 + i * 30, readings={"gpu_temp": 90})
    rms.reset_for_tests()
    rms.run_cycle(notify=lambda e: None, now=T0 + 600, readings={"gpu_temp": 90})
    gpu = next(m for m in rms.snapshot(now=T0 + 600)["metrics"] if m["name"] == "gpu_temp")
    assert gpu["alerting"] and gpu["past_seconds"] == 600


def test_the_resources_route_returns_the_snapshot(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.api_config import ApiSettings
    from api.server import create_app
    rms.run_cycle(notify=lambda e: None, now=T0, readings={"ram": 42})
    client = TestClient(create_app(ApiSettings(serve_frontend=False)), base_url="http://127.0.0.1:8600",
                        client=("127.0.0.1", 5000))
    r = client.get("/api/diagnostics/resources")
    assert r.status_code == 200
    by = {m["name"]: m for m in r.json()["metrics"]}
    assert by["ram"]["value"] == 42 and r.json()["sample_seconds"] == 30


# --- the PC alerts card ---------------------------------------------------------

def test_describe_rules_lists_the_seven_alerts_with_ranges_and_defaults(isolated_db):
    rows = {r["id"]: r for r in rms.describe_rules()}
    assert list(rows) == ["gpu_temp", "gpu_throttle", "ram", "disk_free", "gpu_memory_warn", "gpu_memory", "cpu"]
    assert rows["gpu_throttle"]["has_limit"] is False and rows["gpu_throttle"]["limit"] is None
    assert rows["disk_free"]["below"] is True and rows["disk_free"]["unit"] == "GB"
    assert rows["gpu_memory"]["enabled"] is False and rows["gpu_memory"]["default_limit"] == 100
    assert (rows["gpu_temp"]["limit_min"], rows["gpu_temp"]["limit_max"]) == (40, 120)


def test_set_rules_saves_only_the_touched_alerts_and_the_monitor_reads_them(isolated_db):
    rows = {r["id"]: r for r in rms.set_rules({"gpu_memory": {"enabled": True},
                                               "ram": {"limit": 90, "minutes": 3}})}
    assert rows["gpu_memory"]["enabled"] is True and rows["gpu_memory"]["limit"] == 100
    assert rms.get_rules()["ram"] == {"enabled": True, "limit": 90, "minutes": 3}
    import db
    assert set(db.get_app_setting(rms.RULES_SETTING)) == {"gpu_memory", "ram"}


@pytest.mark.parametrize("changes", [
    {"nope": {"enabled": True}},
    {"ram": {"limit": 20}},
    {"ram": {"limit": True}},
    {"ram": {"minutes": 0}},
    {"gpu_throttle": {"limit": 5}},
])
def test_set_rules_refuses_bad_input_and_saves_nothing(isolated_db, changes):
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        rms.set_rules({"cpu": {"enabled": True}, **changes})
    assert rms.get_rules()["cpu"]["enabled"] is False



def _low_disk_events(n=12):
    got = []
    for i in range(n):
        rms.run_cycle(notify=got.append, now=T0 + i * 30, readings={"disk_free": 4.0})
    return got


def test_a_low_disk_alert_says_what_a_cleanup_would_free(isolated_db, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "storage_scan", lambda: {"would_free_bytes": 3 * 1024 ** 3})
    [event] = _low_disk_events()
    assert event["metric"] == "disk_free" and event["text"].endswith(
        "A cleanup in Library tools > Storage would free about 3.0 GB.")
    assert "/" not in event["text"].replace("Library tools > Storage", "")


@pytest.mark.parametrize("scan", ["little", "fails", "breaks"])
def test_a_low_disk_alert_names_no_figure_when_there_is_none_worth_naming(isolated_db, monkeypatch, scan):
    from services import library_admin_service

    def scanned():
        if scan == "fails":
            raise OSError("D:\\Baihe is gone")
        if scan == "breaks":
            raise KeyError("Baihe")       # anything a scan can hit must not cost the alert
        return {"would_free_bytes": 1024}
    monkeypatch.setattr(library_admin_service, "storage_scan", scanned)
    [event] = _low_disk_events()
    assert "cleanup" not in event["text"] and "Baihe" not in event["text"]


def test_other_alerts_do_not_scan_the_library(isolated_db, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "storage_scan", lambda: pytest.fail("scanned"))
    for i in range(11):
        rms.run_cycle(notify=lambda e: None, now=T0 + i * 30, readings={"gpu_temp": 90})
