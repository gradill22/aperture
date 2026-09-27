import { useEffect, useRef, useState } from "react";
import { clearChat, sendChat, stopChat } from "../actions";
import { useStore } from "../store";
import type { ToolTrace } from "../types";

const EXAMPLES = [
  "Which aircraft passed within 2 km of Reagan National between 14:00 and 15:00 UTC?",
  "Show me the track of N101HQ.",
  "Find military airfields near Andrews.",
];

function Tools({ tools }: { tools: ToolTrace[] }) {
  if (!tools.length) return null;
  return (
    <div className="tools">
      {tools.map((t, i) => (
        <span
          key={i}
          className={t.ok ? "tool" : "tool failed"}
          title={`${JSON.stringify(t.arguments)}${t.error ? `\n${t.error}` : ""}`}
        >
          {t.name}
        </span>
      ))}
    </div>
  );
}

function Elapsed() {
  const [s, setS] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setS((v) => v + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{s}s</>;
}

export function ChatPanel() {
  const chat = useStore((s) => s.chat);
  const pending = useStore((s) => s.chatPending);
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [chat.length, pending]);

  const submit = () => {
    if (!text.trim() || pending) return;
    void sendChat(text);
    setText("");
  };

  return (
    <div className="panel chat" data-testid="chat-panel">
      <div className="messages">
        {chat.length === 0 && (
          <div className="hint">
            <p>Ask about the loaded day. Answers come from the local database via tools; the model runs in LM Studio.</p>
            {EXAMPLES.map((q) => (
              <button key={q} className="example" onClick={() => void sendChat(q)} disabled={pending}>
                {q}
              </button>
            ))}
          </div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`msg ${m.role}${m.error ? " error" : ""}`}>
            {m.tools && <Tools tools={m.tools} />}
            <div className="text">{m.content}</div>
          </div>
        ))}
        {pending && (
          <div className="msg assistant pending">
            Thinking… <Elapsed /> <button onClick={stopChat}>Stop</button>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          data-testid="chat-input"
          rows={2}
          value={text}
          placeholder="Ask about aircraft, places, geofences…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="composer-actions">
          <button type="button" onClick={clearChat} disabled={!chat.length && !pending}>
            Clear
          </button>
          <button type="submit" className="primary" disabled={pending || !text.trim()}>
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
