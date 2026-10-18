import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, AuthError, Role } from "./api";
import type { Lang } from "./i18n";

type Opp = { priority: number; text_fa: string; evidence: string };
type Quality = {
  total: number; ng: number; fpy: number | null; defect_rate: number | null; warnings: string[];
  pareto: { class_name: string; count: number; pct: number; cumulative_pct: number }[];
  trend: { date: string; total: number; defect_rate: number }[];
  by_line: { line: number; total: number; defect_rate: number; p_value: number | null; vs_rest_z: number | null }[];
  by_shift: { shift: string; total: number; defect_rate: number; p_value: number | null; vs_rest_z: number | null }[];
  alarm_association: { line: number; insufficient_data?: boolean; rate_with_alarm?: number; rate_without_alarm?: number; p_value?: number; n_with: number; n_without: number }[];
  opportunities: Opp[];
};
type GapRow = { criterion: string; name_fa: string; weight: number; own: number | null; best_rival: number | null; best_rival_name: string | null; gap: number | null; lever: string | null };
type Gap = { rows: GapRow[]; own_total: number | null; rivals: Record<string, { total: number | null; coverage: number }>; priorities: GapRow[]; warnings: string[]; own_defined: boolean };
type Comp = { id: number; name: string; kind: string; is_own: boolean; origin: string; notes: string | null; source_url: string | null; scores: { criterion: string; score: number; evidence: string }[] };
type Cert = { id: number; name: string; scope: string; status: string; target_market: string | null; note: string | null };
type Fc = { history: { month: string; qty: number }[]; forecast: { months: string[]; p10: number[]; p50: number[]; p90: number[]; method: string; wape: number | null; confidence: string; warnings: string[]; n: number }; demo: boolean };
type Prod = { id: number; code: string; name_fa: string; is_demo: boolean };
type CustRow = { customer_id: number; name: string; kind: string; is_demo: boolean;
  profile: { rate_per_month: number; last3: number; trend_pct_per_month: number | null; months_since_last: number | null; risk: string } | null;
  forecast: { p10: number[]; p50: number[]; p90: number[]; confidence: string; warnings: string[] } | null };
type Rec = { severity: string; kind: string; text_fa: string; why: string };
type Scen = { name: string; label: string; months: { month_offset: number; demand_units: number; utilization: number; lines_needed: number; shifts_per_day: number; unmet_units: number }[];
  materials: { code: string; name: string; shortfall_month: number | null; order_within_days: number | null; order_qty: number }[]; recommendations: Rec[] };
type Scenarios = { assumptions: Record<string, number>; assumptions_note: string; scenarios: Scen[]; measured_fpy: number | null; notes: string[]; quality_plus_hours_saved: number };

const nf = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? "—" : v.toLocaleString("fa-IR", { maximumFractionDigits: d }));
const pc = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
const RISK: Record<string, string> = { high: "بالا", medium: "متوسط", low: "پایین", unknown: "نامشخص" };
const STATUS: Record<string, string> = { have: "دارد", in_progress: "در حال اخذ", missing: "ندارد", na: "غیرقابل‌اعمال" };

function ForecastChart({ fc }: { fc: Fc }) {
  const W = 640, H = 200, P = 28;
  const hist = fc.history.slice(-24);
  const f = fc.forecast;
  const all = [...hist.map((h) => h.qty), ...f.p90, ...f.p10];
  const max = Math.max(1, ...all);
  const n = hist.length + f.p50.length;
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(n - 1, 1);
  const y = (v: number) => H - P - ((H - 2 * P) * v) / max;
  const hp = hist.map((h, i) => `${x(i)},${y(h.qty)}`).join(" ");
  const fx = (k: number) => x(hist.length + k);
  const band = [...f.p90.map((v, k) => `${fx(k)},${y(v)}`), ...f.p10.map((v, k) => `${fx(k)},${y(v)}`).reverse()].join(" ");
  const mid = f.p50.map((v, k) => `${fx(k)},${y(v)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="forecast">
      <polygon points={band} fill="#0969da" opacity="0.15" />
      <polyline points={hp} fill="none" stroke="#1c1f24" strokeWidth="2" />
      <polyline points={`${hist.length ? `${x(hist.length - 1)},${y(hist[hist.length - 1].qty)} ` : ""}${mid}`} fill="none" stroke="#0969da" strokeWidth="2" strokeDasharray="5 3" />
      <text x={P} y={12} fontSize="10">{nf(max)}</text>
      <text x={P} y={H - 8} fontSize="9">{hist[0]?.month.slice(0, 7)}</text>
      <text x={W - P} y={H - 8} fontSize="9" textAnchor="end">{f.months[f.months.length - 1]?.slice(0, 7)}</text>
    </svg>
  );
}

export default function Market({ role, lang, onAuthLost }: { role: Role; lang: Lang; onAuthLost: () => void }) {
  const canWrite = role === "manager" || role === "admin";
  const [q, setQ] = useState<Quality | null>(null);
  const [seg, setSeg] = useState<"domestic" | "export">("domestic");
  const [gap, setGap] = useState<Gap | null>(null);
  const [comps, setComps] = useState<Comp[]>([]);
  const [certs, setCerts] = useState<{ certifications: Cert[]; readiness: number | null; note: string } | null>(null);
  const [prods, setProds] = useState<Prod[]>([]);
  const [pid, setPid] = useState<number | null>(null);
  const [fc, setFc] = useState<Fc | null>(null);
  const [cust, setCust] = useState<CustRow[]>([]);
  const [sc, setSc] = useState<Scenarios | null>(null);
  const [scName, setScName] = useState("base");
  const [msg, setMsg] = useState<string | null>(null);
  const [nc, setNc] = useState({ name: "", kind: "domestic", is_own: false });
  const [score, setScore] = useState({ cid: 0, criterion: "quality", score: 3, evidence: "" });
  const [csv, setCsv] = useState("");

  const guard = useCallback((p: Promise<unknown>) => p.catch((e) => { if (e instanceof AuthError) onAuthLost(); else if (!String((e as Error).message).startsWith("409")) setMsg(String((e as Error).message)); }), [onAuthLost]);
  const load = useCallback(() => {
    guard(api<Quality>("/api/market/quality?days=30").then(setQ));
    guard(api<Comp[]>("/api/market/competitors").then(setComps));
    guard(api<{ certifications: Cert[]; readiness: number | null; note: string }>("/api/market/export-readiness").then(setCerts));
    guard(api<CustRow[]>("/api/market/customers/consumption?horizon=3").then(setCust));
    guard(api<Prod[]>("/api/market/products").then((p) => { setProds(p); setPid((cur) => cur ?? p[0]?.id ?? null); }));
    guard(api<Scenarios>("/api/market/scenarios?months=6").then(setSc));
  }, [guard]);
  useEffect(load, [load]);
  useEffect(() => { guard(api<Gap>(`/api/market/competitive?segment=${seg}`).then(setGap)); }, [guard, seg, comps]);
  useEffect(() => { if (pid) guard(api<Fc>(`/api/market/forecast?product_id=${pid}&horizon=6`).then(setFc)); else setFc(null); }, [guard, pid, prods]);

  const act = async (fn: () => Promise<unknown>) => { try { await fn(); setMsg(null); load(); } catch (e) { if (e instanceof AuthError) onAuthLost(); else setMsg(String((e as Error).message)); } };
  const addComp = (e: FormEvent) => { e.preventDefault(); act(() => api("/api/market/competitors", { method: "POST", body: JSON.stringify(nc) })); setNc({ ...nc, name: "" }); };
  const addScore = (e: FormEvent) => { e.preventDefault(); act(() => api(`/api/market/competitors/${score.cid || comps[0]?.id}/scores`, { method: "POST", body: JSON.stringify({ criterion: score.criterion, score: score.score, evidence: score.evidence, as_of: new Date().toISOString().slice(0, 10) }) })); setScore({ ...score, evidence: "" }); };
  const importCsv = () => act(async () => {
    const customers = await api<{ id: number; name: string }[]>("/api/business/customers");
    const rows = csv.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => l.split(",").map((s) => s.trim()));
    const items = rows.map(([c, p, m, qty]) => {
      const cu = customers.find((x) => x.name === c), pr = prods.find((x) => x.code === p);
      if (!cu || !pr) throw new Error(`مشتری یا محصول ناشناخته: ${c} / ${p}`);
      return { customer_id: cu.id, product_id: pr.id, period: `${m}-01`, qty: Number(qty) };
    });
    await api("/api/market/sales/bulk", { method: "POST", body: JSON.stringify(items) });
    setCsv("");
  });
  const scenario = useMemo(() => sc?.scenarios.find((s) => s.name === scName), [sc, scName]);
  const anyDemo = prods.some((p) => p.is_demo) || cust.some((c) => c.is_demo);
  const own = comps.find((c) => c.is_own);

  return (
    <div className="market">
      <p className="small">این تب فقط از داده‌ای استفاده می‌کند که کارخانه وارد کرده یا خود سامانه ثبت کرده است؛ هیچ داده‌ی رقبا یا مشتریان از خودش ساخته نمی‌شود. اگر داده‌ای نباشد، خالی می‌ماند و می‌گوید.</p>
      {anyDemo && <span className="badge b-warn">شامل داده‌ی نمونه (واقعی نیست)</span>}
      {msg && <p className="err">{msg}</p>}
      {role === "admin" && (
        <p className="small">
          <button onClick={() => act(() => api("/api/market/demo-seed", { method: "POST" }))}>بارگذاری داده‌ی نمونه بازار</button>{" "}
          <button onClick={() => act(() => api("/api/market/demo-seed", { method: "DELETE" }))}>حذف داده‌ی نمونه</button>{" "}
          <button onClick={() => act(() => api("/api/market/competitors/seed-public", { method: "POST" }))}>افزودن رقبای معرفی‌شده در منابع عمومی (بدون امتیاز)</button>
        </p>)}

      <h2>۱. بهبود کیفیت تولید <small>(۳۰ روز اخیر، از بازرسی‌های ثبت‌شده)</small></h2>
      {q && (<>
        <section className="cards">
          <div className="card"><span>تعداد بازرسی</span><b>{nf(q.total)}</b></div>
          <div className="card"><span>FPY</span><b>{pc(q.fpy)}</b></div>
          <div className="card"><span>نرخ عیب</span><b>{pc(q.defect_rate)}</b></div>
        </section>
        {q.warnings.map((w) => <p key={w} className="err small">{w}</p>)}
        <div className="two">
          <div className="pareto"><b>پارتوی عیوب</b>
            {q.pareto.length === 0 && <p className="small">—</p>}
            {q.pareto.map((r) => (
              <div className="pbar" key={r.class_name}><span>{r.class_name}</span>
                <div className="bar"><i style={{ width: `${r.pct * 100}%` }} /></div><b>{pc(r.pct, 0)}</b></div>))}
          </div>
          <table>
            <thead><tr><th>خط / شیفت</th><th>تعداد</th><th>نرخ عیب</th><th>معنی‌داری</th></tr></thead>
            <tbody>
              {[...q.by_line.map((x) => ({ k: `خط ${x.line}`, ...x })), ...q.by_shift.map((x) => ({ k: `شیفت ${x.shift}`, ...x }))].map((x) => (
                <tr key={x.k} className={x.p_value !== null && x.p_value < 0.05 && (x.vs_rest_z ?? 0) > 0 ? "st-critical" : ""}>
                  <td>{x.k}</td><td>{nf(x.total)}</td><td>{pc(x.defect_rate)}</td><td>{x.p_value === null ? "داده کم" : `p=${x.p_value}`}</td></tr>))}
            </tbody>
          </table>
        </div>
        <ul className="feed">
          {q.opportunities.length === 0 && <li>فرصت بهبود معنی‌داری از داده‌ی فعلی استنباط نشد (یا داده کافی نیست).</li>}
          {q.opportunities.map((o, i) => <li key={i} className={o.priority === 1 ? "n-critical" : "n-warning"}>{o.text_fa}<br /><small>مستند: {o.evidence}</small></li>)}
          {q.alarm_association.filter((a) => !a.insufficient_data).map((a) => (
            <li key={a.line}><small>خط {a.line}: نرخ عیب با آلارم فعال {pc(a.rate_with_alarm)} در برابر بدون آلارم {pc(a.rate_without_alarm)} (p={a.p_value}) – همبستگی است، نه اثبات علت</small></li>))}
        </ul>
      </>)}

      <h2>۲. ارزیابی رقبا و شکاف رقابتی</h2>
      <div className="tabs">
        <button className={seg === "domestic" ? "on" : ""} onClick={() => setSeg("domestic")}>بازار داخلی</button>
        <button className={seg === "export" ? "on" : ""} onClick={() => setSeg("export")}>صادرات</button>
      </div>
      {gap && (<>
        {gap.warnings.map((w) => <p key={w} className="err small">{w}</p>)}
        <table>
          <thead><tr><th>معیار</th><th>وزن (فرضی، قابل ویرایش)</th><th>ما</th><th>بهترین رقیب</th><th>شکاف</th></tr></thead>
          <tbody>
            {gap.rows.map((r) => (
              <tr key={r.criterion} className={r.gap !== null && r.gap > 0 ? "st-reorder" : ""}>
                <td>{r.name_fa}</td><td>{pc(r.weight, 0)}</td><td>{r.own ?? "—"}</td>
                <td>{r.best_rival ?? "—"} {r.best_rival_name && <small>({r.best_rival_name})</small>}</td><td>{r.gap ?? "—"}</td></tr>))}
          </tbody>
        </table>
        {gap.priorities.length > 0 && <ul className="feed">{gap.priorities.map((p) => <li key={p.criterion} className="n-warning"><b>{p.name_fa}</b> (شکاف {p.gap}): {p.lever}</li>)}</ul>}
        <p className="small">امتیاز کل ما: {gap.own_total ?? "—"} · رقبا: {Object.entries(gap.rivals).map(([n, r]) => `${n} ${r.total ?? "—"} (پوشش ${pc(r.coverage, 0)})`).join(" · ") || "—"}</p>
      </>)}
      <ul className="feed">
        {comps.map((c) => (
          <li key={c.id}><b>{c.name}</b>{c.is_own && " (خود شرکت)"} <small>[{c.kind === "domestic" ? "داخلی" : "خارجی"} · {c.origin === "public" ? "منبع عمومی" : c.origin}]</small>
            {c.notes && <><br /><small>{c.notes}</small></>}{c.source_url && <> <a href={c.source_url} target="_blank" rel="noreferrer"><small>منبع</small></a></>}
            <br /><small>{c.scores.length ? c.scores.map((s) => `${s.criterion}:${s.score}`).join("، ") : "بدون امتیاز"}</small></li>))}
        {comps.length === 0 && <li>رقیبی ثبت نشده است.</li>}
      </ul>
      {canWrite && (
        <div className="two">
          <form onSubmit={addComp} className="form"><b>افزودن رقیب / تعریف «خود شرکت»</b>
            <input required placeholder="نام" value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} />
            <select value={nc.kind} onChange={(e) => setNc({ ...nc, kind: e.target.value })} aria-label="kind"><option value="domestic">داخلی</option><option value="international">خارجی</option></select>
            <label className="small"><input type="checkbox" checked={nc.is_own} disabled={!!own} onChange={(e) => setNc({ ...nc, is_own: e.target.checked })} /> خود شرکت ما</label>
            <button type="submit">ثبت</button></form>
          <form onSubmit={addScore} className="form"><b>ثبت امتیاز (۱ تا ۵) با مستند</b>
            <select value={score.cid || comps[0]?.id || 0} onChange={(e) => setScore({ ...score, cid: Number(e.target.value) })} aria-label="competitor">{comps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <select value={score.criterion} onChange={(e) => setScore({ ...score, criterion: e.target.value })} aria-label="criterion">
              {["quality", "price", "delivery", "certifications", "brand", "service", "capacity", "innovation"].map((k) => <option key={k}>{k}</option>)}</select>
            <input type="number" min={1} max={5} step={0.5} value={score.score} onChange={(e) => setScore({ ...score, score: Number(e.target.value) })} />
            <input required minLength={3} placeholder="مستند امتیاز (آزمون، لیست قیمت، ممیزی…)" value={score.evidence} onChange={(e) => setScore({ ...score, evidence: e.target.value })} />
            <button type="submit">ثبت</button></form>
        </div>)}

      <h2>۳. آمادگی صادرات {certs && <small>({certs.readiness === null ? "—" : pc(certs.readiness, 0)} گواهی‌های قابل‌اعمال را دارید)</small>}</h2>
      {canWrite && <p className="small"><button onClick={() => act(() => api("/api/market/certifications/seed-reference", { method: "POST" }))}>افزودن فهرست مرجع گواهینامه‌ها</button></p>}
      <table>
        <tbody>
          {certs?.certifications.length === 0 && <tr><td>گواهی‌ای ثبت نشده است.</td></tr>}
          {certs?.certifications.map((c) => (
            <tr key={c.id}><td>{c.name}<br /><small>{c.target_market} {c.note && `· ${c.note}`}</small></td>
              <td>{canWrite ? <select value={c.status} onChange={(e) => act(() => api(`/api/market/certifications/${c.id}`, { method: "PATCH", body: JSON.stringify({ status: e.target.value }) }))} aria-label={c.name}>
                {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select> : STATUS[c.status]}</td></tr>))}
        </tbody>
      </table>
      {certs && <p className="small">{certs.note}</p>}

      <h2>۴. پیش‌بینی مصرف مشتریان</h2>
      {prods.length === 0 ? <p className="small">محصول یا سابقه‌ی فروشی ثبت نشده است. سابقه‌ی ماهانه‌ی فروش را وارد کنید (یا داده‌ی نمونه را بارگذاری کنید).</p> : (<>
        <select value={pid ?? ""} onChange={(e) => setPid(Number(e.target.value))} aria-label="product">{prods.map((p) => <option key={p.id} value={p.id}>{p.code} – {p.name_fa}</option>)}</select>
        {fc && (<>
          <ForecastChart fc={fc} />
          <p className="small">روش: {fc.forecast.method} · خطای بک‌تست (WAPE): {fc.forecast.wape === null ? "—" : pc(fc.forecast.wape, 0)} · اعتماد: {fc.forecast.confidence} · {fc.forecast.n} ماه داده · نوار آبی = بازه‌ی ۱۰ تا ۹۰٪
            {fc.forecast.warnings.map((w) => <span key={w} className="err"> · {w}</span>)}</p></>)}
      </>)}
      <table>
        <thead><tr><th>مشتری</th><th>نرخ مصرف ماهانه</th><th>روند ماهانه</th><th>ریسک</th><th>پیش‌بینی ۳ ماه (P50، بازه)</th></tr></thead>
        <tbody>
          {cust.length === 0 && <tr><td colSpan={5}>مشتری‌ای ثبت نشده است.</td></tr>}
          {cust.map((c) => (
            <tr key={c.customer_id} className={c.profile?.risk === "high" ? "st-critical" : ""}>
              <td>{c.name} <small>{c.kind === "domestic" ? "داخلی" : "خارجی"}</small>{c.is_demo && <span className="badge b-warn">نمونه</span>}</td>
              <td>{nf(c.profile?.rate_per_month)}</td><td>{c.profile?.trend_pct_per_month === null || !c.profile ? "—" : `${c.profile.trend_pct_per_month}%`}</td>
              <td>{RISK[c.profile?.risk ?? "unknown"]}{c.profile?.months_since_last ? <small> ({c.profile.months_since_last} ماه از آخرین خرید)</small> : null}</td>
              <td>{c.forecast ? c.forecast.p50.map((v, i) => `${nf(v)} [${nf(c.forecast!.p10[i])}–${nf(c.forecast!.p90[i])}]`).join(" | ") : "—"}</td></tr>))}
        </tbody>
      </table>
      {canWrite && prods.length > 0 && (
        <details className="small"><summary>ورود سابقه‌ی فروش (هر خط: نام مشتری، کد محصول، سال-ماه، تعداد)</summary>
          <textarea rows={4} style={{ width: "100%" }} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder="مشتری الف, P-100, 2026-08, 12000" />
          <button onClick={importCsv}>ثبت</button></details>)}

      <h2>۵. سناریوهای پیشنهادی برای خط تولید <small>(پیشنهاد؛ تصمیم با برنامه‌ریز است)</small></h2>
      {sc ? (<>
        <p className="small err">{sc.assumptions_note}: {sc.assumptions.lines} خط · {sc.assumptions.hours_per_day} ساعت/روز · {sc.assumptions.days_per_month} روز/ماه · OEE هدف {pc(sc.assumptions.target_oee, 0)}
          {sc.measured_fpy !== null ? ` · FPY اندازه‌گیری‌شده ${pc(sc.measured_fpy)}` : " · FPY پیش‌فرض محصول"}</p>
        {sc.notes.map((n) => <p key={n} className="small err">{n}</p>)}
        <div className="tabs">{sc.scenarios.map((s) => <button key={s.name} className={scName === s.name ? "on" : ""} onClick={() => setScName(s.name)}>{s.label}</button>)}</div>
        {scenario && (<>
          <table>
            <thead><tr><th>ماه</th><th>تقاضا (واحد)</th><th>بهره‌برداری</th><th>خط لازم</th><th>شیفت/روز</th><th>تقاضای پوشش‌نیافته</th></tr></thead>
            <tbody>{scenario.months.map((m) => (
              <tr key={m.month_offset} className={m.utilization > 1 ? "st-critical" : m.utilization > 0.95 ? "st-reorder" : ""}>
                <td>{m.month_offset}</td><td>{nf(m.demand_units)}</td>
                <td><div className="bar"><i style={{ width: `${Math.min(m.utilization, 1.5) / 1.5 * 100}%`, background: m.utilization > 1 ? "#cf222e" : "#1a7f37" }} /></div>{pc(m.utilization, 0)}</td>
                <td>{m.lines_needed}</td><td>{m.shifts_per_day}</td><td>{nf(m.unmet_units)}</td></tr>))}</tbody>
          </table>
          <ul className="feed">
            {scenario.recommendations.length === 0 && <li>در این سناریو اقدام خاصی پیشنهاد نمی‌شود.</li>}
            {scenario.recommendations.map((r, i) => <li key={i} className={r.severity === "critical" ? "n-critical" : r.severity === "warning" ? "n-warning" : ""}>{r.text_fa}<br /><small>مبنا: {r.why}</small></li>)}
          </ul>
          {scenario.materials.filter((m) => m.shortfall_month).length > 0 && <p className="small">کمبود مواد: {scenario.materials.filter((m) => m.shortfall_month).map((m) => `${m.name} از ماه ${m.shortfall_month} (سفارش ${nf(m.order_qty)})`).join("، ")}</p>}
        </>)}
        {sc.quality_plus_hours_saved > 0 && <p className="small">بهبود FPY به‌اندازه‌ی ۲ واحد درصد، در این بازه حدود {nf(sc.quality_plus_hours_saved)} ساعت ماشین آزاد می‌کند.</p>}
      </>) : <p className="small">برای سناریو، محصول و سابقه‌ی فروش لازم است.</p>}
    </div>
  );
}
