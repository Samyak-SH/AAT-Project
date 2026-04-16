import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SessionListEntry, SessionSummary, SetRow } from "../api";
import { fetchSession, fetchSessions } from "../api";

// ── helpers ──

function formatTime(epoch: number | null | undefined): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formColor(score: number): string {
  if (score >= 0.85) return "#22c55e";
  if (score >= 0.6) return "#facc15";
  return "#ef4444";
}

function formGrade(score: number): string {
  if (score >= 0.95) return "A+";
  if (score >= 0.85) return "A";
  if (score >= 0.75) return "B";
  if (score >= 0.6) return "C";
  if (score >= 0.4) return "D";
  return "F";
}

const EX_COLORS: Record<string, string> = {
  curl: "#60a5fa",
  squat: "#a78bfa",
  rest: "#6b7280",
  other: "#fbbf24",
};
function exColor(ex: string) {
  return EX_COLORS[ex] || "#9ca3af";
}

const tooltipStyle = {
  contentStyle: {
    background: "#111820",
    border: "1px solid #273441",
    borderRadius: 8,
    color: "#e5e7eb",
  },
  itemStyle: { color: "#e5e7eb" },
  labelStyle: { color: "#9ca3af" },
};

// ── analytics computation ──

function computeAnalytics(s: SessionSummary) {
  const activeSets = s.sets.filter(
    (x) => x.exercise !== "rest" && x.exercise !== "other",
  );
  const duration =
    s.ended_at && s.started_at ? s.ended_at - s.started_at : 0;

  const byEx: Record<
    string,
    { reps: number; sets: number; formSum: number; bestForm: number; dur: number }
  > = {};
  for (const set of activeSets) {
    const e = byEx[set.exercise] ?? {
      reps: 0, sets: 0, formSum: 0, bestForm: 0, dur: 0,
    };
    e.reps += set.reps;
    e.sets += 1;
    e.formSum += set.form_score;
    if (set.form_score > e.bestForm) e.bestForm = set.form_score;
    if (set.started_at && set.ended_at) e.dur += set.ended_at - set.started_at;
    byEx[set.exercise] = e;
  }

  const exercises = Object.entries(byEx).map(([name, d]) => ({
    name,
    reps: d.reps,
    sets: d.sets,
    avgForm: d.sets ? d.formSum / d.sets : 0,
    bestForm: d.bestForm,
    avgRepsPerSet: d.sets ? Math.round((d.reps / d.sets) * 10) / 10 : 0,
    dur: d.dur,
  }));

  const totalReps = activeSets.reduce((a, x) => a + x.reps, 0);
  const avgForm = activeSets.length
    ? activeSets.reduce((a, x) => a + x.form_score, 0) / activeSets.length
    : 0;
  const bestSet = activeSets.reduce<SetRow | null>(
    (b, x) => (!b || x.form_score > b.form_score ? x : b), null,
  );
  const worstSet = activeSets.reduce<SetRow | null>(
    (w, x) => (!w || x.form_score < w.form_score ? x : w), null,
  );
  const repsPerMin = duration > 60 ? totalReps / (duration / 60) : totalReps;

  const pieData = exercises.map((e) => ({
    name: e.name[0].toUpperCase() + e.name.slice(1),
    value: e.reps,
    fill: exColor(e.name),
  }));

  const formTrend = activeSets.map((x, i) => ({
    set: i + 1,
    form: Math.round(x.form_score * 100),
    exercise: x.exercise,
  }));

  const barData = activeSets.map((x, i) => ({
    name: `${x.exercise}#${i + 1}`,
    reps: x.reps,
    form: Math.round(x.form_score * 100),
  }));

  return {
    exercises,
    totalReps,
    avgForm,
    bestSet,
    worstSet,
    repsPerMin,
    duration,
    numSets: activeSets.length,
    pieData,
    formTrend,
    barData,
    activeSets,
  };
}

// ── component ──

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
    return () => { alive = false; };
  }, [selected]);

  const a = summary ? computeAnalytics(summary) : null;

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

      {err && <div className="mb-3 text-xs text-red-400">{err}</div>}

      <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4">
        {/* Session list */}
        <div className="rounded-xl bg-ink-700/60 p-2 max-h-[34rem] overflow-y-auto">
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

        {/* Detail pane */}
        <div>
          {summary && a ? (
            <div className="space-y-5">

              {/* ── Top stats ── */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Stat label="Total reps" value={a.totalReps} />
                <Stat label="Sets" value={a.numSets} />
                <Stat label="Duration" value={a.duration ? formatDuration(a.duration) : "—"} />
                <Stat label="Reps / min" value={a.duration > 60 ? a.repsPerMin.toFixed(1) : "—"} />
              </div>

              {/* ── Form overview ── */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl bg-ink-700 p-3">
                  <div className="text-xs text-gray-400">Avg form</div>
                  <div className="flex items-baseline gap-2 mt-1">
                    <span className="text-2xl font-semibold" style={{ color: formColor(a.avgForm) }}>
                      {Math.round(a.avgForm * 100)}%
                    </span>
                    <span
                      className="text-sm font-bold px-1.5 py-0.5 rounded"
                      style={{ background: formColor(a.avgForm) + "22", color: formColor(a.avgForm) }}
                    >
                      {formGrade(a.avgForm)}
                    </span>
                  </div>
                </div>
                <div className="rounded-xl bg-ink-700 p-3">
                  <div className="text-xs text-gray-400">Best set</div>
                  {a.bestSet ? (
                    <div className="mt-1">
                      <span className="text-lg font-semibold text-emerald-400">
                        {Math.round(a.bestSet.form_score * 100)}%
                      </span>
                      <span className="text-xs text-gray-400 ml-1.5 capitalize">
                        {a.bestSet.exercise} · {a.bestSet.reps} reps
                      </span>
                    </div>
                  ) : <div className="text-gray-500 mt-1">—</div>}
                </div>
                <div className="rounded-xl bg-ink-700 p-3">
                  <div className="text-xs text-gray-400">Worst set</div>
                  {a.worstSet ? (
                    <div className="mt-1">
                      <span className="text-lg font-semibold" style={{ color: formColor(a.worstSet.form_score) }}>
                        {Math.round(a.worstSet.form_score * 100)}%
                      </span>
                      <span className="text-xs text-gray-400 ml-1.5 capitalize">
                        {a.worstSet.exercise} · {a.worstSet.reps} reps
                      </span>
                    </div>
                  ) : <div className="text-gray-500 mt-1">—</div>}
                </div>
                <Stat label="Started" value={formatTime(summary.started_at)} small />
              </div>

              {/* ── Per-exercise breakdown + pie ── */}
              <div className="grid grid-cols-1 sm:grid-cols-[1fr_200px] gap-4">
                <div>
                  <div className="text-xs text-gray-400 uppercase tracking-wide mb-2">By exercise</div>
                  <div className="space-y-2">
                    {a.exercises.map((e) => (
                      <div key={e.name} className="flex items-center gap-3 rounded-lg bg-ink-700/60 px-3 py-2">
                        <div className="w-2 h-8 rounded-full shrink-0" style={{ background: exColor(e.name) }} />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold capitalize">{e.name}</div>
                          <div className="text-xs text-gray-400">
                            {e.reps} reps · {e.sets} sets · {e.avgRepsPerSet} reps/set
                            {e.dur > 0 && ` · ${formatDuration(e.dur)}`}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-semibold" style={{ color: formColor(e.avgForm) }}>
                            {Math.round(e.avgForm * 100)}%
                          </div>
                          <div className="text-[10px] text-gray-500">best {Math.round(e.bestForm * 100)}%</div>
                        </div>
                      </div>
                    ))}
                    {a.exercises.length === 0 && (
                      <div className="text-sm text-gray-500">No exercise sets recorded.</div>
                    )}
                  </div>
                </div>

                {a.pieData.length > 0 && (
                  <div>
                    <div className="text-xs text-gray-400 uppercase tracking-wide mb-2">Rep share</div>
                    <div className="h-44">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={a.pieData}
                            dataKey="value"
                            nameKey="name"
                            cx="50%"
                            cy="50%"
                            innerRadius={40}
                            outerRadius={65}
                            paddingAngle={3}
                            strokeWidth={0}
                          >
                            {a.pieData.map((d, i) => (
                              <Cell key={i} fill={d.fill} />
                            ))}
                          </Pie>
                          <Tooltip {...tooltipStyle} formatter={(val: number) => [`${val} reps`, ""]} />
                          <Legend wrapperStyle={{ fontSize: 11, color: "#d1d5db" }} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </div>

              {/* ── Reps & form bar chart ── */}
              {a.barData.length > 0 && (
                <>
                  <div className="text-xs text-gray-400 uppercase tracking-wide">Reps &amp; form per set</div>
                  <div className="h-56">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={a.barData} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                        <CartesianGrid stroke="#273441" strokeDasharray="3 3" />
                        <XAxis dataKey="name" tick={{ fill: "#9ca3af", fontSize: 11 }} />
                        <YAxis yAxisId="reps" tick={{ fill: "#9ca3af", fontSize: 11 }} />
                        <YAxis yAxisId="form" orientation="right" domain={[0, 100]} tick={{ fill: "#9ca3af", fontSize: 11 }} />
                        <Tooltip {...tooltipStyle} />
                        <Legend wrapperStyle={{ color: "#d1d5db", fontSize: 12 }} />
                        <Bar yAxisId="reps" dataKey="reps" name="Reps" fill="#60a5fa" radius={[6, 6, 0, 0]} />
                        <Bar yAxisId="form" dataKey="form" name="Form %" radius={[6, 6, 0, 0]}>
                          {a.barData.map((d, idx) => (
                            <Cell key={idx} fill={formColor(d.form / 100)} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </>
              )}

              {/* ── Form trend line ── */}
              {a.formTrend.length > 1 && (
                <>
                  <div className="text-xs text-gray-400 uppercase tracking-wide">Form trend across sets</div>
                  <div className="h-40">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={a.formTrend} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                        <CartesianGrid stroke="#273441" strokeDasharray="3 3" />
                        <XAxis
                          dataKey="set"
                          tick={{ fill: "#9ca3af", fontSize: 11 }}
                          label={{ value: "Set #", position: "insideBottom", fill: "#6b7280", fontSize: 10, offset: 0 }}
                        />
                        <YAxis
                          domain={[0, 100]}
                          tick={{ fill: "#9ca3af", fontSize: 11 }}
                          label={{ value: "Form %", angle: -90, position: "insideLeft", fill: "#6b7280", fontSize: 10 }}
                        />
                        <Tooltip {...tooltipStyle} formatter={(val: number) => [`${val}%`, "Form"]} />
                        <Line
                          type="monotone"
                          dataKey="form"
                          stroke="#22c55e"
                          strokeWidth={2}
                          dot={{ fill: "#22c55e", r: 3 }}
                          activeDot={{ r: 5 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </>
              )}

              {/* ── Set detail table ── */}
              <div className="overflow-x-auto">
                <div className="text-xs text-gray-400 uppercase tracking-wide mb-2">Set details</div>
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-gray-400">
                    <tr>
                      <th className="text-left py-2 pr-3">Set</th>
                      <th className="text-left py-2 pr-3">Exercise</th>
                      <th className="text-right py-2 pr-3">Reps</th>
                      <th className="text-right py-2 pr-3">Form</th>
                      <th className="text-right py-2 pr-3">Grade</th>
                      <th className="text-right py-2 pr-3">Duration</th>
                    </tr>
                  </thead>
                  <tbody className="text-gray-200">
                    {a.activeSets.map((s, i) => {
                      const dur = s.started_at && s.ended_at ? s.ended_at - s.started_at : 0;
                      return (
                        <tr key={s.id} className="border-t border-ink-700">
                          <td className="py-2 pr-3">#{i + 1}</td>
                          <td className="py-2 pr-3">
                            <span className="flex items-center gap-2 capitalize">
                              <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: exColor(s.exercise) }} />
                              {s.exercise}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-right">{s.reps}</td>
                          <td className="py-2 pr-3 text-right">
                            <span style={{ color: formColor(s.form_score) }} className="font-semibold">
                              {Math.round(s.form_score * 100)}%
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-right">
                            <span
                              className="text-xs font-bold px-1.5 py-0.5 rounded"
                              style={{ background: formColor(s.form_score) + "22", color: formColor(s.form_score) }}
                            >
                              {formGrade(s.form_score)}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-right text-gray-400 text-xs">
                            {dur > 0 ? formatDuration(dur) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

            </div>
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
