import { ParetoPoint, SpcPoint, TrendPoint } from "./api";

const W = 520, H = 180, P = 28;

export function Pareto({ data }: { data: ParetoPoint[] }) {
  if (!data.length) return null;
  const max = Math.max(...data.map((d) => d.count));
  const bw = (W - 2 * P) / data.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" className="chart" aria-label="Pareto">
      {data.map((d, i) => {
        const h = ((H - 2 * P) * d.count) / max;
        return (
          <g key={d.class_name}>
            <rect x={P + i * bw + 4} y={H - P - h} width={bw - 8} height={h} fill="#cf222e" />
            <text x={P + i * bw + bw / 2} y={H - 8} fontSize="9" textAnchor="middle">{d.class_name}</text>
            <text x={P + i * bw + bw / 2} y={H - P - h - 3} fontSize="9" textAnchor="middle">{d.count}</text>
          </g>
        );
      })}
      <polyline fill="none" stroke="#0969da" strokeWidth="2"
        points={data.map((d, i) => `${P + i * bw + bw / 2},${H - P - (H - 2 * P) * d.cumulative_pct}`).join(" ")} />
    </svg>
  );
}

export function Trend({ data }: { data: TrendPoint[] }) {
  if (data.length < 2) return null;
  const max = Math.max(0.01, ...data.map((d) => d.defect_rate));
  const x = (i: number) => P + (i * (W - 2 * P)) / (data.length - 1);
  const y = (v: number) => H - P - ((H - 2 * P) * v) / max;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" className="chart" aria-label="Trend">
      <polyline fill="none" stroke="#cf222e" strokeWidth="2" points={data.map((d, i) => `${x(i)},${y(d.defect_rate)}`).join(" ")} />
      <text x={P} y={12} fontSize="10">{(max * 100).toFixed(1)}%</text>
      <text x={P} y={H - 8} fontSize="9">{data[0].bucket}</text>
      <text x={W - P} y={H - 8} fontSize="9" textAnchor="end">{data[data.length - 1].bucket}</text>
    </svg>
  );
}

export function Spc({ points, pBar }: { points: SpcPoint[]; pBar: number }) {
  if (points.length < 2) return null;
  const max = Math.max(0.01, ...points.map((d) => Math.max(d.ucl, d.p)));
  const x = (i: number) => P + (i * (W - 2 * P)) / (points.length - 1);
  const y = (v: number) => H - P - ((H - 2 * P) * v) / max;
  const line = (f: (d: SpcPoint) => number) => points.map((d, i) => `${x(i)},${y(f(d))}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" className="chart" aria-label="SPC p-chart">
      <polyline fill="none" stroke="#888" strokeDasharray="4" points={line((d) => d.ucl)} />
      <polyline fill="none" stroke="#888" strokeDasharray="4" points={line((d) => d.lcl)} />
      <line x1={P} x2={W - P} y1={y(pBar)} y2={y(pBar)} stroke="#1a7f37" />
      <polyline fill="none" stroke="#0969da" strokeWidth="2" points={line((d) => d.p)} />
      {points.map((d, i) => d.out_of_control && <circle key={d.bucket} cx={x(i)} cy={y(d.p)} r="4" fill="#cf222e" />)}
    </svg>
  );
}
