import { FormEvent, useEffect, useRef, useState } from "react";
import { askQueryStream, getUsername } from "../api";

type Msg = {
  id: number;
  role: "user" | "assistant";
  text: string;
  status?: "thinking" | "streaming" | "done" | "error";
};

const SUGGESTIONS = [
  "how did I do today?",
  "which exercise had the worst form?",
  "how many curls this week?",
];

export default function FloatingChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const idRef = useRef<number>(1);
  const username = getUsername() || "you";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, open]);

  // Greet on first open
  useEffect(() => {
    if (open && messages.length === 0) {
      setMessages([{
        id: idRef.current++,
        role: "assistant",
        text: `Hey ${username}! Ask me about your workouts.`,
        status: "done",
      }]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const send = async (raw: string) => {
    const q = raw.trim();
    if (!q || busy) return;
    setInput("");

    const userMsgId = idRef.current++;
    const aiMsgId = idRef.current++;
    setMessages((m) => [
      ...m,
      { id: userMsgId, role: "user", text: q, status: "done" },
      { id: aiMsgId, role: "assistant", text: "", status: "thinking" },
    ]);

    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      let gotAny = false;
      for await (const chunk of askQueryStream(q, ac.signal)) {
        if (!gotAny) {
          gotAny = true;
          setMessages((m) =>
            m.map((x) => (x.id === aiMsgId ? { ...x, status: "streaming" } : x)),
          );
        }
        setMessages((m) =>
          m.map((x) =>
            x.id === aiMsgId ? { ...x, text: x.text + chunk } : x,
          ),
        );
      }
      setMessages((m) =>
        m.map((x) =>
          x.id === aiMsgId
            ? { ...x, status: "done", text: x.text || "(no response)" }
            : x,
        ),
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((m) =>
        m.map((x) =>
          x.id === aiMsgId
            ? { ...x, status: "error", text: `⚠ ${msg}` }
            : x,
        ),
      );
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setBusy(false);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  const clearChat = () => {
    setMessages([]);
  };

  return (
    <>
      {/* Floating toggle button */}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close chat" : "Open chat"}
        className={
          "fixed bottom-6 right-6 z-40 w-14 h-14 rounded-full shadow-xl transition " +
          "flex items-center justify-center " +
          (open
            ? "bg-ink-700 hover:bg-ink-600 text-gray-200"
            : "bg-emerald-500 hover:bg-emerald-400 text-ink-900")
        }
      >
        {open ? (
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor">
            <path d="M4 4h16a2 2 0 012 2v10a2 2 0 01-2 2H8l-4 4V6a2 2 0 012-2z" />
          </svg>
        )}
      </button>

      {/* Chat panel */}
      <div
        className={
          "fixed bottom-24 right-6 z-40 w-[22rem] max-w-[calc(100vw-2rem)] h-[32rem] " +
          "rounded-2xl border border-ink-700 bg-ink-800/95 backdrop-blur shadow-2xl " +
          "flex flex-col overflow-hidden origin-bottom-right " +
          "transition-all duration-200 " +
          (open
            ? "opacity-100 scale-100 pointer-events-auto"
            : "opacity-0 scale-95 pointer-events-none")
        }
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-ink-700 bg-ink-900/40">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-emerald-500 grid place-content-center text-ink-900 font-bold">
              G
            </div>
            <div>
              <div className="text-sm font-semibold">Coach</div>
              <div className="text-[11px] text-gray-400">
                {busy ? "typing…" : "asks about your workouts"}
              </div>
            </div>
          </div>
          <button
            onClick={clearChat}
            className="text-xs text-gray-400 hover:text-gray-200"
            title="Clear chat"
          >
            Clear
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3 scroll-smooth">
          {messages.map((m) => (
            <Bubble key={m.id} msg={m} username={username} />
          ))}
          {messages.length === 1 && !busy && (
            <div className="pt-2 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="text-[11px] rounded-full px-2.5 py-1 bg-ink-700 hover:bg-ink-600 text-gray-300 transition"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* Input */}
        <form onSubmit={onSubmit} className="p-3 border-t border-ink-700 flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask me anything…"
            className="flex-1 rounded-xl bg-ink-700 border border-ink-600 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            disabled={busy}
          />
          {busy ? (
            <button
              type="button"
              onClick={stop}
              className="rounded-xl bg-red-500 hover:bg-red-400 text-white font-semibold px-3.5 text-sm"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed text-ink-900 font-semibold px-4 text-sm transition"
            >
              Send
            </button>
          )}
        </form>
      </div>
    </>
  );
}

function Bubble({ msg, username }: { msg: Msg; username: string }) {
  const isUser = msg.role === "user";
  return (
    <div className={"flex gap-2 items-start " + (isUser ? "flex-row-reverse" : "")}>
      <div
        className={
          "w-7 h-7 shrink-0 rounded-full grid place-content-center font-bold text-[11px] " +
          (isUser ? "bg-sky-500 text-ink-900" : "bg-emerald-500 text-ink-900")
        }
        title={isUser ? username : "Coach"}
      >
        {isUser ? username.slice(0, 1).toUpperCase() : "G"}
      </div>
      <div
        className={
          "max-w-[78%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed shadow-sm " +
          (isUser
            ? "bg-sky-500/90 text-ink-900 rounded-tr-sm"
            : msg.status === "error"
              ? "bg-red-500/15 border border-red-500/30 text-red-200 rounded-tl-sm"
              : "bg-ink-700 text-gray-100 rounded-tl-sm")
        }
      >
        {msg.status === "thinking" ? (
          <Thinking />
        ) : (
          <span className="whitespace-pre-wrap break-words">
            {msg.text}
            {msg.status === "streaming" && <Caret />}
          </span>
        )}
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <span className="inline-flex items-center gap-1 py-1">
      <Dot delay="0s" />
      <Dot delay="0.15s" />
      <Dot delay="0.3s" />
    </span>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-gray-400 inline-block animate-bounce"
      style={{ animationDelay: delay, animationDuration: "0.9s" }}
    />
  );
}

function Caret() {
  return (
    <span className="inline-block w-[7px] h-[1em] align-[-2px] ml-[1px] bg-gray-300 animate-pulse" />
  );
}
