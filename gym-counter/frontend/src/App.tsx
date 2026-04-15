import { useEffect, useState } from "react";
import LiveCounter from "./components/LiveCounter";
import SessionHistory from "./components/SessionHistory";
import QueryBox from "./components/QueryBox";
import {
  getBasePublic,
  getMode,
  health,
  login,
  setBase,
  setMode,
  setToken,
  type Mode,
} from "./api";

type Tab = "live" | "history";

export default function App() {
  const [tab, setTab] = useState<Tab>("live");
  const [mode, setModeState] = useState<Mode>(getMode());
  const [base, setBaseState] = useState<string>(getBasePublic());
  const [online, setOnline] = useState<boolean | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [authMsg, setAuthMsg] = useState<string | null>(null);
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("admin");

  useEffect(() => {
    (async () => {
      const ok = await health();
      setOnline(ok);
      if (!ok && mode === "live") {
        setMode("mock");
        setModeState("mock");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleMode = () => {
    const next: Mode = mode === "live" ? "mock" : "live";
    setMode(next);
    setModeState(next);
  };

  const doLogin = async () => {
    setAuthMsg(null);
    try {
      await login(user, pass);
      setAuthMsg("Logged in.");
      setAuthOpen(false);
    } catch (e) {
      setAuthMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const doLogout = () => {
    setToken(null);
    setAuthMsg("Logged out.");
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-ink-900 via-ink-900 to-ink-800">
      <header className="sticky top-0 z-10 backdrop-blur bg-ink-900/80 border-b border-ink-700">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-emerald-500 grid place-content-center text-ink-900 font-black text-lg">
              G
            </div>
            <div>
              <div className="font-semibold">Gym Rep Counter</div>
              <div className="text-xs text-gray-400">
                Mode: <span className="text-emerald-300">{mode}</span>
                {" · "}API: <span className="text-gray-300">{base}</span>
                {" · "}
                {online == null ? "checking…" : online ? (
                  <span className="text-emerald-400">online</span>
                ) : (
                  <span className="text-red-400">offline</span>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <nav className="bg-ink-800 rounded-xl p-1 text-sm">
              <TabBtn active={tab === "live"} onClick={() => setTab("live")}>Live</TabBtn>
              <TabBtn active={tab === "history"} onClick={() => setTab("history")}>History</TabBtn>
            </nav>
            <button
              onClick={toggleMode}
              className="text-xs rounded-xl px-3 py-2 bg-ink-800 hover:bg-ink-700 border border-ink-700"
              title="Toggle between live backend and mock data"
            >
              {mode === "live" ? "Switch to mock" : "Switch to live"}
            </button>
            <button
              onClick={() => setAuthOpen((v) => !v)}
              className="text-xs rounded-xl px-3 py-2 bg-ink-800 hover:bg-ink-700 border border-ink-700"
            >
              Login
            </button>
          </div>
        </div>

        {authOpen && (
          <div className="max-w-6xl mx-auto px-6 pb-4">
            <div className="rounded-xl bg-ink-800 border border-ink-700 p-4 flex flex-col md:flex-row gap-3 md:items-end">
              <Field label="API base">
                <input
                  className="bg-ink-700 rounded-lg px-3 py-2 text-sm w-72"
                  value={base}
                  onChange={(e) => setBaseState(e.target.value)}
                  onBlur={() => setBase(base)}
                />
              </Field>
              <Field label="Username">
                <input
                  className="bg-ink-700 rounded-lg px-3 py-2 text-sm"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                />
              </Field>
              <Field label="Password">
                <input
                  type="password"
                  className="bg-ink-700 rounded-lg px-3 py-2 text-sm"
                  value={pass}
                  onChange={(e) => setPass(e.target.value)}
                />
              </Field>
              <button
                onClick={doLogin}
                className="rounded-lg bg-emerald-500 hover:bg-emerald-400 text-ink-900 font-semibold px-4 py-2 text-sm"
              >
                Log in
              </button>
              <button
                onClick={doLogout}
                className="rounded-lg bg-ink-700 hover:bg-ink-600 text-gray-200 px-4 py-2 text-sm"
              >
                Log out
              </button>
              {authMsg && <div className="text-xs text-gray-300">{authMsg}</div>}
            </div>
          </div>
        )}
      </header>

      <main className="max-w-6xl mx-auto px-6 py-6 grid grid-cols-1 gap-6">
        {tab === "live" ? <LiveCounter /> : <SessionHistory />}
        <QueryBox />
      </main>

      <footer className="max-w-6xl mx-auto px-6 py-6 text-xs text-gray-500">
        ESP32 + ADXL345 · FastAPI + TF · React + Tailwind
      </footer>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={
        "px-3 py-1.5 rounded-lg transition " +
        (active ? "bg-emerald-500 text-ink-900 font-semibold" : "text-gray-300 hover:text-white")
      }
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-gray-400">{label}</span>
      {children}
    </label>
  );
}
