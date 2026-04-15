import { FormEvent, useState } from "react";
import { getBasePublic, login, setBase, signup, type Mode, getMode, setMode } from "../api";

type Tab = "login" | "signup";

type Props = {
  onAuthenticated: (username: string) => void;
};

export default function AuthPage({ onAuthenticated }: Props) {
  const [tab, setTab] = useState<Tab>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [apiBase, setApiBase] = useState(getBasePublic());
  const [mode, setModeState] = useState<Mode>(getMode());

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!username || !password) {
      setErr("Enter a username and password.");
      return;
    }
    if (tab === "signup") {
      if (password.length < 6) {
        setErr("Password must be at least 6 characters.");
        return;
      }
      if (password !== confirm) {
        setErr("Passwords don't match.");
        return;
      }
    }
    setBusy(true);
    try {
      const u = tab === "login"
        ? await login(username, password)
        : await signup(username, password);
      onAuthenticated(u);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  };

  const applyBase = () => {
    setBase(apiBase);
  };

  const toggleMode = () => {
    const next: Mode = mode === "live" ? "mock" : "live";
    setMode(next);
    setModeState(next);
  };

  return (
    <div className="min-h-screen relative overflow-hidden bg-ink-900">
      {/* Background flourish */}
      <div className="pointer-events-none absolute inset-0 -z-0">
        <div className="absolute -top-40 -left-40 w-[40rem] h-[40rem] rounded-full bg-emerald-500/10 blur-3xl" />
        <div className="absolute -bottom-40 -right-40 w-[40rem] h-[40rem] rounded-full bg-sky-500/10 blur-3xl" />
        <div className="absolute inset-0 opacity-[0.04] [background-image:linear-gradient(rgba(255,255,255,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.5)_1px,transparent_1px)] [background-size:32px_32px]" />
      </div>

      <div className="relative z-10 min-h-screen grid md:grid-cols-2">
        {/* Left: brand / pitch */}
        <div className="hidden md:flex flex-col justify-between p-12 border-r border-ink-700/60">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500 grid place-content-center text-ink-900 font-black text-lg">
              G
            </div>
            <div className="text-lg font-semibold tracking-tight">Gym Rep Counter</div>
          </div>

          <div>
            <h1 className="text-4xl font-semibold tracking-tight leading-tight">
              Count reps.<br />
              Score form.<br />
              <span className="text-emerald-400">Coach yourself.</span>
            </h1>
            <p className="mt-6 text-gray-400 max-w-sm">
              An ESP32 on your wrist, a 1-D CNN in the cloud, and a local LLM
              that answers questions about your session. All yours.
            </p>

            <ul className="mt-8 space-y-3 text-sm text-gray-300">
              <Feature>Live rep counter with form scoring</Feature>
              <Feature>Per-set history + trends</Feature>
              <Feature>Natural-language coach (local or Claude)</Feature>
            </ul>
          </div>

          <div className="text-xs text-gray-500">
            ESP32 + ADXL345 · FastAPI · React · Tailwind
          </div>
        </div>

        {/* Right: auth card */}
        <div className="flex items-center justify-center p-6">
          <div className="w-full max-w-md">
            <div className="md:hidden flex items-center gap-3 mb-6">
              <div className="w-10 h-10 rounded-xl bg-emerald-500 grid place-content-center text-ink-900 font-black text-lg">
                G
              </div>
              <div className="text-lg font-semibold">Gym Rep Counter</div>
            </div>

            <div className="rounded-2xl bg-ink-800/80 backdrop-blur border border-ink-700 shadow-2xl p-7">
              {/* Tabs */}
              <div className="flex bg-ink-700/60 rounded-xl p-1 mb-6">
                <TabButton active={tab === "login"} onClick={() => { setTab("login"); setErr(null); }}>
                  Log in
                </TabButton>
                <TabButton active={tab === "signup"} onClick={() => { setTab("signup"); setErr(null); }}>
                  Sign up
                </TabButton>
              </div>

              <h2 className="text-xl font-semibold mb-1">
                {tab === "login" ? "Welcome back" : "Create your account"}
              </h2>
              <p className="text-sm text-gray-400 mb-6">
                {tab === "login"
                  ? "Log in to continue your training."
                  : "Username can be anything you want — this is a local app."}
              </p>

              <form onSubmit={submit} className="space-y-4">
                <Field label="Username">
                  <input
                    type="text"
                    autoComplete="username"
                    autoFocus
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full bg-ink-700 border border-ink-600 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    placeholder="e.g. rudy"
                  />
                </Field>

                <Field label="Password">
                  <input
                    type="password"
                    autoComplete={tab === "login" ? "current-password" : "new-password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-ink-700 border border-ink-600 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    placeholder={tab === "signup" ? "at least 6 characters" : ""}
                  />
                </Field>

                {tab === "signup" && (
                  <Field label="Confirm password">
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={confirm}
                      onChange={(e) => setConfirm(e.target.value)}
                      className="w-full bg-ink-700 border border-ink-600 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                  </Field>
                )}

                {err && (
                  <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
                    {err}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={busy}
                  className="w-full rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 disabled:cursor-not-allowed text-ink-900 font-semibold py-2.5 transition-colors"
                >
                  {busy ? "…" : tab === "login" ? "Log in" : "Create account"}
                </button>

                <div className="text-center text-xs text-gray-400">
                  {tab === "login" ? (
                    <>No account?{" "}
                      <button type="button" onClick={() => setTab("signup")} className="text-emerald-400 hover:underline">
                        Sign up
                      </button>
                    </>
                  ) : (
                    <>Already have an account?{" "}
                      <button type="button" onClick={() => setTab("login")} className="text-emerald-400 hover:underline">
                        Log in
                      </button>
                    </>
                  )}
                </div>
              </form>

              {/* Advanced toggle */}
              <div className="mt-6 pt-5 border-t border-ink-700">
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="w-full flex items-center justify-between text-xs text-gray-400 hover:text-gray-200 transition"
                >
                  <span>Connection settings</span>
                  <span>{showAdvanced ? "▴" : "▾"}</span>
                </button>

                {showAdvanced && (
                  <div className="mt-3 space-y-3">
                    <Field label="API base">
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={apiBase}
                          onChange={(e) => setApiBase(e.target.value)}
                          className="flex-1 bg-ink-700 border border-ink-600 rounded-lg px-3 py-2 text-xs"
                          placeholder="http://localhost:8000 or empty for same-origin"
                        />
                        <button
                          type="button"
                          onClick={applyBase}
                          className="rounded-lg bg-ink-700 hover:bg-ink-600 px-3 py-2 text-xs"
                        >
                          Save
                        </button>
                      </div>
                    </Field>

                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <span>
                        Mode: <span className="text-emerald-300 font-semibold">{mode}</span>
                      </span>
                      <button
                        type="button"
                        onClick={toggleMode}
                        className="rounded-lg bg-ink-700 hover:bg-ink-600 px-3 py-1.5"
                      >
                        Switch to {mode === "live" ? "mock" : "live"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="text-center text-xs text-gray-500 mt-4">
              Demo: <span className="text-gray-300 font-mono">admin</span> /{" "}
              <span className="text-gray-300 font-mono">admin</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "flex-1 rounded-lg py-2 text-sm transition " +
        (active ? "bg-emerald-500 text-ink-900 font-semibold shadow" : "text-gray-300 hover:text-white")
      }
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-gray-400 mb-1.5">{label}</div>
      {children}
    </label>
  );
}

function Feature({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <svg viewBox="0 0 20 20" className="w-5 h-5 shrink-0 mt-0.5 text-emerald-400" fill="currentColor">
        <path d="M16.707 5.293a1 1 0 010 1.414l-7.5 7.5a1 1 0 01-1.414 0l-3.5-3.5a1 1 0 111.414-1.414L8.5 12.086l6.793-6.793a1 1 0 011.414 0z" />
      </svg>
      <span>{children}</span>
    </li>
  );
}
