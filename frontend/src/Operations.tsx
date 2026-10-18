import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, AuthError } from "./api";
import type { Lang } from "./i18n";

type Reason = { code: string; name_fa: string; category: string };
type Stop = { id: number; line: number; start: number; end: number | null; minutes: number; reason_code: string; reason_fa: string; source: string; note: string | null };
type Par = { total_minutes: number; unassigned_minutes: number; planned_minutes: number; unplanned_minutes: number;
  rows: { reason_code: string; name_fa: string; category: string; minutes: number; count: number; pct: number; cumulative_pct: number }[] };
type Cause = { code: string; name_fa: string };
type Oee = { oee: number | null; availability: number | null; performance: number | null; quality: number | null; parts: number; warnings: string[];
  minutes: { planned: number; unplanned_stops: number; speed_loss: number | null; quality_loss: number | null; fully_productive: number | null; planned_stops: number };
  stop_reasons: { reason_code: string; name_fa: string; minutes: number }[];
  by_line?: { line: number; oee: number | null; availability: number | null; performance: number | null; quality: number | null }[];
  by_shift: { shift: string; oee: number | null; availability: number | null; performance: number | null; quality: number | null }[] };
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);
type Action = { id: number; line: number; severity: string; alarm_message: string; cause_fa: string; action_text: string; status: string; created_by: string; created_at: string };
type Cov = { critical_total: number; with_action: number; coverage: number | null; without_action: { ts: number; line: number | null; summary: string }[] };

const T = {
  fa: {
    down: "توقف‌ها و پارتوی دلیل توقف", days: "۷ روز اخیر", none: "—", line: "خط", start: "شروع", dur: "مدت (دقیقه)", reason: "دلیل",
    open: "در جریان", auto: "خودکار", manual: "دستی", total: "جمع", unassigned: "بدون دلیل ثبت‌شده", planned: "برنامه‌ریزی‌شده",
    unplanned: "برنامه‌ریزی‌نشده", stopLine: "توقف دستی خط", note: "یادداشت", stop: "توقف", save: "ثبت",
    actions: "اقدامات اصلاحی", cov: "پوشش آلارم‌های بحرانی", noActions: "اقدامی ثبت نشده.", close: "بستن", closed: "بسته‌شده",
    missing: "آلارم‌های بحرانی بدون اقدام", by: "توسط", clock: "ساعت",
  },
  en: {
    down: "Stops & downtime Pareto", days: "last 7 days", none: "—", line: "Line", start: "Start", dur: "Minutes", reason: "Reason",
    open: "ongoing", auto: "auto", manual: "manual", total: "Total", unassigned: "Unassigned", planned: "Planned",
    unplanned: "Unplanned", stopLine: "Manual line stop", note: "Note", stop: "Stop", save: "Save",
    actions: "Corrective actions", cov: "Critical-alarm coverage", noActions: "No actions recorded.", close: "Close", closed: "Closed",
    missing: "Critical alarms without an action", by: "by", clock: "Time",
  },
} as const;
const hm = (ts: number) => new Date(ts * 1000).toLocaleString("fa-IR", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

export default function Operations({ lang, lines, onAuthLost }: { lang: Lang; lines: number; onAuthLost: () => void }) {
  const t = T[lang];
  const [reasons, setReasons] = useState<Reason[]>([]);
  const [stops, setStops] = useState<Stop[]>([]);
  const [par, setPar] = useState<Par | null>(null);
  const [acts, setActs] = useState<Action[]>([]);
  const [cov, setCov] = useState<Cov | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [stop, setStop] = useState({ line: 1, reason_code: "changeover", note: "" });
  const [oee, setOee] = useState<Oee | null>(null);

  const guard = useCallback((p: Promise<unknown>) => p.catch((e) => { if (e instanceof AuthError) onAuthLost(); else setErr(String((e as Error).message)); }), [onAuthLost]);
  const load = useCallback(() => {
    guard(api<Reason[]>("/api/plant/downtime/reasons").then(setReasons));
    guard(api<Stop[]>("/api/plant/downtime?days=7").then(setStops));
    guard(api<Par>("/api/plant/downtime/pareto?days=7").then(setPar));
    guard(api<Action[]>("/api/plant/actions?days=30").then(setActs));
    guard(api<Cov>("/api/plant/actions/coverage?days=7").then(setCov));
    guard(api<Oee>("/api/plant/oee?days=7").then(setOee));
  }, [guard]);
  useEffect(() => { load(); const id = setInterval(load, 10000); return () => clearInterval(id); }, [load]);

  const act = async (fn: () => Promise<unknown>) => { try { await fn(); setErr(null); load(); } catch (e) { if (e instanceof AuthError) onAuthLost(); else setErr(String((e as Error).message)); } };
  const assign = (id: number, reason_code: string) => act(() => api(`/api/plant/downtime/${id}`, { method: "PATCH", body: JSON.stringify({ reason_code }) }));
  const doStop = (e: FormEvent) => { e.preventDefault(); act(() => api(`/api/plant/lines/${stop.line}/stop`, { method: "POST", body: JSON.stringify({ reason_code: stop.reason_code, note: stop.note || null }) })); };
  const closeAction = (id: number) => act(() => api(`/api/plant/actions/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) }));
  const max = Math.max(1, ...(par?.rows.map((r) => r.minutes) ?? [1]));

  return (
    <div className="ops">
      {err && <p className="err">{err}</p>}
      <h2>OEE <small>(دسترسی × عملکرد × کیفیت – {t.days})</small></h2>
      {oee && (<>
        <section className="cards">
          <div className="card"><span>OEE</span><b>{pct(oee.oee)}</b></div>
          <div className="card"><span>دسترسی</span><b>{pct(oee.availability)}</b></div>
          <div className="card"><span>عملکرد</span><b>{pct(oee.performance)}</b></div>
          <div className="card"><span>کیفیت (FPY)</span><b>{pct(oee.quality)}</b></div>
        </section>
        <div className="pareto"><b>درخت تلفات (دقیقه از زمان برنامه‌ریزی‌شده {Math.round(oee.minutes.planned)})</b>
          {[["توقف‌های برنامه‌ریزی‌نشده", oee.minutes.unplanned_stops, "#cf222e"], ["افت سرعت", oee.minutes.speed_loss, "#bf8700"],
            ["افت کیفیت", oee.minutes.quality_loss, "#8250df"], ["تولید کاملاً مفید", oee.minutes.fully_productive, "#1a7f37"]].map(([n, v, c]) => (
            <div className="pbar" key={String(n)}><span>{n}</span>
              <div className="bar"><i style={{ width: `${oee.minutes.planned ? (Number(v ?? 0) / oee.minutes.planned) * 100 : 0}%`, background: String(c) }} /></div><b>{v === null ? "—" : Math.round(Number(v))}</b></div>))}
          {oee.stop_reasons.slice(0, 4).map((r) => <p key={r.reason_code} className="small">· {r.name_fa}: {r.minutes} دقیقه</p>)}
        </div>
        {oee.warnings.map((w) => <p key={w} className="small err">{w}</p>)}
        <table>
          <thead><tr><th></th><th>OEE</th><th>دسترسی</th><th>عملکرد</th><th>کیفیت</th></tr></thead>
          <tbody>
            {oee.by_line?.map((l) => <tr key={`l${l.line}`}><td>{t.line} {l.line}</td><td>{pct(l.oee)}</td><td>{pct(l.availability)}</td><td>{pct(l.performance)}</td><td>{pct(l.quality)}</td></tr>)}
            {oee.by_shift.map((l) => <tr key={`s${l.shift}`}><td>شیفت {l.shift}</td><td>{pct(l.oee)}</td><td>{pct(l.availability)}</td><td>{pct(l.performance)}</td><td>{pct(l.quality)}</td></tr>)}
          </tbody>
        </table></>)}

      <h2>{t.down} <small>({t.days})</small></h2>
      <div className="two">
        <div>
          {par && par.rows.length > 0 ? (
            <div className="pareto">
              {par.rows.map((r) => (
                <div key={r.reason_code} className="pbar">
                  <span>{r.name_fa} <small>({r.count})</small></span>
                  <div className="bar"><i className={r.category === "planned" ? "planned" : r.reason_code === "unknown" ? "unk" : ""} style={{ width: `${(r.minutes / max) * 100}%` }} /></div>
                  <b>{r.minutes}</b>
                </div>))}
              <p className="small">{t.total}: {par.total_minutes} · {t.planned}: {par.planned_minutes} · {t.unplanned}: {par.unplanned_minutes}
                {par.unassigned_minutes > 0 && <span className="err"> · {t.unassigned}: {par.unassigned_minutes}</span>}</p>
            </div>) : <p className="small">{t.none}</p>}
          <form onSubmit={doStop} className="form"><b>{t.stopLine}</b>
            <select value={stop.line} onChange={(e) => setStop({ ...stop, line: Number(e.target.value) })} aria-label={t.line}>
              {Array.from({ length: lines }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{t.line} {n}</option>)}</select>
            <select value={stop.reason_code} onChange={(e) => setStop({ ...stop, reason_code: e.target.value })} aria-label={t.reason}>
              {reasons.filter((r) => r.code !== "unknown" && r.code !== "safe_stop").map((r) => <option key={r.code} value={r.code}>{r.name_fa}</option>)}</select>
            <input placeholder={t.note} value={stop.note} onChange={(e) => setStop({ ...stop, note: e.target.value })} />
            <button type="submit">{t.stop}</button></form>
        </div>
        <table>
          <thead><tr><th>{t.line}</th><th>{t.start}</th><th>{t.dur}</th><th>{t.reason}</th></tr></thead>
          <tbody>
            {stops.length === 0 && <tr><td colSpan={4}>{t.none}</td></tr>}
            {stops.slice(0, 12).map((s) => (
              <tr key={s.id} className={s.reason_code === "unknown" ? "st-reorder" : ""}>
                <td>{s.line}</td><td>{hm(s.start)}</td><td>{s.minutes}{s.end === null && <small> {t.open}</small>}</td>
                <td><select value={s.reason_code} onChange={(e) => assign(s.id, e.target.value)} aria-label={t.reason}>
                  {reasons.map((r) => <option key={r.code} value={r.code}>{r.name_fa}</option>)}</select>
                  <small> {s.source === "auto" ? t.auto : t.manual}</small></td>
              </tr>))}
          </tbody>
        </table>
      </div>

      <h2>{t.actions}</h2>
      {cov && <p className="small">{t.cov}: {cov.coverage === null ? t.none : `${(cov.coverage * 100).toFixed(0)}%`} ({cov.with_action}/{cov.critical_total})</p>}
      {cov && cov.without_action.length > 0 && (
        <details className="small"><summary className="err">{t.missing}: {cov.without_action.length}</summary>
          <ul className="feed">{cov.without_action.slice(0, 8).map((w, i) => <li key={i}><small>{hm(w.ts)}</small> {w.summary}</li>)}</ul></details>)}
      <ul className="feed">
        {acts.length === 0 && <li>{t.noActions}</li>}
        {acts.slice(0, 10).map((a) => (
          <li key={a.id} className={a.status === "open" ? "n-warning" : ""}>
            <small>{t.line} {a.line} · {a.cause_fa} · {t.by} {a.created_by} · {a.status === "open" ? t.open : t.closed}</small><br />
            {a.alarm_message}<br /><b>{a.action_text}</b>{" "}
            {a.status === "open" && <button onClick={() => closeAction(a.id)}>{t.close}</button>}
          </li>))}
      </ul>
    </div>
  );
}

export function ActionForm({ alarmId, lang, onDone }: { alarmId: number; lang: Lang; onDone: () => void }) {
  const [causes, setCauses] = useState<Cause[]>([]);
  const [f, setF] = useState({ cause_code: "other", action_text: "", done: false });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Cause[]>("/api/plant/causes").then(setCauses).catch(() => {}); }, []);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { await api(`/api/plant/alarms/${alarmId}/action`, { method: "POST", body: JSON.stringify(f) }); onDone(); } catch (x) { setErr(String((x as Error).message)); }
  };
  const L = lang === "fa" ? { cause: "علت", what: "اقدام انجام‌شده", done: "انجام شد", save: "ثبت اقدام" } : { cause: "Cause", what: "Action taken", done: "Done", save: "Record action" };
  return (
    <form onSubmit={submit} className="form inline">
      <select value={f.cause_code} onChange={(e) => setF({ ...f, cause_code: e.target.value })} aria-label={L.cause}>
        {causes.map((c) => <option key={c.code} value={c.code}>{c.name_fa}</option>)}</select>
      <input required minLength={3} placeholder={L.what} value={f.action_text} onChange={(e) => setF({ ...f, action_text: e.target.value })} />
      <label className="small"><input type="checkbox" checked={f.done} onChange={(e) => setF({ ...f, done: e.target.checked })} /> {L.done}</label>
      <button type="submit">{L.save}</button>
      {err && <span className="err small">{err}</span>}
    </form>
  );
}
