"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Point = [number, number]; // [unix seconds, value]

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

const clock = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export function LineChart({
  title,
  points,
  format,
  threshold,
  thresholdLabel,
  height = 132,
  hint,
}: {
  title: string;
  points: Point[];
  format: (v: number) => string;
  threshold?: number;
  thresholdLabel?: string;
  height?: number;
  hint?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 40, r: 10, t: 8, b: 20 };
  const last = points.at(-1)?.[1];
  const breach = threshold !== undefined && last !== undefined && last > threshold;

  const geo = useMemo(() => {
    if (points.length < 2 || width < 60) return null;
    const t0 = points[0][0];
    const t1 = points[points.length - 1][0];
    const maxV = niceMax(Math.max(...points.map((p) => p[1]), threshold ?? 0) * 1.1);
    const iw = width - pad.l - pad.r;
    const ih = height - pad.t - pad.b;
    const x = (t: number) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * iw;
    const y = (v: number) => pad.t + ih - (v / maxV) * ih;
    const line = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    const area = `${line}L${x(t1).toFixed(1)},${pad.t + ih}L${x(t0).toFixed(1)},${pad.t + ih}Z`;
    return { x, y, line, area, maxV, iw, ih, t0, t1 };
  }, [points, width, height, threshold, pad.l, pad.r, pad.t, pad.b]);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!geo) return;
    const bx = e.currentTarget.getBoundingClientRect().left;
    const px = e.clientX - bx;
    let best = 0;
    let bestD = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(geo.x(p[0]) - px);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    setHover(best);
  };

  const hp = hover !== null ? points[hover] : null;

  return (
    <figure className={`chart ${breach ? "breach" : ""}`}>
      <figcaption>
        <span className="chart-title">{title}</span>
        <span className="chart-value">{last !== undefined ? format(last) : "–"}</span>
      </figcaption>
      {hint && <p className="chart-hint">{hint}</p>}
      <div ref={ref} className="chart-plot" style={{ height }}>
        {!geo && <p className="muted small chart-empty">Waiting for data…</p>}
        {geo && (
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={`${title}: latest ${last !== undefined ? format(last) : "no data"}`}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            {[0, 0.5, 1].map((f) => {
              const v = geo.maxV * f;
              return (
                <g key={f}>
                  <line className="grid" x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} />
                  <text className="axis" x={pad.l - 6} y={geo.y(v) + 3} textAnchor="end">
                    {format(v)}
                  </text>
                </g>
              );
            })}
            <text className="axis" x={pad.l} y={height - 4}>
              {clock(geo.t0)}
            </text>
            <text className="axis" x={width - pad.r} y={height - 4} textAnchor="end">
              {clock(geo.t1)}
            </text>
            {threshold !== undefined && threshold <= geo.maxV && (
              <g>
                <line className="threshold" x1={pad.l} x2={width - pad.r} y1={geo.y(threshold)} y2={geo.y(threshold)} />
                <text className="axis threshold-label" x={width - pad.r} y={geo.y(threshold) - 4} textAnchor="end">
                  {thresholdLabel ?? `alert ${format(threshold)}`}
                </text>
              </g>
            )}
            <path className="area" d={geo.area} />
            <path className="line" d={geo.line} />
            {hp && (
              <g>
                <line className="crosshair" x1={geo.x(hp[0])} x2={geo.x(hp[0])} y1={pad.t} y2={pad.t + geo.ih} />
                <circle className="dot" cx={geo.x(hp[0])} cy={geo.y(hp[1])} r={4.5} />
              </g>
            )}
          </svg>
        )}
        {geo && hp && (
          <div
            className="chart-tip"
            style={{ left: Math.min(Math.max(geo.x(hp[0]), 60), width - 60), top: Math.max(geo.y(hp[1]) - 44, 0) }}
          >
            <strong>{format(hp[1])}</strong>
            <span>{clock(hp[0])}</span>
          </div>
        )}
      </div>
    </figure>
  );
}

const SEV_ORDER = ["S1", "S2", "S3", "S4"] as const;

/** Active incidents by severity: one segmented bar, every segment labelled. */
export function SeverityBar({ counts }: { counts: Record<string, number> }) {
  const total = SEV_ORDER.reduce((s, k) => s + (counts[k] ?? 0), 0);
  return (
    <div className="sevbar-wrap">
      <div className="sevbar" role="img" aria-label={SEV_ORDER.map((k) => `${k}: ${counts[k] ?? 0}`).join(", ")}>
        {total === 0 ? (
          <span className="sevbar-empty" />
        ) : (
          SEV_ORDER.filter((k) => counts[k]).map((k) => (
            <span key={k} className={`sevbar-seg sev-${k}`} style={{ flexGrow: counts[k] }} title={`${k}: ${counts[k]}`} />
          ))
        )}
      </div>
      <ul className="sevbar-legend">
        {SEV_ORDER.map((k) => (
          <li key={k}>
            <span className={`sev sev-${k}`}>{k}</span> {counts[k] ?? 0}
          </li>
        ))}
      </ul>
    </div>
  );
}
