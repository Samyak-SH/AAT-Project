import { FormEvent, useState } from "react";
import { askQuery } from "../api";

type QA = { q: string; a: string };

const EXAMPLES = [
  "How many curls did I do today?",
  "Which exercise had the worst form?",
  "What was my total rep count this session?",
];

export default function QueryBox() {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<QA[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const a = await askQuery(q);
      setHistory((h) => [{ q, a }, ...h].slice(0, 20));
      setQuestion("");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl bg-ink-800 p-6 shadow-xl border border-ink-700">
      <h2 className="text-xl font-semibold tracking-tight mb-3">Ask your coach</h2>

      <form onSubmit={submit} className="flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. how many squats did I do today?"
          className="flex-1 rounded-xl bg-ink-700 border border-ink-600 px-4 py-2.5 text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed text-ink-900 font-semibold px-5 py-2.5 transition"
        >
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((e) => (
          <button
            key={e}
            onClick={() => setQuestion(e)}
            className="text-xs rounded-full px-3 py-1 bg-ink-700 hover:bg-ink-600 text-gray-300 transition"
            type="button"
          >
            {e}
          </button>
        ))}
      </div>

      {err && <div className="mt-3 text-xs text-red-400">{err}</div>}

      <div className="mt-5 space-y-3 max-h-80 overflow-y-auto">
        {history.map((h, i) => (
          <div key={i} className="rounded-xl bg-ink-700/60 p-3">
            <div className="text-xs text-gray-400">You asked</div>
            <div className="text-sm">{h.q}</div>
            <div className="text-xs text-gray-400 mt-2">Answer</div>
            <div className="text-sm text-emerald-300 whitespace-pre-wrap">{h.a}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
