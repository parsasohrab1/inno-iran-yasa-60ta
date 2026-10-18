"""Notifications to the operator, production manager and plant manager.

Channels are pluggable. `log` is always on (and is what the dashboard shows). `webhook` posts JSON to any URL, which
covers messaging bots and most SMS-gateway HTTP APIs once a small relay is placed in front of them; add a dedicated
Channel subclass for a specific provider. Real phone numbers / tokens belong in data/plant_contacts.json (git-ignored),
never in the repository.
"""
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

ROLES = ("operator", "production_manager", "plant_manager")
ROLE_FA = {"operator": "اپراتور", "production_manager": "مدیر تولید", "plant_manager": "مدیر کارخانه"}


@dataclass
class Contact:
    id: str
    name: str
    role: str
    line: int | None = None  # operators are per line; managers cover all lines
    channels: dict[str, str] = field(default_factory=dict)  # e.g. {"webhook": "https://..."}


@dataclass
class Notification:
    ts: float
    contact_id: str
    name: str
    role: str
    channel: str
    severity: str  # info | warning | critical
    subject: str
    body: str
    ok: bool
    detail: str
    ref: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Channel(Protocol):
    name: str

    def send(self, contact: Contact, subject: str, body: str, severity: str) -> tuple[bool, str]: ...


class LogChannel:
    name = "log"

    def send(self, contact, subject, body, severity):
        return True, "recorded"


class WebhookChannel:
    name = "webhook"

    def __init__(self, timeout: float = 2.0):
        self.timeout = timeout

    def send(self, contact, subject, body, severity):
        url = contact.channels.get("webhook")
        if not url:
            return False, "no webhook configured"
        try:
            import httpx

            r = httpx.post(url, json={"to": contact.name, "role": contact.role, "severity": severity,
                                      "subject": subject, "text": body}, timeout=self.timeout)
            return r.is_success, f"HTTP {r.status_code}"
        except Exception as e:  # a failing channel must never break the control loop
            return False, f"{type(e).__name__}: {e}"


class Notifier:
    def __init__(self, contacts: list[Contact], channels: list[Channel] | None = None,
                 sink: Callable[[Notification], None] | None = None, clock: Callable[[], float] = time.time):
        self.contacts = contacts
        self.channels = {c.name: c for c in (channels or [LogChannel(), WebhookChannel()])}
        self.sink = sink
        self.clock = clock
        self.history: deque[Notification] = deque(maxlen=500)

    def recipients(self, role: str, line: int | None) -> list[Contact]:
        out = [c for c in self.contacts if c.role == role and (role != "operator" or c.line in (None, line))]
        return out

    def send(self, role: str, line: int | None, subject: str, body: str, severity: str = "info",
             ref: str | None = None) -> list[Notification]:
        sent = []
        for c in self.recipients(role, line):
            for ch_name in ["log"] + [k for k in c.channels if k != "log"]:
                ch = self.channels.get(ch_name)
                if ch is None or (hasattr(ch, "accepts") and not ch.accepts(severity)):
                    continue
                ok, detail = ch.send(c, subject, body, severity)
                n = Notification(self.clock(), c.id, c.name, c.role, ch_name, severity, subject, body, ok, detail, ref)
                self.history.append(n)
                sent.append(n)
                if self.sink:
                    try:
                        self.sink(n)
                    except Exception:
                        pass
        return sent


def default_contacts(lines: int = 4) -> list[Contact]:
    """PLACEHOLDER contacts (names marked 'نمونه'): replace via data/plant_contacts.json."""
    cs = [Contact(f"op{n}", f"اپراتور خط {n} (نمونه)", "operator", n) for n in range(1, lines + 1)]
    cs.append(Contact("pm1", "مدیر تولید (نمونه)", "production_manager"))
    cs.append(Contact("plm1", "مدیر کارخانه (نمونه)", "plant_manager"))
    return cs


def load_contacts(path: str, lines: int = 4) -> list[Contact]:
    import json
    import os

    if not os.path.exists(path):
        return default_contacts(lines)
    return [Contact(**c) for c in json.load(open(path, encoding="utf-8"))]
