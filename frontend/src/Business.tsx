import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, AuthError, Role } from "./api";
import type { Lang } from "./i18n";

type Inv = {
  id: number; code: string; name_fa: string; name_en: string | null; unit: string; on_hand: number; daily_use: number;
  days_of_cover: number | null; status: "ok" | "reorder" | "critical"; reorder_point: number; safety_stock: number;
  lead_time_days: number; is_demo: boolean;
  last_price: { price: number; currency: string; source: string; as_of: string; note: string | null } | null;
};
type Cust = { id: number; name: string; kind: string; country: string | null; products: string | null; is_demo: boolean };

const T = {
  fa: {
    inv: "انبار مواد اولیه", code: "کد", name: "ماده", onHand: "موجودی", use: "مصرف روزانه", cover: "روز پوشش", status: "وضعیت",
    price: "آخرین قیمت", ok: "کافی", reorder: "نیاز به سفارش", critical: "بحرانی", demoBanner: "داده نمونه – واقعی نیست",
    empty: "هنوز داده‌ای وارد نشده است. اطلاعات واقعی از ERP/انبار یا ورود دستی لازم است.", seed: "بارگذاری داده نمونه (دمو)",
    clear: "حذف داده نمونه", addPrice: "ثبت قیمت (بورس کالا / تأمین‌کننده / بازار)", source: "منبع", save: "ثبت", amount: "قیمت", date: "تاریخ",
    move: "ثبت ورود/خروج انبار", qty: "مقدار", receipt: "ورود", issue: "خروج", customers: "مشتریان", domestic: "داخلی",
    international: "بین‌المللی", noCust: "مشتری ثبت نشده است.", addCust: "افزودن مشتری", cname: "نام", ckind: "نوع",
    note: "نرخ بورسی/قیمت بازار باید توسط کارخانه یا فید رسمی وارد شود؛ سامانه هیچ قیمتی را از خود نمی‌سازد.",
  },
  en: {
    inv: "Raw-material warehouse", code: "Code", name: "Material", onHand: "On hand", use: "Daily use", cover: "Days of cover", status: "Status",
    price: "Latest price", ok: "OK", reorder: "Reorder", critical: "Critical", demoBanner: "Demo data – not real",
    empty: "No data yet. Real data needs the ERP/warehouse feed or manual entry.", seed: "Load demo data",
    clear: "Remove demo data", addPrice: "Record a price (exchange / supplier / market)", source: "Source", save: "Save", amount: "Price", date: "Date",
    move: "Record stock in/out", qty: "Quantity", receipt: "Receipt", issue: "Issue", customers: "Customers", domestic: "Domestic",
    international: "International", noCust: "No customers recorded.", addCust: "Add customer", cname: "Name", ckind: "Kind",
    note: "Exchange rates / market prices must be entered by the factory or an official feed; the system never invents prices.",
  },
} as const;

export default function Business({ role, lang, onAuthLost }: { role: Role; lang: Lang; onAuthLost: () => void }) {
  const t = T[lang];
  const [inv, setInv] = useState<Inv[]>([]);
  const [cust, setCust] = useState<Cust[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const canWrite = role === "manager" || role === "admin";
  const [price, setPrice] = useState({ material_id: 0, price: "", source: "ime", as_of: new Date().toISOString().slice(0, 10) });
  const [mv, setMv] = useState({ material_id: 0, qty: "", kind: "receipt" });
  const [nc, setNc] = useState({ name: "", kind: "domestic", country: "" });

  const load = useCallback(() => {
    api<Inv[]>("/api/business/inventory").then(setInv).catch((e) => e instanceof AuthError && onAuthLost());
    api<Cust[]>("/api/business/customers").then(setCust).catch(() => {});
  }, [onAuthLost]);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); setMsg(null); load(); } catch (e) { if (e instanceof AuthError) onAuthLost(); else setMsg(String((e as Error).message)); }
  };
  const submitPrice = (e: FormEvent) => { e.preventDefault(); run(() => api("/api/business/prices", { method: "POST", body: JSON.stringify({ ...price, price: Number(price.price), material_id: price.material_id || inv[0]?.id }) })); };
  const submitMove = (e: FormEvent) => { e.preventDefault(); run(() => api("/api/business/stock", { method: "POST", body: JSON.stringify({ ...mv, qty: Number(mv.qty), material_id: mv.material_id || inv[0]?.id }) })); };
  const submitCust = (e: FormEvent) => { e.preventDefault(); run(() => api("/api/business/customers", { method: "POST", body: JSON.stringify({ ...nc, country: nc.country || null }) })); setNc({ ...nc, name: "" }); };
  const label = { ok: t.ok, reorder: t.reorder, critical: t.critical } as const;
  const anyDemo = inv.some((i) => i.is_demo) || cust.some((c) => c.is_demo);

  return (
    <div>
      <p className="small">{t.note}</p>
      {anyDemo && <span className="badge b-warn">{t.demoBanner}</span>}
      {msg && <p className="err">{msg}</p>}

      <h2>{t.inv}
        {role === "admin" && <span className="small"> <button onClick={() => run(() => api("/api/business/demo-seed", { method: "POST" }))}>{t.seed}</button>{" "}
          <button onClick={() => run(() => api("/api/business/demo-seed", { method: "DELETE" }))}>{t.clear}</button></span>}
      </h2>
      <table>
        <thead><tr><th>{t.code}</th><th>{t.name}</th><th>{t.onHand}</th><th>{t.use}</th><th>{t.cover}</th><th>{t.status}</th><th>{t.price}</th></tr></thead>
        <tbody>
          {inv.length === 0 && <tr><td colSpan={7}>{t.empty}</td></tr>}
          {inv.map((i) => (
            <tr key={i.id}>
              <td>{i.code}</td><td>{lang === "fa" ? i.name_fa : i.name_en ?? i.name_fa}</td>
              <td>{i.on_hand.toLocaleString()} {i.unit}</td><td>{i.daily_use.toLocaleString()}</td><td>{i.days_of_cover ?? "—"}</td>
              <td className={`st-${i.status}`}>{label[i.status]}</td>
              <td>{i.last_price ? <>{i.last_price.price} {i.last_price.currency} <span className={`badge ${i.last_price.source === "demo" ? "b-warn" : ""}`}>{i.last_price.source}</span> <small>{i.last_price.as_of}</small></> : "—"}</td>
            </tr>))}
        </tbody>
      </table>

      {canWrite && inv.length > 0 && (
        <div className="two">
          <form onSubmit={submitPrice} className="form"><b>{t.addPrice}</b>
            <select value={price.material_id} onChange={(e) => setPrice({ ...price, material_id: Number(e.target.value) })} aria-label={t.name}>
              {inv.map((i) => <option key={i.id} value={i.id}>{i.code}</option>)}</select>
            <input type="number" step="any" min="0" required placeholder={t.amount} value={price.price} onChange={(e) => setPrice({ ...price, price: e.target.value })} />
            <select value={price.source} onChange={(e) => setPrice({ ...price, source: e.target.value })} aria-label={t.source}>
              {["ime", "supplier", "market", "manual"].map((s) => <option key={s}>{s}</option>)}</select>
            <input type="date" value={price.as_of} onChange={(e) => setPrice({ ...price, as_of: e.target.value })} aria-label={t.date} />
            <button type="submit">{t.save}</button></form>
          <form onSubmit={submitMove} className="form"><b>{t.move}</b>
            <select value={mv.material_id} onChange={(e) => setMv({ ...mv, material_id: Number(e.target.value) })} aria-label={t.name}>
              {inv.map((i) => <option key={i.id} value={i.id}>{i.code}</option>)}</select>
            <input type="number" step="any" min="0" required placeholder={t.qty} value={mv.qty} onChange={(e) => setMv({ ...mv, qty: e.target.value })} />
            <select value={mv.kind} onChange={(e) => setMv({ ...mv, kind: e.target.value })} aria-label="kind">
              <option value="receipt">{t.receipt}</option><option value="issue">{t.issue}</option></select>
            <button type="submit">{t.save}</button></form>
        </div>)}

      <h2>{t.customers}</h2>
      {(["domestic", "international"] as const).map((k) => (
        <div key={k}><h3>{k === "domestic" ? t.domestic : t.international}</h3>
          <ul className="feed">
            {cust.filter((c) => c.kind === k).length === 0 && <li>{t.noCust}</li>}
            {cust.filter((c) => c.kind === k).map((c) => <li key={c.id}>{c.name}{c.country ? ` – ${c.country}` : ""}{c.is_demo && <span className="badge b-warn">demo</span>}</li>)}
          </ul></div>))}
      {canWrite && (
        <form onSubmit={submitCust} className="form"><b>{t.addCust}</b>
          <input required placeholder={t.cname} value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} />
          <select value={nc.kind} onChange={(e) => setNc({ ...nc, kind: e.target.value })} aria-label={t.ckind}>
            <option value="domestic">{t.domestic}</option><option value="international">{t.international}</option></select>
          <input placeholder="country" value={nc.country} onChange={(e) => setNc({ ...nc, country: e.target.value })} />
          <button type="submit">{t.save}</button></form>)}
    </div>
  );
}
