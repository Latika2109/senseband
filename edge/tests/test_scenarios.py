"""Milestone 1 acceptance: behaviour of the whole edge pipeline on simulator scenarios."""
from __future__ import annotations

import pytest
from senseband_edge.alerts.sms import MockSms

from .harness import kinds, make_settings, run_scenario


def test_fall_sends_exactly_one_sms_after_30s():
    r = run_scenario("fall")
    falls = [a for a in r.engine.alerts.alerts if a.kind == "fall"]
    assert len(falls) == 1
    a = falls[0]
    assert a.level == "critical"
    assert a.sms_due_t == pytest.approx(a.t + 30.0, abs=0.25)      # 30 s "I'm OK" window
    assert len(r.sms.delivered) == 1                               # one SMS (one contact)
    assert "fall" in r.sms.delivered[0].text.lower()
    assert len(r.sms.delivered[0].text) <= 160
    assert a.status == "sms_sent" and a.sms_sent == 1
    assert a.t == pytest.approx(40.5 + 10.3, abs=1.5)              # fall at 40.5 s + still_s
    # buzzer sounded during the countdown and stopped afterwards
    assert ("buzzer", True) in r.io.log and r.io.buzzer_on is False


def test_fall_cancelled_by_im_ok_sends_no_sms():
    state = {"done": False}

    def press(eng, f):
        pend = eng.alerts.pending_countdown
        if pend and not state["done"] and eng.now >= pend[0].t + 12:   # user answers after 12 s
            assert eng.i_am_ok("button")
            state["done"] = True

    r = run_scenario("fall", on_frame=press)
    a = next(a for a in r.engine.alerts.alerts if a.kind == "fall")
    assert a.status == "cancelled" and a.acknowledged_by == "button"
    assert r.sms.log == []                                         # nothing sent
    row = r.store.get("events", a.id)
    assert row["status"] == "cancelled" and row["cancelled_ts"] and row["sms_sent"] == 0
    assert r.io.buzzer_on is False


def test_sms_retries_three_times_then_succeeds():
    sms = MockSms(fail_first=2)
    r = run_scenario("fall", sms=sms)
    assert [x.ok for x in sms.log] == [False, False, True]
    a = next(a for a in r.engine.alerts.alerts if a.kind == "fall")
    assert a.status == "sms_sent"


def test_sms_all_attempts_fail_is_recorded_not_fatal():
    sms = MockSms(fail_first=99)
    r = run_scenario("fall", sms=sms)
    assert len(sms.log) == 3                                       # retries = 3
    a = next(a for a in r.engine.alerts.alerts if a.kind == "fall")
    assert a.status == "sms_failed"
    assert r.store.get("events", a.id)["status"] == "sms_failed"


def test_multiple_contacts_each_get_one_sms():
    cfg = make_settings(sms={"contacts": ["+911", "+912", "+913"]})
    r = run_scenario("fall", settings=cfg)
    assert sorted(x.number for x in r.sms.delivered) == ["+911", "+912", "+913"]


@pytest.mark.slow
def test_jogging_produces_no_heart_rate_alert():
    r = run_scenario("jogging")
    assert not [k for k in kinds(r) if k.startswith("hr_")], kinds(r)
    assert kinds(r, "critical") == [] and kinds(r, "warning") == []
    ctx = {v["context"] for v in r.vitals}
    assert {"running", "sitting", "walking"} <= ctx
    run_hr = [v["hr"] for v in r.vitals if v["context"] == "running" and v["hr"]]
    assert run_hr and 140 < sum(run_hr) / len(run_hr) < 165
    assert r.sms.log == []


def test_resting_tachycardia_warns_without_sms():
    r = run_scenario("resting_tachycardia")
    w = [a for a in r.engine.alerts.alerts if a.kind == "hr_high_rest"]
    assert len(w) == 1 and w[0].level == "warning"
    assert 140 - 5 < w[0].context["hr"] < 140 + 5
    assert w[0].t > 120 + 30                    # not before the sustained period
    assert r.sms.log == []                      # warnings notify the app only
    assert kinds(r, "critical") == []


def test_resting_has_no_alerts():
    r = run_scenario("resting")
    assert r.engine.alerts.alerts == []
    v = r.vitals[-1]
    assert v["context"] == "sitting" and v["hr_src"] == "ecg" and abs(v["hr"] - 72) < 4
    assert v["quality"] == {"ecg": "good", "ppg": "good"} and v["rhythm"] == "regular"
    assert abs(v["spo2"] - 97) < 3 and v["status"] == "normal"
    assert 60 < v["hr_expected"] < 85


def test_lead_off_gives_check_sensor_never_vital_alert():
    r = run_scenario("lead_off")
    ks = kinds(r)
    assert "check_sensor_ecg" in ks and "check_sensor_ppg" in ks
    assert all(k.startswith("check_sensor") for k in ks), ks
    assert r.sms.log == []
    off = [v for v in r.vitals if 70 < (v["ts"] - 1_700_000_000_000) / 1000 < 118]
    assert off and all(v["quality"]["ecg"] == "lead_off" and v["hr_src"] != "ecg" for v in off)


def test_noisy_ecg_gives_check_sensor_and_no_rhythm_alert():
    r = run_scenario("noisy_ecg")
    assert "check_sensor_ecg" in kinds(r)
    assert "irregular_rhythm" not in kinds(r)
    assert not [k for k in kinds(r) if k != "check_sensor_ecg"]


def test_atrial_fibrillation_is_a_careful_warning():
    r = run_scenario("atrial_fibrillation")
    a = [a for a in r.engine.alerts.alerts if a.kind == "irregular_rhythm"]
    assert len(a) == 1 and a[0].level == "warning"
    assert a[0].t > 60                                              # not in the normal part
    assert "doctor" in a[0].message and "not a diagnosis" in a[0].message
    assert "you have" not in a[0].message.lower()
    assert r.sms.log == []


def test_low_spo2_warns_then_critical_sms():
    r = run_scenario("low_spo2")
    ks = kinds(r)
    assert "spo2_low" in ks and "spo2_critical" in ks
    crit = next(a for a in r.engine.alerts.alerts if a.kind == "spo2_critical")
    assert crit.level == "critical" and crit.status == "sms_sent"
    assert len(r.sms.delivered) == 1


def test_sleep_desaturations_warn_but_do_not_page_anyone():
    r = run_scenario("sleeping")
    assert "sleep_desats" in kinds(r)
    assert kinds(r, "critical") == []
    assert r.sms.log == []
    ctx = {v["context"] for v in r.vitals}
    assert "sleeping" in ctx


def test_collapse_critical_no_pulse_sms():
    r = run_scenario("collapse")
    c = [a for a in r.engine.alerts.alerts if a.kind == "collapse"]
    assert len(c) == 1 and c[0].level == "critical"
    assert len(r.sms.delivered) == 1 and "collapse" in r.sms.delivered[0].text.lower()
    assert not [k for k in kinds(r) if k.startswith("hr_")]       # no vital alert before


def test_exercise_scenarios_are_quiet():
    for name in ("elbow_exercise", "knee_exercise", "grip_test"):
        r = run_scenario(name)
        assert r.engine.alerts.alerts == [], (name, kinds(r))


def test_sos_sends_immediately_and_cannot_be_cancelled():
    r = run_scenario("resting", 20)
    eng = r.engine
    a = eng.sos()
    assert a and a.status == "sms_sent" and len(r.sms.delivered) == 1
    assert eng.i_am_ok() == []


def test_alert_dedup_within_cooldown():
    r = run_scenario("resting", 10)
    eng = r.engine
    assert eng.raise_alert("x", "warning", "m", 10.0)
    assert eng.raise_alert("x", "warning", "m", 20.0) is None
    assert eng.raise_alert("x", "warning", "m", 700.0)


def test_events_and_ecg_strips_persisted_and_queued_for_sync():
    r = run_scenario("fall")
    ev = r.store.list("events")
    assert len(ev) == 1 and ev[0]["kind"] == "fall" and ev[0]["sms_sent"] == 1
    strips = r.store.list("ecg_strips")
    assert len(strips) == 1 and strips[0]["event_id"] == ev[0]["id"]
    assert len(strips[0]["samples"]) == 4 * 250 * 30            # float32, 30 s at 250 Hz
    outbox_tables = {p["table"] for p in r.store.pending(1000)}
    assert {"events", "ecg_strips", "vitals_minute"} <= outbox_tables
    mins = r.store.list("vitals_minute", order="minute_ts")
    assert len(mins) >= 1 and mins[0]["activity"] in ("standing", "walking", "running")
