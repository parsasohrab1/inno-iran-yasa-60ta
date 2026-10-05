import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  api, AuthError, download, getToken, Inspection, KPI, login, ParetoPoint, Role, setToken, SpcPoint, TrendPoint,
} from "./api";
import { Pareto, Spc, Trend } from "./charts";
import { Lang, messages } from "./i18n";

const ANALYTICS_ROLES: Role[] = ["qc", "manager", "maintenance", "admin"];
const REPORT_ROLES: Role[] = ["qc", "manager", "admin"];

function roleFromToken(): Role | null {
  try {
    return JSON.parse(atob(getToken().split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).role;
  } catch {
    return null;
  }
}

export default function App() {
  const [lang, setLang] = useState<Lang>("fa");
  const [role, setRole] = useState<Role | null>(roleFromToken());
  const t = messages[lang];

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "fa" ? "rtl" : "ltr"; // NFR-8
  }, [lang]);

  const logout = useCallback(() => { setToken(""); setRole(null); }, []);

  return (
    <main>
      <header>
        <h1>{t.title}</h1>
        <div>
          {role && <button onClick={logout}>{t.logout}</button>}{" "}
          <button onClick={() => setLang(lang === "fa" ? "en" : "fa")}>{lang === "fa" ? "EN" : "فا"}</button>
        </div>
      </header>
      {role ? <Dashboard role={role} lang={lang} onAuthLost={logout} /> : <Login lang={lang} onLogin={setRole} />}
    </main>
  );
}

function Login({ lang, onLogin }: { lang: Lang; onLogin: (r: Role) => void }) {
  const t = messages[lang];
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { onLogin(await login(u, p)); } catch { setErr(true); }
  };

  return (
    <form onSubmit={submit} className="login">
      <input placeholder={t.username} value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" />
      <input placeholder={t.password} type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
      <button type="submit">{t.login}</button>
      {err && <p className="err">{t.badLogin}</p>}
    </form>
  );
}

function Dashboard({ role, lang, onAuthLost }: { role: Role; lang: Lang; onAuthLost: () => void }) {
  const t = messages[lang];
  const [kpi, setKpi] = useState<KPI | null>(null);
  const [rows, setRows] = useState<Inspection[]>([]);
  const [onlyReview, setOnlyReview] = useState(false);
  const [pareto, setPareto] = useState<ParetoPoint[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [spc, setSpc] = useState<{ p_bar: number; points: SpcPoint[] }>({ p_bar: 0, points: [] });

  const guard = useCallback(<T,>(p: Promise<T>) => p.catch((e) => { if (e instanceof AuthError) onAuthLost(); }), [onAuthLost]);
  const canAnalyze = ANALYTICS_ROLES.includes(role);
  const canReport = REPORT_ROLES.includes(role);
  const canReview = role === "qc" || role === "admin";

  const refresh = useCallback(() => {
    guard(api<KPI>("/api/kpi").then(setKpi));
    if (canAnalyze) {
      guard(api<ParetoPoint[]>("/api/analytics/pareto").then(setPareto));
      guard(api<TrendPoint[]>("/api/analytics/trend").then(setTrend));
      guard(api<{ p_bar: number; points: SpcPoint[] }>("/api/analytics/spc").then(setSpc));
    }
  }, [guard, canAnalyze]);

  useEffect(() => {
    refresh();
    guard(api<Inspection[]>("/api/inspections?limit=30").then(setRows));
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/inspections?token=${getToken()}`);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data) as Inspection;
      setRows((prev) => [msg, ...prev.filter((r) => r.id !== msg.id)].slice(0, 30));
      refresh();
    };
    return () => ws.close();
  }, [guard, refresh]);

  const review = (id: number, status: "OK" | "NG") =>
    guard(api<Inspection>(`/api/inspections/${id}/review`, { method: "PATCH", body: JSON.stringify({ status }) }));

  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
  const shown = onlyReview ? rows.filter((r) => r.status === "Review") : rows;

  return (
    <>
      <section className="cards">
        <div className="card"><span>{t.total}</span><b>{kpi?.total ?? "–"}</b></div>
        <div className="card"><span>{t.fpy}</span><b>{kpi ? pct(kpi.fpy) : "–"}</b></div>
        <div className="card"><span>{t.defectRate}</span><b>{kpi ? pct(kpi.defect_rate) : "–"}</b></div>
        <div className="card"><span>{t.review}</span><b>{kpi?.review ?? "–"}</b></div>
      </section>

      {canAnalyze && (
        <section className="charts">
          <div><h2>{t.pareto}</h2><Pareto data={pareto} /></div>
          <div><h2>{t.trend}</h2><Trend data={trend} /></div>
          <div><h2>{t.spc}</h2><Spc points={spc.points} pBar={spc.p_bar} /></div>
        </section>
      )}

      <h2>
        {t.live}{" "}
        <label className="small"><input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} /> {t.onlyReview}</label>
        {canReport && (
          <span className="small">
            {" "}
            <button onClick={() => guard(download("/api/reports/inspections.csv", "inspections.csv"))}>{t.exportCsv}</button>{" "}
            <button onClick={() => guard(download("/api/reports/inspections.xlsx", "inspections.xlsx"))}>{t.exportXlsx}</button>
          </span>
        )}
      </h2>
      <table>
        <thead><tr><th>{t.part}</th><th>{t.line}</th><th>{t.defects}</th><th>{t.status}</th><th /></tr></thead>
        <tbody>
          {shown.length === 0 && <tr><td colSpan={5}>{t.none}</td></tr>}
          {shown.map((r) => (
            <tr key={r.id}>
              <td>{r.part_id}</td>
              <td>{r.line_id}</td>
              <td>{r.defects.map((d) => `${d.class_name} ${(d.confidence * 100).toFixed(0)}%`).join(", ")}</td>
              <td className={`s-${r.status}`}>{r.status}</td>
              <td>
                {canReview && r.status === "Review" && (
                  <>
                    <button onClick={() => review(r.id, "NG")}>{t.confirmNg}</button>{" "}
                    <button onClick={() => review(r.id, "OK")}>{t.markOk}</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
