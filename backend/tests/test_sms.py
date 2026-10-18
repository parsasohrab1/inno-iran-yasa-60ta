"""SMS channel tests. A mock HTTP transport is used everywhere: nothing here can contact a real gateway."""
import os

import httpx
import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")

from app.plant.engine import PlantEngine  # noqa: E402
from app.plant.notify import Contact, LogChannel, Notifier  # noqa: E402
from app.plant.sms import HttpSmsChannel, clean_text, normalize_mobile  # noqa: E402

SECRET = "TEST-KEY-123456"
PHONE = "09123456789"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("SMS_API_KEY", SECRET)


def channel(handler, **kw):
    return HttpSmsChannel(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def contact(sms=PHONE):
    return Contact("c1", "مدیر تولید (نمونه)", "production_manager", None, {"sms": sms} if sms else {})


def test_normalize_mobile_variants():
    for raw in ("09123456789", "+989123456789", "00989123456789", "989123456789", "9123456789", "0912 345 6789",
                "۰۹۱۲۳۴۵۶۷۸۹", "0912-345-6789"):
        assert normalize_mobile(raw) == PHONE, raw
    for bad in (None, "", "02112345678", "0812345678", "abc", "0912345678"):
        assert normalize_mobile(bad) is None, bad


def test_clean_text_strips_emoji_collapses_space_and_caps_length():
    assert clean_text("🔴 بحرانی:   دما\n بالا ⚠️") == "بحرانی: دما بالا ⚠"
    long = clean_text("الف" * 500)
    assert len(long) == 210 and long.endswith("…")


def test_kavenegar_success_builds_the_documented_request():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["params"] = str(req.url), dict(req.url.params)
        return httpx.Response(200, json={"return": {"status": 200, "message": "ok"}})

    ok, detail = channel(handler, sender="10004346").send(contact(), "s", "🔴 دمای پرس از حد بحرانی گذشت", "critical")
    assert ok and detail == "kavenegar status 200"
    assert seen["url"].startswith(f"https://api.kavenegar.com/v1/{SECRET}/sms/send.json")
    assert seen["params"] == {"receptor": PHONE, "message": "دمای پرس از حد بحرانی گذشت", "sender": "10004346"}


def test_gateway_error_is_reported_as_failure():
    ok, detail = channel(lambda r: httpx.Response(200, json={"return": {"status": 418, "message": "no credit"}})).send(
        contact(), "s", "b", "critical")
    assert not ok and detail == "kavenegar status 418"
    ok, detail = channel(lambda r: httpx.Response(500, text="oops")).send(contact(), "s", "b", "critical")
    assert not ok and "500" in detail


def test_no_request_is_made_for_missing_key_or_invalid_number(monkeypatch):
    calls = []
    ch = channel(lambda r: calls.append(r) or httpx.Response(200, json={"return": {"status": 200}}))
    assert ch.send(contact("12345"), "s", "b", "critical") == (False, "no valid mobile number configured")
    assert ch.send(contact(None), "s", "b", "critical")[0] is False
    monkeypatch.delenv("SMS_API_KEY")
    assert ch.send(contact(), "s", "b", "critical") == (False, "SMS_API_KEY is not set")
    assert calls == []


def test_secrets_and_numbers_never_appear_in_result_details():
    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")  # the message would contain the key-bearing URL

    ok, detail = channel(boom).send(contact(), "s", "b", "critical")
    assert not ok and SECRET not in detail and PHONE not in detail and detail == "ConnectError"


def test_generic_provider_posts_a_templated_json_body():
    seen = {}

    def handler(req: httpx.Request):
        import json

        seen["body"], seen["auth"] = json.loads(req.content), req.headers["authorization"]
        return httpx.Response(202)

    ch = channel(handler, provider="generic", url="https://relay.local/send", sender="PLANT",
                 template={"msisdn": "{to}", "body": "{text}", "from": "{sender}", "priority": 1})
    ok, _ = ch.send(contact(), "s", "هشدار دما", "critical")
    assert ok and seen["body"] == {"msisdn": PHONE, "body": "هشدار دما", "from": "PLANT", "priority": 1}
    assert seen["auth"] == f"Bearer {SECRET}"
    with pytest.raises(ValueError):
        HttpSmsChannel(provider="generic")
    with pytest.raises(ValueError):
        HttpSmsChannel(provider="nonsense")


def test_only_critical_messages_are_texted_by_default():
    calls = []
    ch = channel(lambda r: calls.append(r) or httpx.Response(200, json={"return": {"status": 200}}))
    n = Notifier([contact()], channels=[LogChannel(), ch])
    n.send("production_manager", None, "s", "b", "warning")
    n.send("production_manager", None, "s", "b", "info")
    assert calls == []
    out = n.send("production_manager", None, "s", "b", "critical")
    assert len(calls) == 1 and {x.channel for x in out} == {"log", "sms"}
    sms = next(x for x in out if x.channel == "sms")
    assert sms.ok and PHONE not in str(sms.to_dict()) and SECRET not in str(sms.to_dict())
    low = HttpSmsChannel(min_severity="warning", client=ch._client)
    n2 = Notifier([contact()], channels=[LogChannel(), low])
    n2.send("production_manager", None, "s", "b", "warning")
    assert len(calls) == 2


def test_failed_sms_is_recorded_but_never_breaks_the_notifier():
    n = Notifier([contact()], channels=[LogChannel(), channel(lambda r: httpx.Response(503))])
    out = n.send("production_manager", None, "s", "b", "critical")
    sms = next(x for x in out if x.channel == "sms")
    assert sms.ok is False and "503" in sms.detail and any(x.channel == "log" and x.ok for x in out)


def test_escalated_critical_alarm_is_texted_to_the_production_manager_end_to_end():
    texts = []

    def handler(req: httpx.Request):
        texts.append(req.url.params["message"])
        return httpx.Response(200, json={"return": {"status": 200}})

    class Clock:
        t = 1_800_000_000.0

        def __call__(self):
            return self.t

    c = Clock()
    contacts = [Contact("op1", "اپراتور خط ۱ (نمونه)", "operator", 1, {"sms": PHONE}),
                Contact("pm1", "مدیر تولید (نمونه)", "production_manager", None, {"sms": "09121111111"}),
                Contact("plm1", "مدیر کارخانه (نمونه)", "plant_manager", None, {"sms": "09122222222"})]
    e = PlantEngine(lines=1, seed=1, fault_rate=0.0, clock=c, contacts=contacts,
                    channels=[LogChannel(), channel(handler)])
    for _ in range(5):
        c.t += 1
        e.step(c.t)
    e.sim.set_setpoint("L1.press1.plate_temp", 200.0)  # runaway heater -> critical alarm
    for _ in range(500):
        c.t += 1
        e.step(c.t)
    assert len(texts) == 3  # operator at once, production manager after 120 s, plant manager after 300 s (unacknowledged)
    assert all("🔴" not in t for t in texts)
    sms = [n for n in e.notifier.history if n.channel == "sms"]
    assert [n.role for n in sms] == ["operator", "production_manager", "plant_manager"]
