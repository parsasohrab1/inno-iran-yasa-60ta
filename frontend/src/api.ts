export type Role = "operator" | "qc" | "manager" | "maintenance" | "admin";

export interface Defect { id: number; class_name: string; confidence: number; severity: string; confirmed: boolean | null }
export interface Inspection {
  id: number; part_id: string; line_id: number; status: "OK" | "NG" | "Review";
  reviewed_by: string | null; defects: Defect[];
}
export interface KPI {
  total: number; ng: number; review: number; defect_rate: number; fpy: number;
  top_defects: { class_name: string; count: number }[];
}
export interface TrendPoint { bucket: string; total: number; ng: number; defect_rate: number }
export interface ParetoPoint { class_name: string; count: number; cumulative_pct: number }
export interface SpcPoint { bucket: string; p: number; ucl: number; lcl: number; out_of_control: boolean }

let token = "";
try { token = sessionStorage.getItem("token") ?? ""; } catch { /* storage unavailable */ }
export const getToken = () => token;
export const setToken = (t: string) => {
  token = t;
  try { t ? sessionStorage.setItem("token", t) : sessionStorage.removeItem("token"); } catch { /* ignore */ }
};

export class AuthError extends Error {}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token && { Authorization: `Bearer ${token}` }), ...init.headers },
  });
  if (res.status === 401) throw new AuthError("unauthorized");
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function login(username: string, password: string): Promise<Role> {
  const r = await api<{ access_token: string; role: Role }>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ username, password }),
  });
  setToken(r.access_token);
  return r.role;
}

export async function download(path: string, filename: string) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}
