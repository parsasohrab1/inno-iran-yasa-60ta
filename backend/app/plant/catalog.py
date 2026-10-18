"""Typical equipment and sensors for a rubber-products line (gaskets / O-rings).

TYPICAL, NOT VERIFIED: the real equipment list must be confirmed at the site visit
(docs/SITE_VISIT_CHECKLIST_FA.md). Limits and nominals below are plausible engineering defaults, not plant data.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SensorSpec:
    key: str
    name_fa: str
    name_en: str
    unit: str
    nominal: float
    noise: float
    kind: str  # temperature | pressure | current | vibration | speed | flow | weight | humidity | energy | vision
    lo_lo: float | None = None
    lo: float | None = None
    hi: float | None = None
    hi_hi: float | None = None
    controlled: bool = False  # has a setpoint the autopilot/PLC can move
    safety: bool = False  # limit violation requests a safe stop when autopilot is engaged
    decimals: int = 1

    def span(self) -> float:
        """Distance from nominal to the first high limit (or low limit): the unit used to size faults."""
        if self.hi is not None:
            return self.hi - self.nominal
        if self.lo is not None:
            return self.nominal - self.lo
        return max(abs(self.nominal) * 0.1, 1.0)


S = SensorSpec

EQUIPMENT_TYPES: dict[str, dict] = {
    "mixer": {
        "name_fa": "میکسر داخلی (بانبوری)", "name_en": "Internal mixer (Banbury)",
        "sensors": [
            S("batch_temp", "دمای خمیر", "Batch temperature", "°C", 105, 1.5, "temperature", hi=125, hi_hi=135, safety=True),
            S("rotor_speed", "سرعت روتور", "Rotor speed", "rpm", 40, 0.4, "speed", lo=30, hi=50),
            S("ram_pressure", "فشار رم", "Ram pressure", "bar", 6.0, 0.1, "pressure", lo=4.5, hi=7.5, hi_hi=8.5, safety=True),
            S("motor_current", "جریان موتور", "Motor current", "A", 180, 4, "current", hi=240, hi_hi=270),
            S("batch_weight", "وزن بچ", "Batch weight", "kg", 250, 0.8, "weight", lo=245, hi=255),
            S("vibration", "ارتعاش گیربکس", "Gearbox vibration", "mm/s", 2.0, 0.15, "vibration", hi=4.5, hi_hi=7.0),
        ]},
    "mill": {
        "name_fa": "میل دو غلتکی", "name_en": "Two-roll mill",
        "sensors": [
            S("roll_temp", "دمای غلتک", "Roll temperature", "°C", 60, 1.0, "temperature", hi=80, hi_hi=90),
            S("motor_current", "جریان موتور", "Motor current", "A", 90, 2.5, "current", hi=125, hi_hi=145),
            S("nip_gap", "فاصله غلتک", "Nip gap", "mm", 5.0, 0.05, "speed", lo=4.5, hi=5.5, decimals=2),
        ]},
    "extruder": {
        "name_fa": "اکسترودر", "name_en": "Extruder",
        "sensors": [
            S("barrel_temp", "دمای بارل", "Barrel temperature", "°C", 85, 1.0, "temperature", hi=105, hi_hi=115),
            S("head_pressure", "فشار هد", "Head pressure", "bar", 120, 3, "pressure", lo=90, hi=170, hi_hi=190, safety=True),
            S("screw_speed", "سرعت ماردون", "Screw speed", "rpm", 30, 0.3, "speed", lo=22, hi=38),
            S("motor_current", "جریان موتور", "Motor current", "A", 70, 2, "current", hi=100, hi_hi=115),
            S("output", "دبی خروجی", "Output rate", "kg/h", 240, 4, "flow", lo=200),
        ]},
    "press": {
        "name_fa": "پرس قالب‌گیری", "name_en": "Molding press",
        "sensors": [
            S("plate_temp", "دمای صفحه قالب", "Mold plate temperature", "°C", 175, 0.8, "temperature",
              lo_lo=160, lo=168, hi=182, hi_hi=190, controlled=True, safety=True),
            S("hydraulic_pressure", "فشار هیدرولیک", "Hydraulic pressure", "bar", 180, 2.5, "pressure",
              lo=150, hi=210, hi_hi=230, safety=True),
            S("clamp_force", "نیروی بستن قالب", "Clamp force", "kN", 1200, 12, "pressure", lo=1050, hi=1350),
            S("cycle_time", "زمان سیکل", "Cycle time", "s", 240, 2, "speed", lo=225, hi=260),
            S("vibration", "ارتعاش", "Vibration", "mm/s", 1.8, 0.12, "vibration", hi=4.0, hi_hi=6.5),
        ]},
    "oven": {
        "name_fa": "کوره پخت ثانویه", "name_en": "Post-cure oven",
        "sensors": [
            S("air_temp", "دمای هوای کوره", "Oven air temperature", "°C", 200, 1.0, "temperature",
              lo_lo=185, lo=192, hi=208, hi_hi=220, controlled=True, safety=True),
            S("fan_current", "جریان فن", "Fan current", "A", 22, 0.6, "current", hi=30, hi_hi=36),
            S("exhaust_flow", "دبی اگزاست", "Exhaust flow", "m³/h", 1500, 30, "flow", lo=1200),
        ]},
    "deflash": {
        "name_fa": "پلیسه‌گیر", "name_en": "Deflashing unit",
        "sensors": [
            S("motor_current", "جریان موتور", "Motor current", "A", 15, 0.5, "current", hi=22, hi_hi=26),
            S("vibration", "ارتعاش", "Vibration", "mm/s", 2.2, 0.2, "vibration", hi=5.0, hi_hi=7.5),
            S("part_count", "نرخ قطعه", "Part rate", "pcs/min", 30, 0.8, "speed", lo=22),
        ]},
    "inspection": {
        "name_fa": "ایستگاه بازرسی هوشمند", "name_en": "Smart inspection station",
        "sensors": [
            S("throughput", "نرخ بازرسی", "Inspection rate", "pcs/min", 30, 0.8, "vision", lo=24),
            S("latency", "تأخیر استنتاج", "Inference latency", "ms", 70, 4, "vision", hi=100, hi_hi=150),
            S("lighting", "شدت نور", "Illumination", "lux", 800, 12, "vision", lo=650, hi=950),
            S("camera_temp", "دمای دوربین", "Camera temperature", "°C", 42, 0.8, "temperature", hi=60, hi_hi=70),
        ]},
    "conveyor": {
        "name_fa": "نوار نقاله", "name_en": "Conveyor",
        "sensors": [
            S("speed", "سرعت نوار", "Belt speed", "m/min", 12.0, 0.1, "speed", lo=7.5, hi=15, controlled=True),
            S("motor_current", "جریان موتور", "Motor current", "A", 8, 0.3, "current", hi=12, hi_hi=14),
        ]},
    "utilities": {
        "name_fa": "تأسیسات مشترک", "name_en": "Plant utilities",
        "sensors": [
            S("air_pressure", "فشار هوای فشرده", "Compressed air pressure", "bar", 7.5, 0.08, "pressure",
              lo_lo=6.0, lo=6.5, hi=8.5, safety=True),
            S("chiller_temp", "دمای آب چیلر", "Chiller water temperature", "°C", 7.0, 0.2, "temperature", hi=10, hi_hi=13),
            S("steam_pressure", "فشار بخار", "Steam pressure", "bar", 9.0, 0.15, "pressure", lo=7.5, hi=10.5, hi_hi=11.5),
            S("power", "توان مصرفی کل", "Total power", "kW", 850, 25, "energy", hi=1150, hi_hi=1300),
            S("ambient_temp", "دمای سالن", "Hall temperature", "°C", 28, 0.4, "temperature", hi=38),
            S("ambient_humidity", "رطوبت سالن", "Hall humidity", "%RH", 40, 1.2, "humidity", hi=70),
        ]},
}

# per production line; a shared mixing hall and utilities are modelled as "line 0"
LINE_LAYOUT = ["mill", "extruder", "press", "press", "oven", "deflash", "inspection", "conveyor"]
SHARED_LAYOUT = ["mixer", "utilities"]


@dataclass
class Sensor:
    id: str
    spec: SensorSpec
    line: int
    equipment_id: str


@dataclass
class Equipment:
    id: str
    line: int
    type: str
    name_fa: str
    name_en: str
    sensors: list[Sensor] = field(default_factory=list)
    verified: bool = False  # becomes True only after confirmation at the site visit


def build_plant(lines: int = 4) -> list[Equipment]:
    out: list[Equipment] = []

    def make(line: int, etype: str, idx: int):
        t = EQUIPMENT_TYPES[etype]
        eid = f"L{line}.{etype}{idx or ''}"
        suffix = f" {idx}" if idx else ""
        eq = Equipment(eid, line, etype, t["name_fa"] + suffix, t["name_en"] + suffix)
        eq.sensors = [Sensor(f"{eid}.{s.key}", s, line, eid) for s in t["sensors"]]
        out.append(eq)

    for etype in SHARED_LAYOUT:
        make(0, etype, 0)
    counts = {e: LINE_LAYOUT.count(e) for e in set(LINE_LAYOUT)}
    for line in range(1, lines + 1):
        seen: dict[str, int] = {}
        for etype in LINE_LAYOUT:
            seen[etype] = seen.get(etype, 0) + 1
            make(line, etype, seen[etype] if counts[etype] > 1 else 0)
    return out
