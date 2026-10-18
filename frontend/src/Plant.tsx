import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, AuthError, getToken, Role } from "./api";
import type { Lang } from "./i18n";
import Operations, { ActionForm } from "./Operations";

type Sensor = {
  id: string; key: string; name_fa: string; unit: string; value: number | null; quality: string;
  state: "ok" | "warning" | "critical" | "fault"; setpoint: number | null; decimals: number; spark?: number[];
};
type Equip = { id: string; line: number; type: string; name_fa: string; name_en: string; verified: boolean; sensors: Sensor[] };
type Alarm = { id: number; severity: string; message: string; raised_at: number; acked_by: string | null; line: number; level: number };
type Ap = {
  mode: "off" | "advisory" | "autonomous"; engaged: boolean; safe_stop: string | null; operator_absent: boolean;
  expires_in_s: number | null; actions_last_hour: number; recent: { ts: number; kind: string; summary: string }[];
};
type LineS = {
  line: number; state: string; stop_reason: string | null; throughput: number | null; speed_ratio: number | null;
  defect_rate: number | null; samples: number | null; oee: number | null; operator_present: boolean; autopilot: Ap;
};
type Notif = { ts: number; role: string; name: string; subject: string; body: string; severity: string; ok: boolean; channel: string };
type Snap = {
  ts: number; simulated: boolean;
  source: { kind: string; connected: boolean; can_write: boolean; error: string | null };
  anomaly: { state: "learning" | "active"; progress: number; active: { equipment_id: string; kind: string; sensors: { id: string; z: number }[] }[] }; shift: { name_fa: string; working: boolean; ends_at: string; assumed: boolean };
  lines: LineS[]; equipment: Equip[]; alarms: { active: Alarm[]; recent: Alarm[] }; notifications: Notif[];
  events: { ts: number; kind: string; summary: string; severity: string }[];
};

const T = {
  fa: {
    sim: "داده شبیه‌سازی‌شده", assumed: "ساعات شیفت فرضی – نیازمند تأیید", off: "خارج از روز کاری",
    ends: "پایان شیفت", lines: "خطوط تولید", running: "در حال کار", stopped: "متوقف", oee: "OEE", defect: "نرخ عیب",
    thr: "نرخ بازرسی", speed: "سرعت", operator: "اپراتور", present: "حاضر", absent: "غایب", autopilot: "اتوپایلوت",
    engaged: "کنترل در دست اتوپایلوت", standby: "آماده‌باش", modeOff: "خاموش", modeAdv: "مشورتی", modeAuto: "خودکار",
    confirmAuto: "حالت خودکار فقط وقتی اپراتور حاضر نیست وارد عمل می‌شود و در محدوده مجاز دستور را تغییر می‌دهد. با توقف ایمن قفل می‌شود. ادامه می‌دهید؟",
    restart: "راه‌اندازی مجدد دستی", alarms: "آلارم‌های فعال", none: "—", ack: "تأیید", acked: "تأییدشده",
    notifs: "پیام‌های ارسالی", equip: "تجهیزات و سنسورها", all: "همه", shared: "سالن مشترک", line: "خط",
    log: "گزارش اتوپایلوت", unverified: "تجهیزات نمونه – نیازمند تأیید در بازدید",
    faultTitle: "تزریق خطای آزمایشی (دمو)", inject: "تزریق", togglePresence: "حضور اپراتور (شبیه‌سازی)",
    critical: "بحرانی", warning: "هشدار", operatorR: "اپراتور", production_manager: "مدیر تولید", plant_manager: "مدیر کارخانه",
    safeStop: "توقف ایمن (قفل‌شده)", samples: "نمونه",
    srcSim: "منبع داده: شبیه‌ساز", srcOpc: "منبع داده: OPC-UA", connected: "متصل", disconnected: "قطع",
    writeOn: "نوشتن روی PLC: فعال", writeOff: "نوشتن روی PLC: غیرفعال (فقط پایش)", learning: "یادگیری رفتار عادی",
    anomaly: "ناهنجاری‌های رفتاری", anomalyOk: "رفتار همه تجهیزات عادی است", recordAction: "ثبت اقدام اصلاحی",
  },
  en: {
    sim: "Simulated data", assumed: "Shift hours are assumed – to be confirmed", off: "Non-working day",
    ends: "Shift ends", lines: "Production lines", running: "Running", stopped: "Stopped", oee: "OEE", defect: "Defect rate",
    thr: "Inspection rate", speed: "Speed", operator: "Operator", present: "Present", absent: "Absent", autopilot: "Autopilot",
    engaged: "Autopilot in control", standby: "Standby", modeOff: "Off", modeAdv: "Advisory", modeAuto: "Autonomous",
    confirmAuto: "Autonomous mode acts only while no operator is present, within narrow limits, and latches a safe stop on any fault. Continue?",
    restart: "Manual restart", alarms: "Active alarms", none: "—", ack: "Acknowledge", acked: "Acknowledged",
    notifs: "Sent messages", equip: "Equipment & sensors", all: "All", shared: "Shared hall", line: "Line",
    log: "Autopilot log", unverified: "Typical equipment – to be confirmed on site",
    faultTitle: "Inject test fault (demo)", inject: "Inject", togglePresence: "Operator presence (simulated)",
    critical: "Critical", warning: "Warning", operatorR: "Operator", production_manager: "Production manager", plant_manager: "Plant manager",
    safeStop: "Safe stop (latched)", samples: "samples",
    srcSim: "Data source: simulator", srcOpc: "Data source: OPC-UA", connected: "connected", disconnected: "disconnected",
    writeOn: "PLC writes: enabled", writeOff: "PLC writes: disabled (monitor only)", learning: "Learning normal behaviour",
    anomaly: "Behaviour anomalies", anomalyOk: "All equipment behaving normally", recordAction: "Record corrective action",
  },
} as const;

const MANAGER: Role[] = ["manager", "qc", "admin"];
const fmt = (v: number | null, d: number) => (v === null ? "—" : v.toFixed(d));
const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(0)}%`);
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString("fa-IR", { hour12: false });

function Spark({ data, state }: { data: number[]; state: string }) {
  if (data.length < 2) return <svg className="spark" />;
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * 80},${20 - ((v - min) / span) * 18 - 1}`).join(" ");
  return <svg className="spark" viewBox="0 0 80 20"><polyline fill="none" strokeWidth="1.5" className={`sp-${state}`} points={pts} /></svg>;
}

export default function Plant({ role, lang, onAuthLost }: { role: Role; lang: Lang; onAuthLost: () => void }) {
  const t = T[lang];
  const [snap, setSnap] = useState<Snap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<number | "all">("all");
  const [logLine, setLogLine] = useState(1);
  const [actionFor, setActionFor] = useState<number | null>(null);
  const sparks = useRef(new Map<string, number[]>());
  const [fault, setFault] = useState({ sensor: "L1.press1.plate_temp", kind: "drift" });
  const canManage = MANAGER.includes(role);

  const feed = useCallback((s: Snap, fromRest: boolean) => {
    for (const e of s.equipment) for (const x of e.sensors) {
      if (fromRest && x.spark) sparks.current.set(x.id, x.spark);
      else if (x.value !== null) {
        const b = sparks.current.get(x.id) ?? [];
        b.push(x.value);
        if (b.length > 40) b.shift();
        sparks.current.set(x.id, b);
      }
    }
    setSnap(s);
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    api<Snap>("/api/plant/snapshot").then((s) => { feed(s, true); }).catch((e) => {
      if (e instanceof AuthError) onAuthLost(); else setErr(String(e.message ?? e));
    });
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/plant?token=${getToken()}`);
    ws.onmessage = (m) => { if (!closed) feed(JSON.parse(m.data) as Snap, false); };
    return () => { closed = true; ws?.close(); };
  }, [feed, onAuthLost]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { if (e instanceof AuthError) onAuthLost(); else setErr(String((e as Error).message)); }
  };
  const setMode = (n: number, mode: string) => {
    if (mode === "autonomous" && !window.confirm(t.confirmAuto)) return;
    act(() => api(`/api/plant/lines/${n}/autopilot`, { method: "PUT", body: JSON.stringify({ mode, confirm: mode === "autonomous" }) }));
  };
  const visible = useMemo(() => (snap ? snap.equipment.filter((e) => filter === "all" || e.line === filter) : []), [snap, filter]);

  if (!snap) return <p>{err ?? "…"}</p>;
  const modeLabel = { off: t.modeOff, advisory: t.modeAdv, autonomous: t.modeAuto } as const;
  const roleLabel = (r: string) => (t as Record<string, string>)[r === "operator" ? "operatorR" : r] ?? r;
  const lineLog = snap.lines.find((l) => l.line === logLine)?.autopilot.recent ?? [];

  return (
    <div className="plant">
      <div className="badges">
        {snap.simulated ? <span className="badge b-sim">{t.sim}</span> : null}
        <span className={`badge ${snap.source.kind === "opcua" && !snap.source.connected ? "b-crit" : "b-sim"}`}>
          {snap.source.kind === "opcua" ? `${t.srcOpc} – ${snap.source.connected ? t.connected : t.disconnected}` : t.srcSim}</span>
        <span className={`badge ${snap.source.can_write ? "b-warn" : ""}`}>{snap.source.can_write ? t.writeOn : t.writeOff}</span>
        <span className="badge">{snap.shift.name_fa}{!snap.shift.working && ` · ${t.off}`} · {t.ends}: {new Date(snap.shift.ends_at).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit", hour12: false })}</span>
        {snap.shift.assumed && <span className="badge b-warn">{t.assumed}</span>}
        {snap.simulated && <span className="badge b-warn">{t.unverified}</span>}
      </div>
      {err && <p className="err">{err}</p>}

      <h2>{t.lines}</h2>
      <section className="lines">
        {snap.lines.map((l) => (
          <div key={l.line} className={`card line ${l.state === "stopped" ? "stopped" : ""}`}>
            <div className="row"><b>{t.line} {l.line}</b><span className={`pill ${l.state === "running" ? "p-ok" : "p-crit"}`}>{l.state === "running" ? t.running : t.stopped}</span></div>
            <div className="kv"><span>{t.oee}</span><b>{pct(l.oee)}</b></div>
            <div className="kv"><span>{t.defect}</span><b>{pct(l.defect_rate)}{l.samples ? <small> ({l.samples} {t.samples})</small> : null}</b></div>
            <div className="kv"><span>{t.thr}</span><b>{fmt(l.throughput, 1)}</b></div>
            <div className="kv"><span>{t.speed}</span><b>{pct(l.speed_ratio)}</b></div>
            <div className="kv"><span>{t.operator}</span><b>{l.operator_present ? t.present : t.absent}</b></div>
            <div className="kv"><span>{t.autopilot}</span>
              <b className={l.autopilot.engaged ? "eng" : ""}>{l.autopilot.engaged ? t.engaged : l.autopilot.mode === "autonomous" ? t.standby : modeLabel[l.autopilot.mode]}</b></div>
            {l.autopilot.safe_stop && <p className="err small">{t.safeStop}: {l.autopilot.safe_stop}</p>}
            {canManage && (
              <div className="ctl">
                <select value={l.autopilot.mode} onChange={(e) => setMode(l.line, e.target.value)} aria-label={t.autopilot}>
                  <option value="off">{t.modeOff}</option><option value="advisory">{t.modeAdv}</option><option value="autonomous">{t.modeAuto}</option>
                </select>
                <label className="small"><input type="checkbox" checked={l.operator_present} aria-label={t.togglePresence}
                  onChange={(e) => act(() => api(`/api/plant/lines/${l.line}/operator-presence`, { method: "POST", body: JSON.stringify({ present: e.target.checked }) }))} /> {t.togglePresence}</label>
                {l.state === "stopped" && <button onClick={() => act(() => api(`/api/plant/lines/${l.line}/restart`, { method: "POST" }))}>{t.restart}</button>}
              </div>
            )}
          </div>
        ))}
      </section>

      <h2>{t.alarms} ({snap.alarms.active.length})</h2>
      <table>
        <tbody>
          {snap.alarms.active.length === 0 && <tr><td>{t.none}</td></tr>}
          {snap.alarms.active.map((a) => (
            <tr key={a.id} className={`al-${a.severity}`}>
              <td>{a.severity === "critical" ? t.critical : t.warning}</td><td>{clock(a.raised_at)}</td><td>{a.message}</td>
              <td>{a.acked_by ? `${t.acked}: ${a.acked_by}` : <button onClick={() => act(() => api(`/api/plant/alarms/${a.id}/ack`, { method: "POST" }))}>{t.ack}</button>}{" "}
                <button onClick={() => setActionFor(actionFor === a.id ? null : a.id)}>{t.recordAction}</button></td>
            </tr>
          )).flatMap((row, i) => {
            const a = snap.alarms.active[i];
            return actionFor === a.id
              ? [row, <tr key={`f${a.id}`}><td colSpan={4}><ActionForm alarmId={a.id} lang={lang} onDone={() => setActionFor(null)} /></td></tr>]
              : [row];
          })}
        </tbody>
      </table>

      <h2>{t.anomaly}</h2>
      {snap.anomaly.state === "learning" ? (
        <p className="small">{t.learning}: <progress value={snap.anomaly.progress} max={1} /> {(snap.anomaly.progress * 100).toFixed(0)}%</p>
      ) : snap.anomaly.active.length === 0 ? <p className="small">{t.anomalyOk}</p> : (
        <ul className="feed">{snap.anomaly.active.map((x) => (
          <li key={x.equipment_id} className="n-warning"><b>{x.equipment_id}</b> · {x.kind === "drift" ? "drift" : "pattern"} · {x.sensors.map((s) => `${s.id.split(".").slice(-1)[0]} ${s.z > 0 ? "+" : ""}${s.z}σ`).join(", ")}</li>))}</ul>)}

      <Operations lang={lang} lines={snap.lines.length} onAuthLost={onAuthLost} />

      <div className="two">
        <div>
          <h2>{t.notifs}</h2>
          <ul className="feed">
            {snap.notifications.filter((n) => n.channel === "log").slice(0, 8).map((n, i) => (
              <li key={i} className={`n-${n.severity}`}><small>{clock(n.ts)} · {roleLabel(n.role)}</small><br />{n.body}</li>))}
            {snap.notifications.length === 0 && <li>{t.none}</li>}
          </ul>
        </div>
        <div>
          <h2>{t.log} <select value={logLine} onChange={(e) => setLogLine(Number(e.target.value))} aria-label={t.log}>
            {snap.lines.map((l) => <option key={l.line} value={l.line}>{t.line} {l.line}</option>)}</select></h2>
          <ul className="feed">
            {lineLog.length === 0 && <li>{t.none}</li>}
            {lineLog.slice(0, 8).map((a, i) => <li key={i}><small>{clock(a.ts)} · {a.kind}</small><br />{a.summary}</li>)}
          </ul>
        </div>
      </div>

      <h2>{t.equip}</h2>
      <div className="tabs">
        {(["all", 0, 1, 2, 3, 4] as const).filter((x) => x === "all" || x === 0 || x <= snap.lines.length).map((x) => (
          <button key={x} className={filter === x ? "on" : ""} onClick={() => setFilter(x)}>{x === "all" ? t.all : x === 0 ? t.shared : `${t.line} ${x}`}</button>))}
      </div>
      <div className="equip">
        {visible.map((e) => (
          <div key={e.id} className="card eq">
            <b>{lang === "fa" ? e.name_fa : e.name_en}</b> <small>{e.id}</small>
            <div className="sensors">
              {e.sensors.map((s) => (
                <div key={s.id} className={`sensor s-${s.state}`} title={s.id}>
                  <span>{s.name_fa}</span>
                  <b>{s.state === "fault" ? "⚠" : fmt(s.value, s.decimals)} <small>{s.unit}</small></b>
                  <Spark data={sparks.current.get(s.id) ?? s.spark ?? []} state={s.state} />
                  {s.setpoint !== null && <small>SP {s.setpoint.toFixed(1)}</small>}
                </div>))}
            </div>
          </div>))}
      </div>

      {role === "admin" && (
        <details className="small"><summary>{t.faultTitle}</summary>
          <select value={fault.sensor} onChange={(e) => setFault({ ...fault, sensor: e.target.value })} aria-label="sensor">
            {snap.equipment.flatMap((e) => e.sensors).map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}</select>
          <select value={fault.kind} onChange={(e) => setFault({ ...fault, kind: e.target.value })} aria-label="kind">
            {["drift", "spike", "stuck", "dropout"].map((k) => <option key={k}>{k}</option>)}</select>
          <button onClick={() => act(() => api("/api/plant/faults", { method: "POST", body: JSON.stringify({ sensor_id: fault.sensor, kind: fault.kind, magnitude: 1.6, duration_s: 120 }) }))}>{t.inject}</button>
        </details>)}
    </div>
  );
}
