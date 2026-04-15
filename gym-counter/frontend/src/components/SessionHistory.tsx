import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SessionListEntry, SessionSummary } from "../api";
import { fetchSession, fetchSessions } from "../api";

function formatTime(epoch: number | null | undefined): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function formBarColor(score: number): string {
  if (score >= 0.85) return "#22c55e";
  if (score >= 0.60) return "#facc15";
  return "#ef4444";
}

export default function SessionHistory() {
  const [sessions, setSessions] = useState<SessionListEntry[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const list = await fetchSessions();
      setSessions(list);
      setErr(null);
      if (list.length && selected == null) setSelected(list[0].id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selected == null) return;
    let alive = true;
    (async () => {
      try {
        const s = await fetchSession(selected);
        if (!alive) return;
        setSummary(s);
        setErr(null);
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [selected]);

  const chartData =
    summary?.sets.map((s, i) => ({
      name: `${s.exercise}#${i + 1}`,
      reps: s.reps,
      form: Math.round(s.form_score * 100),
    })) ?? [];

  return (
    <div className="rounded-2xl bg-ink-800 p-6 shadow-xl border border-ink-700">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold tracking-tight">Session history</h2>
        <button
          onClick={refresh}
          className="text-xs rounded-md px-2 py-1 bg-ink-700 hover:bg-ink-600 transition"
          disabled={loading}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {err && (
        <div className="mb-3 text-xs text-red-400">{err}</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4">
        <div className="rounded-xl bg-ink-700/60 p-2 max-h-72 overflow-y-auto">
          {sessions.length === 0 ? (
            <div className="text-sm text-gray-400 p-2">No sessions yet.</div>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelected(s.id)}
                className={
                  "w-full text-left rounded-lg px-3 py-2 mb-1 text-sm transition " +
                  (s.id === selected
                    ? "bg-emerald-500 text-ink-900 font-semibold"
                    : "hover:bg-ink-600 text-gray-200")
                }
              >
                <div>Session #{s.id}</div>
                <div className="text-xs opacity-75">{formatTime(s.started_at)}</div>
              </button>
            ))
          )}
        </div>

        <div>
          {summary ? (
            <>
              <div className="grid grid-cols-3 gap-3 mb-4">
                <Stat label="Total reps" value={summary.total_reps} />
                <Stat label="Sets" value={summary.sets.length} />
                <Stat
                  label="Started"
                  value={formatTime(summary.started_at)}
                  small
                />
              </div>

              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                    <CartesianGrid stroke="#273441" strokeDasharray="3 3" />
                    <XAxis dataKey="name" tick={{ fill: "#9ca3af", fontSize: 11 }} />
                    <YAxis yAxisId="reps" tick={{ fill: "#9ca3af", fontSize: 11 }} />
                    <YAxis
                      yAxisId="form"
                      orientation="right"
                      domain={[0, 100]}
                      tick={{ fill: "#9ca3af", fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#111820",
                        border: "1px solid #273441",
                        borderRadius: 8,
                        color: "#e5e7eb",
                      }}
                    />
                    <Legend wrapperStyle={{ color: "#d1d5db", fontSize: 12 }} />
                    <Bar yAxisId="reps" dataKey="reps" name="Reps" fill="#60a5fa" radius={[6, 6, 0, 0]} />
                    <Bar yAxisId="form" dataKey="form" name="Form %" radius={[6, 6, 0, 0]}>
                      {chartData.map((d, idx) => (
                        <Cell key={idx} fill={formBarColor(d.form / 100)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-gray-400">
                    <tr>
                      <th className="text-left py-2 pr-3">Set</th>
                      <th className="text-left py-2 pr-3">Exercise</th>
                      <th className="text-right py-2 pr-3">Reps</th>
                      <th className="text-right py-2 pr-3">Form</th>
                    </tr>
                  </thead>
                  <tbody className="text-gray-200">
                    {summary.sets.map((s, i) => (
                      <tr key={s.id} className="border-t border-ink-700">
                        <td className="py-2 pr-3">#{i + 1}</td>
                        <td className="py-2 pr-3 capitalize">{s.exercise}</td>
                        <td className="py-2 pr-3 text-right">{s.reps}</td>
                        <td className="py-2 pr-3 text-right">
                          <span
                            style={{ color: formBarColor(s.form_score) }}
                            className="font-semibold"
                          >
                            {Math.round(s.form_score * 100)}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="text-sm text-gray-400">Select a session to see details.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, small }: { label: string; value: string | number; small?: boolean }) {
  return (
    <div className="rounded-xl bg-ink-700 p-3">
      <div className="text-xs text-gray-400">{label}</div>
      <div className={(small ? "text-sm" : "text-2xl ") + " font-semibold mt-1"}>{value}</div>
    </div>
  );
}
