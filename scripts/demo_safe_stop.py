"""Live safe-stop demo against a running backend: autopilot takes over, a heater fault is injected, the line safe-stops.
Usage: python scripts/demo_safe_stop.py   (backend on :8001, admin login from the dev defaults)
"""
import json, sys, time, urllib.request

B = "http://localhost:8001"

def call(method, path, body=None, token=None):
    req = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

tok = call("POST", "/api/auth/login", {"username": "admin", "password": "admin"})["access_token"]
call("POST", "/api/plant/lines/3/operator-presence", {"present": False}, tok)
call("PUT", "/api/plant/lines/3/autopilot", {"mode": "autonomous", "confirm": True, "reason": "live demo"}, tok)
print("autonomous on line 3, operator absent; waiting for takeover", flush=True)
t0 = time.time()
while True:
    s = call("GET", "/api/plant/snapshot", None, tok)
    l = [x for x in s["lines"] if x["line"] == 3][0]
    if l["autopilot"]["engaged"]:
        break
    time.sleep(3)
print(f"engaged after {time.time() - t0:.0f}s; injecting runaway heater fault on L3.press1.plate_temp", flush=True)
call("POST", "/api/plant/faults", {"sensor_id": "L3.press1.plate_temp", "kind": "drift", "magnitude": 2.6, "duration_s": 300}, tok)
t1 = time.time()
while time.time() - t1 < 330:
    s = call("GET", "/api/plant/snapshot", None, tok)
    l = [x for x in s["lines"] if x["line"] == 3][0]
    pv = [e for e in s["equipment"] if e["id"] == "L3.press1"][0]["sensors"][0]["value"]
    if l["autopilot"]["safe_stop"]:
        print(f"SAFE STOP after {time.time() - t1:.0f}s (PV {pv}): {l['autopilot']['safe_stop']}", flush=True)
        print("line state:", l["state"], "| roles notified:", sorted({n["role"] for n in s["notifications"] if n["channel"] == "log" and "توقف ایمن" in n["body"]}), flush=True)
        break
    time.sleep(3)
else:
    print("NO SAFE STOP within 330 s", flush=True)
print("done", flush=True)
