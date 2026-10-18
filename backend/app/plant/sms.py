"""SMS channel for plant notifications (Iranian gateways over HTTP).

Providers
  kavenegar  GET {base}/v1/{API_KEY}/sms/send.json?receptor=&sender=&message=  -> {"return": {"status": 200, ...}}
             (written from Kavenegar's documented REST format; NOT verified against a live account)
  generic    POST a JSON body built from a template to any URL (for gateways with a different API or a local relay)

The API key is read from the environment (SMS_API_KEY) at send time and is never logged, stored or put in a notification record.
Phone numbers live only in data/plant_contacts.json (git-ignored); notification records and logs never contain them.
By default only `critical` messages are texted (cost, alert fatigue); lower severities stay on the dashboard / webhook.
"""
import os
import re
from typing import Callable

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_MOBILE = re.compile(r"^(?:\+98|0098|98|0)?(9\d{9})$")
SEGMENT = 70  # one UCS-2 (Persian) SMS segment
MAX_SEGMENTS = 3


def normalize_mobile(raw: str | None) -> str | None:
    """Iranian mobile number -> 09xxxxxxxxx, or None if it is not one."""
    if not raw:
        return None
    m = _MOBILE.match(re.sub(r"[\s\-()]", "", raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))))
    return f"0{m.group(1)}" if m else None


def clean_text(text: str) -> str:
    """Strip emoji and other non-BMP symbols (they cost extra segments / break some gateways), collapse whitespace, cap length."""
    text = "".join(ch for ch in text if ord(ch) < 0x10000 and ch not in "‍️")
    text = re.sub(r"\s+", " ", text).strip()
    limit = SEGMENT * MAX_SEGMENTS
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class HttpSmsChannel:
    name = "sms"

    def __init__(self, provider: str = "kavenegar", sender: str | None = None, min_severity: str = "critical",
                 base_url: str | None = None, url: str | None = None, template: dict | None = None,
                 api_key_env: str = "SMS_API_KEY", client=None, timeout: float = 5.0):
        if provider not in ("kavenegar", "generic"):
            raise ValueError("provider must be kavenegar or generic")
        if provider == "generic" and not url:
            raise ValueError("generic provider needs a url")
        self.provider, self.sender, self.min_severity = provider, sender, min_severity
        self.base_url = (base_url or "https://api.kavenegar.com").rstrip("/")
        self.url, self.template, self.api_key_env, self.timeout = url, template or {}, api_key_env, timeout
        self._client = client

    def accepts(self, severity: str) -> bool:
        return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK[self.min_severity]

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def send(self, contact, subject: str, body: str, severity: str) -> tuple[bool, str]:
        phone = normalize_mobile(contact.channels.get("sms"))
        if not phone:
            return False, "no valid mobile number configured"
        key = os.getenv(self.api_key_env)
        if not key:
            return False, f"{self.api_key_env} is not set"
        text = clean_text(body)
        try:
            if self.provider == "kavenegar":
                params = {"receptor": phone, "message": text}
                if self.sender:
                    params["sender"] = self.sender
                r = self._http().get(f"{self.base_url}/v1/{key}/sms/send.json", params=params)
                try:
                    status = r.json().get("return", {}).get("status")
                except Exception:
                    status = None
                ok = r.is_success and status == 200
                return ok, f"kavenegar status {status}" if status is not None else f"HTTP {r.status_code}"
            payload = {k: (v.format(to=phone, text=text, sender=self.sender or "") if isinstance(v, str) else v)
                       for k, v in self.template.items()} or {"to": phone, "text": text}
            r = self._http().post(self.url, json=payload, headers={"Authorization": f"Bearer {key}"})
            return r.is_success, f"HTTP {r.status_code}"
        except Exception as e:  # never leak the URL (it contains the key for kavenegar) or the number
            return False, f"{type(e).__name__}"
