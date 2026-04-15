import { useEffect, useState } from "react";
import LiveCounter from "./components/LiveCounter";
import SessionHistory from "./components/SessionHistory";
import FloatingChat from "./components/FloatingChat";
import AuthPage from "./components/AuthPage";
import {
  getBasePublic,
  getMode,
  getUsername,
  health,
  isAuthenticated,
  logout,
  setMode,
  type Mode,
} from "./api";

type Tab = "live" | "history";

export default function App() {
  const [authed, setAuthed] = useState<boolean>(isAuthenticated());
  const [username, setUsername] = useState<string | null>(getUsername());
  const [tab, setTab] = useState<Tab>("live");
  const [mode, setModeState] = useState<Mode>(getMode());
  const [online, setOnline] = useState<boolean | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

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

  if (!authed) {
    return (
      <AuthPage
        onAuthenticated={(u) => {
          setUsername(u);
          setAuthed(true);
        }}
      />
    );
  }

  const toggleMode = () => {
    const next: Mode = mode === "live" ? "mock" : "live";
    setMode(next);
    setModeState(next);
  };

  const doLogout = () => {
    logout();
    setAuthed(false);
    setUsername(null);
    setMenuOpen(false);
  };

  const base = getBasePublic();

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
                {" · "}API: <span className="text-gray-300">{base || "same-origin"}</span>
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
              className="hidden md:inline-flex text-xs rounded-xl px-3 py-2 bg-ink-800 hover:bg-ink-700 border border-ink-700"
              title="Toggle between live backend and mock data"
            >
              {mode === "live" ? "Mock" : "Live"}
            </button>

            <div className="relative">
              <button
                onClick={() => setMenuOpen((v) => !v)}
                className="flex items-center gap-2 rounded-xl pl-2 pr-3 py-1.5 bg-ink-800 hover:bg-ink-700 border border-ink-700 text-sm"
              >
                <span className="w-7 h-7 rounded-full bg-emerald-500 text-ink-900 grid place-content-center font-bold text-xs">
                  {(username || "?").slice(0, 1).toUpperCase()}
                </span>
                <span className="hidden sm:inline">{username}</span>
                <span className="text-gray-500">▾</span>
              </button>
              {menuOpen && (
                <div
                  className="absolute right-0 mt-2 w-48 rounded-xl bg-ink-800 border border-ink-700 shadow-xl overflow-hidden z-20"
                  onMouseLeave={() => setMenuOpen(false)}
                >
                  <div className="px-3 py-2 text-xs text-gray-400 border-b border-ink-700">
                    Signed in as<br />
                    <span className="text-gray-100 font-semibold">{username}</span>
                  </div>
                  <button
                    onClick={toggleMode}
                    className="md:hidden w-full text-left px-3 py-2 text-sm hover:bg-ink-700"
                  >
                    Switch to {mode === "live" ? "mock" : "live"}
                  </button>
                  <button
                    onClick={doLogout}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-ink-700 text-red-300"
                  >
                    Log out
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-6 grid grid-cols-1 gap-6">
        {tab === "live" ? <LiveCounter /> : <SessionHistory />}
      </main>

      <footer className="max-w-6xl mx-auto px-6 py-6 text-xs text-gray-500">
        ESP32 + ADXL345 · FastAPI + TF · React + Tailwind
      </footer>

      <FloatingChat />
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
