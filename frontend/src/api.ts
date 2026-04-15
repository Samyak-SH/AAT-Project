// Thin API client with a mock-mode fallback so the frontend runs standalone.

export type LiveState = {
  exercise: string;
  confidence: number;
  reps: number;
  form_score: number;
  session_id: number | null;
  updated_at: number;
  model_mode?: string;
};

export type SetRow = {
  id: number;
  session_id: number;
  exercise: string;
  reps: number;
  form_score: number;
  started_at: number;
  ended_at: number | null;
};

export type SessionSummary = {
  id: number;
  user: string;
  started_at: number;
  ended_at: number | null;
  sets: SetRow[];
  reps_per_exercise: Record<string, number>;
  form_per_exercise: Record<string, number>;
  total_reps: number;
};

export type SessionListEntry = {
  id: number;
  user: string;
  started_at: number;
  ended_at: number | null;
};

// In dev (`vite`), default to the backend on :8000.
// In prod builds (Docker/nginx), use same-origin so nginx can reverse-proxy /api/.
const DEFAULT_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");

function getBase(): string {
  try {
    const stored = localStorage.getItem("api_base");
    if (stored !== null) return stored;
  } catch { /* ignore */ }
  return DEFAULT_BASE;
}

function getToken(): string | null {
  try {
    return localStorage.getItem("jwt") || null;
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem("jwt", token);
    else localStorage.removeItem("jwt");
  } catch { /* ignore */ }
}

export function setBase(base: string) {
  try { localStorage.setItem("api_base", base); } catch { /* ignore */ }
}

export function getBasePublic(): string {
  return getBase();
}

async function rawFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(getBase() + path, { ...init, headers });
  if (!resp.ok) {
    let detail = "";
    try {
      const j = await resp.json();
      detail = j.error || JSON.stringify(j);
    } catch {
      detail = await resp.text();
    }
    throw new Error(`HTTP ${resp.status}: ${detail}`);
  }
  return (await resp.json()) as T;
}

// ------------------------- Mock state -------------------------

type MockStore = {
  live: LiveState;
  sessions: SessionSummary[];
  nextSessionId: number;
  nextSetId: number;
  tickTimer: number | null;
};

const mock: MockStore = {
  live: {
    exercise: "rest",
    confidence: 0,
    reps: 0,
    form_score: 0,
    session_id: null,
    updated_at: Date.now() / 1000,
    model_mode: "mock",
  },
  sessions: [
    {
      id: 1,
      user: "demo",
      started_at: Date.now() / 1000 - 3600,
      ended_at: Date.now() / 1000 - 3400,
      total_reps: 22,
      reps_per_exercise: { curl: 12, squat: 10 },
      form_per_exercise: { curl: 0.92, squat: 0.81 },
      sets: [
        { id: 1, session_id: 1, exercise: "curl", reps: 12, form_score: 0.92,
          started_at: Date.now() / 1000 - 3600, ended_at: Date.now() / 1000 - 3540 },
        { id: 2, session_id: 1, exercise: "squat", reps: 10, form_score: 0.81,
          started_at: Date.now() / 1000 - 3540, ended_at: Date.now() / 1000 - 3480 },
      ],
    },
  ],
  nextSessionId: 2,
  nextSetId: 4,
  tickTimer: null,
};

function mockAdvance() {
  // Rotate exercises and increment reps so the dashboard moves in mock mode.
  const cycle = ["curl", "curl", "rest", "squat", "squat", "rest"];
  const t = Math.floor(Date.now() / 1000) % cycle.length;
  const ex = cycle[t];
  const conf = ex === "rest" ? 0.55 + 0.1 * Math.sin(Date.now() / 500)
                             : 0.82 + 0.1 * Math.sin(Date.now() / 700);
  mock.live = {
    ...mock.live,
    exercise: ex,
    confidence: Math.max(0, Math.min(1, conf)),
    reps: mock.live.reps + (ex !== "rest" && Math.random() < 0.15 ? 1 : 0),
    form_score: Math.max(0, Math.min(1, 0.75 + 0.2 * Math.sin(Date.now() / 3000))),
    updated_at: Date.now() / 1000,
  };
}

// ------------------------- Public API -------------------------

export type Mode = "live" | "mock";

let mode: Mode = "live";

export function getMode(): Mode { return mode; }
export function setMode(m: Mode) {
  mode = m;
  try { localStorage.setItem("mode", m); } catch { /* ignore */ }
}
(() => {
  try {
    const s = localStorage.getItem("mode");
    if (s === "live" || s === "mock") mode = s;
  } catch { /* ignore */ }
})();

export function getUsername(): string | null {
  try { return localStorage.getItem("username"); } catch { return null; }
}
function setUsername(u: string | null) {
  try {
    if (u) localStorage.setItem("username", u);
    else localStorage.removeItem("username");
  } catch { /* ignore */ }
}

export function isAuthenticated(): boolean {
  try { return !!localStorage.getItem("jwt"); } catch { return false; }
}

export function logout() {
  setToken(null);
  setUsername(null);
}

export async function login(username: string, password: string): Promise<string> {
  if (mode === "mock") {
    setToken("mock-token");
    setUsername(username || "demo");
    return username || "demo";
  }
  const r = await rawFetch<{ token: string; username: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(r.token);
  setUsername(r.username);
  return r.username;
}

export async function signup(username: string, password: string): Promise<string> {
  if (mode === "mock") {
    setToken("mock-token");
    setUsername(username);
    return username;
  }
  const r = await rawFetch<{ token: string; username: string }>("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(r.token);
  setUsername(r.username);
  return r.username;
}

export async function fetchLive(): Promise<LiveState> {
  if (mode === "mock") {
    mockAdvance();
    return { ...mock.live };
  }
  return rawFetch<LiveState>("/api/live");
}

export async function startSession(): Promise<number> {
  if (mode === "mock") {
    const id = mock.nextSessionId++;
    mock.live.session_id = id;
    mock.live.reps = 0;
    mock.sessions.unshift({
      id, user: "demo", started_at: Date.now() / 1000, ended_at: null,
      sets: [], reps_per_exercise: {}, form_per_exercise: {}, total_reps: 0,
    });
    return id;
  }
  const r = await rawFetch<{ session_id: number }>("/api/session/start", { method: "POST" });
  return r.session_id;
}

export async function endSession(): Promise<number | null> {
  if (mode === "mock") {
    const sid = mock.live.session_id;
    if (sid == null) return null;
    const s = mock.sessions.find(x => x.id === sid);
    if (s) s.ended_at = Date.now() / 1000;
    mock.live.session_id = null;
    return sid;
  }
  try {
    const r = await rawFetch<{ session_id: number }>("/api/session/end", { method: "POST" });
    return r.session_id;
  } catch {
    return null;
  }
}

export async function fetchSession(id: number): Promise<SessionSummary> {
  if (mode === "mock") {
    const s = mock.sessions.find(x => x.id === id);
    if (!s) throw new Error(`Session ${id} not found`);
    return s;
  }
  return rawFetch<SessionSummary>(`/api/session/${id}`);
}

export async function fetchSessions(): Promise<SessionListEntry[]> {
  if (mode === "mock") {
    return mock.sessions.map(({ id, user, started_at, ended_at }) => ({ id, user, started_at, ended_at }));
  }
  const r = await rawFetch<{ sessions: SessionListEntry[] }>("/api/sessions");
  return r.sessions;
}

// Stream tokens from /api/query/stream. Yields string chunks as they arrive.
// In mock mode simulates streaming by splitting a fake answer into words.
export async function* askQueryStream(
  question: string,
  signal?: AbortSignal,
): AsyncGenerator<string, void, void> {
  if (mode === "mock") {
    const answer = await askQuery(question);
    const tokens = answer.split(/(\s+)/);
    for (const t of tokens) {
      if (signal?.aborted) return;
      await new Promise((r) => setTimeout(r, 35));
      yield t;
    }
    return;
  }

  const token = getTokenPublic();
  const resp = await fetch(getBase() + "/api/query/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`;
    try { detail += `: ${(await resp.json()).error || ""}`; } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      // Each ndjson line is one event
      // eslint-disable-next-line no-cond-assign
      while ((idx = buf.indexOf("\n")) !== -1) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.type === "token" && typeof obj.content === "string") {
            yield obj.content;
          } else if (obj.type === "error") {
            yield `\n[error: ${obj.message}]`;
          }
        } catch { /* skip malformed */ }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }
}

function getTokenPublic(): string | null {
  try { return localStorage.getItem("jwt"); } catch { return null; }
}

export async function askQuery(question: string): Promise<string> {
  if (mode === "mock") {
    const q = question.toLowerCase();
    const totals: Record<string, number> = {};
    for (const sess of mock.sessions) {
      for (const k of Object.keys(sess.reps_per_exercise)) {
        totals[k] = (totals[k] || 0) + sess.reps_per_exercise[k];
      }
    }
    if (q.includes("curl")) return `You've done ${totals.curl || 0} curls (mock).`;
    if (q.includes("squat")) return `You've done ${totals.squat || 0} squats (mock).`;
    if (q.includes("worst") && q.includes("form")) {
      let worst: [string, number] = ["none", 1];
      for (const s of mock.sessions) {
        for (const [k, v] of Object.entries(s.form_per_exercise)) {
          if (v < worst[1]) worst = [k, v];
        }
      }
      return `Worst form: ${worst[0]} at ${worst[1].toFixed(2)} (mock).`;
    }
    return `Total reps across all sessions: ${Object.values(totals).reduce((a,b)=>a+b,0)} (mock).`;
  }
  const r = await rawFetch<{ answer: string }>("/api/query", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
  return r.answer;
}

export async function health(): Promise<boolean> {
  if (mode === "mock") return true;
  try {
    await rawFetch<{ ok: boolean }>("/api/health");
    return true;
  } catch {
    return false;
  }
}
