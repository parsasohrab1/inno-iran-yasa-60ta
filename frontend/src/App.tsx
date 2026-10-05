import { useEffect, useState } from "react";
import { Lang, messages } from "./i18n";

interface Inspection {
  id: number;
  part_id: string;
  line_id: number;
  status: "OK" | "NG" | "Review";
}
interface KPI {
  total: number;
  review: number;
  defect_rate: number;
  fpy: number;
  top_defects: { class_name: string; count: number }[];
}

export default function App() {
  const [lang, setLang] = useState<Lang>("fa");
  const [kpi, setKpi] = useState<KPI | null>(null);
  const [rows, setRows] = useState<Inspection[]>([]);
  const t = messages[lang];

  const refreshKpi = () =>
    fetch("/api/kpi").then((r) => r.json()).then(setKpi).catch(() => {});

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "fa" ? "rtl" : "ltr"; // NFR-8
  }, [lang]);

  useEffect(() => {
    refreshKpi();
    fetch("/api/inspections?limit=20").then((r) => r.json()).then(setRows).catch(() => {});
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/inspections`);
    ws.onmessage = (e) => {
      setRows((prev) => [JSON.parse(e.data) as Inspection, ...prev].slice(0, 20));
      refreshKpi();
    };
    return () => ws.close();
  }, []);

  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <main>
      <header>
        <h1>{t.title}</h1>
        <button onClick={() => setLang(lang === "fa" ? "en" : "fa")}>{lang === "fa" ? "EN" : "فا"}</button>
      </header>

      <section className="cards">
        <div className="card"><span>{t.total}</span><b>{kpi?.total ?? "–"}</b></div>
        <div className="card"><span>{t.fpy}</span><b>{kpi ? pct(kpi.fpy) : "–"}</b></div>
        <div className="card"><span>{t.defectRate}</span><b>{kpi ? pct(kpi.defect_rate) : "–"}</b></div>
        <div className="card"><span>{t.review}</span><b>{kpi?.review ?? "–"}</b></div>
      </section>

      <h2>{t.topDefects}</h2>
      <ul>
        {kpi?.top_defects.length ? kpi.top_defects.map((d) => (
          <li key={d.class_name}>{d.class_name}: {d.count}</li>
        )) : <li>{t.none}</li>}
      </ul>

      <h2>{t.live}</h2>
      <table>
        <thead><tr><th>{t.part}</th><th>{t.line}</th><th>{t.status}</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}><td>{r.part_id}</td><td>{r.line_id}</td><td className={`s-${r.status}`}>{r.status}</td></tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
