import { useEffect, useState } from "react";
import type { LiveState } from "../api";
import { endSession, fetchLive, startSession } from "../api";

type Props = {
  onSessionChange?: (id: number | null) => void;
};

function formColor(score: number): string {
  if (score >= 0.85) return "bg-green-500";
  if (score >= 0.60) return "bg-yellow-400";
  return "bg-red-500";
}

function formLabel(score: number): string {
  if (score >= 0.85) return "Excellent";
  if (score >= 0.60) return "OK";
  return "Poor";
}

export default function LiveCounter({ onSessionChange }: Props) {
  const [state, setState] = useState<LiveState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await fetchLive();
        if (!alive) return;
        setState(s);
        setErr(null);
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const id = window.setInterval(tick, 500);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const onStart = async () => {
    setBusy(true);
    try {
      const id = await startSession();
      onSessionChange?.(id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onEnd = async () => {
    setBusy(true);
    try {
      const id = await endSession();
      onSessionChange?.(id ?? null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const score = state?.form_score ?? 0;
  const pct = Math.round(score * 100);
  const ex = state?.exercise;
  const isOther = ex === "other";
  const isRest = ex === "rest";

  const exerciseLabel = isOther
    ? "Unknown motion"
    : ex
      ? ex[0].toUpperCase() + ex.slice(1)
      : "—";
  const exerciseHint = isOther
    ? "Not a tracked exercise"
    : isRest
      ? "Ready"
      : ex
        ? "Counting reps"
        : "";
  const exerciseColor = isOther
    ? "text-amber-300"
    : isRest
      ? "text-gray-300"
      : "text-emerald-300";

  return (
    <div className="rounded-2xl bg-ink-800 p-6 shadow-xl border border-ink-700">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold tracking-tight">Live</h2>
        <div className="flex items-center gap-2 text-xs">
          <span
            className={
              "inline-block w-2 h-2 rounded-full " +
              (err ? "bg-red-500" : "bg-emerald-400")
            }
          />
          <span className="text-gray-400">
            {err ? "Disconnected" : "Connected"}
            {state?.model_mode ? ` · ${state.model_mode}` : ""}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 mb-6">
        <div
          className={
            "rounded-xl p-4 border transition-colors " +
            (isOther
              ? "bg-amber-500/10 border-amber-500/30"
              : "bg-ink-700 border-transparent")
          }
        >
          <div className="text-sm text-gray-400 flex items-center gap-1.5">
            Exercise
            {isOther && (
              <span className="inline-block text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded-md bg-amber-500/20 text-amber-300">
                other
              </span>
            )}
          </div>
          <div className={"text-3xl font-semibold mt-1 " + exerciseColor}>
            {exerciseLabel}
          </div>
          <div className="text-xs text-gray-400 mt-1">
            {exerciseHint}
            {exerciseHint && " · "}
            Confidence: {state ? (state.confidence * 100).toFixed(0) + "%" : "—"}
          </div>
        </div>
        <div className="rounded-xl bg-ink-700 p-4">
          <div className="text-sm text-gray-400">Reps (current set)</div>
          <div className="text-5xl font-bold mt-1">{state?.reps ?? 0}</div>
          <div className="text-xs text-gray-400 mt-1">
            Session: {state?.session_id ?? "not started"}
          </div>
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm text-gray-400">Form score</div>
        <div className="text-sm">
          <span className="font-semibold">{pct}%</span>
          <span className="text-gray-400"> · {formLabel(score)}</span>
        </div>
      </div>
      <div className="h-3 rounded-full bg-ink-700 overflow-hidden">
        <div
          className={"h-full transition-all duration-300 " + formColor(score)}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex gap-3 mt-6">
        <button
          onClick={onStart}
          disabled={busy || !!state?.session_id}
          className="flex-1 rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed text-ink-900 font-semibold py-2.5 transition"
        >
          Start session
        </button>
        <button
          onClick={onEnd}
          disabled={busy || !state?.session_id}
          className="flex-1 rounded-xl bg-red-500 hover:bg-red-400 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-2.5 transition"
        >
          End session
        </button>
      </div>

      {err && (
        <div className="mt-4 text-xs text-red-400 break-all">
          {err}
        </div>
      )}
    </div>
  );
}
