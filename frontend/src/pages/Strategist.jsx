import { useEffect, useRef, useState } from "react";
import { ArrowUp, Bot, Loader, Square, User, Wrench } from "lucide-react";

import { useAudit } from "../App.jsx";
import { windowQuery } from "../lib/api.js";

/**
 * Chat with the strategist.
 *
 * Streams over SSE so tool activity is visible while it happens -- the model
 * often spends 10-20 seconds reading placements and ad sets before it writes
 * anything, and a bare spinner for that long reads as broken.
 *
 * The transcript lives in component state only. It is deliberately not
 * persisted: the answers are tied to one reporting window, and showing
 * yesterday's conclusions against today's numbers would be worse than losing
 * them.
 */

const TOOL_LABEL = {
  get_account_overview: "Reading account overview",
  list_campaigns: "Reading campaigns",
  list_adsets: "Reading ad sets",
  list_ads: "Reading ads and creatives",
  get_breakdown: "Reading breakdowns",
  get_audit_findings: "Reading audit findings",
};

export default function Strategist() {
  const { accountId, window: win, data } = useAudit();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [suggestions, setSuggestions] = useState([]);

  const abortRef = useRef(null);
  const endRef = useRef(null);
  const boxRef = useRef(null);

  useEffect(() => {
    fetch(`/api/strategy/suggestions?account_id=${encodeURIComponent(accountId)}`, {
      credentials: "same-origin",
    })
      .then((r) => r.json())
      .then((r) => setSuggestions(r.suggestions || []))
      .catch(() => {});
  }, [accountId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  // Abort any in-flight stream when the account or window changes -- the reply
  // would be answering a question about data that is no longer on screen.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, [accountId, win]);

  async function send(text) {
    const question = (text ?? input).trim();
    if (!question || busy) return;

    const next = [...messages, { role: "user", content: question }];
    setMessages([...next, { role: "assistant", content: "", tools: [] }]);
    setInput("");
    setBusy(true);
    setError(null);
    setStatus("Thinking");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch("/api/strategy/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          account_id: accountId,
          messages: next,
          ...Object.fromEntries(new URLSearchParams(windowQuery(win))),
        }),
      });

      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail?.message || `Request failed (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // SSE frames are separated by a blank line and can split across chunks,
      // so the tail stays in the buffer until its terminator arrives.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let event;
          try {
            event = JSON.parse(line.slice(6));
          } catch {
            continue;
          }

          if (event.type === "thinking") setStatus("Thinking");
          else if (event.type === "tool") {
            setStatus(TOOL_LABEL[event.name] || `Running ${event.name}`);
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = { ...last, tools: [...(last.tools || []), event.name] };
              return copy;
            });
          } else if (event.type === "text") {
            setStatus(null);
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = { ...last, content: last.content + event.text };
              return copy;
            });
          } else if (event.type === "error") {
            setError(event.message);
          }
        }
      }
    } catch (cause) {
      if (cause.name !== "AbortError") setError(cause.message);
    } finally {
      setBusy(false);
      setStatus(null);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
    setStatus(null);
  }

  const empty = messages.length === 0;

  return (
    <div className="chat">
      <div className="chat-scroll" ref={boxRef}>
        {empty && (
          <div className="chat-intro">
            <span className="chat-avatar is-bot">
              <Bot size={20} />
            </span>
            <h2>Your marketing engineer</h2>
            <p>
              I can read {data?.campaigns?.length ?? 0} campaigns,{" "}
              {data?.adsets?.length ?? 0} ad sets and {data?.ads?.length ?? 0} ads on{" "}
              <strong>{data?.account?.name || accountId}</strong>, plus placement, device and
              audience breakdowns and the audit. Ask me what to change.
            </p>
            <div className="chat-suggestions">
              {suggestions.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`bubble-row ${m.role}`}>
            <span className={`chat-avatar ${m.role === "user" ? "is-user" : "is-bot"}`}>
              {m.role === "user" ? <User size={15} /> : <Bot size={15} />}
            </span>
            <div className="bubble">
              {m.role === "assistant" && m.tools?.length > 0 && (
                <div className="tool-trail">
                  {[...new Set(m.tools)].map((t) => (
                    <span key={t} className="tool-chip">
                      <Wrench size={11} />
                      {TOOL_LABEL[t] || t}
                    </span>
                  ))}
                </div>
              )}
              {m.content ? (
                <Markdown text={m.content} />
              ) : (
                m.role === "assistant" &&
                busy && (
                  <span className="chat-status">
                    <Loader size={14} className="spin" />
                    {status || "Working"}…
                  </span>
                )
              )}
            </div>
          </div>
        ))}

        {error && <div className="error-box">{error}</div>}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <textarea
          className="composer-input"
          rows={1}
          placeholder="Ask about campaigns, creatives, placements, budget…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {busy ? (
          <button type="button" className="btn composer-btn" onClick={stop} title="Stop">
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            className="btn btn-primary composer-btn"
            disabled={!input.trim()}
            title="Send"
          >
            <ArrowUp size={16} />
          </button>
        )}
      </form>
    </div>
  );
}

/**
 * Minimal markdown: headings, bold, bullets, numbered lists, inline code.
 *
 * A full markdown library for six constructs would be more bytes than the
 * chart bundle. Text is never inserted as HTML -- every branch renders React
 * nodes, so a model-authored `<img onerror=...>` stays literal text.
 */
function Markdown({ text }) {
  const lines = text.split("\n");
  const blocks = [];
  let list = null;

  const flush = () => {
    if (list) {
      blocks.push(
        list.ordered ? (
          <ol key={blocks.length}>{list.items.map((li, i) => <li key={i}>{inline(li)}</li>)}</ol>
        ) : (
          <ul key={blocks.length}>{list.items.map((li, i) => <li key={i}>{inline(li)}</li>)}</ul>
        )
      );
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^(#{1,4})\s+(.*)$/);

    if (bullet) {
      if (!list || list.ordered) { flush(); list = { ordered: false, items: [] }; }
      list.items.push(bullet[1]);
    } else if (numbered) {
      if (!list || !list.ordered) { flush(); list = { ordered: true, items: [] }; }
      list.items.push(numbered[1]);
    } else if (heading) {
      flush();
      blocks.push(<h4 key={blocks.length}>{inline(heading[2])}</h4>);
    } else if (!line.trim()) {
      flush();
    } else {
      flush();
      blocks.push(<p key={blocks.length}>{inline(line)}</p>);
    }
  }
  flush();
  return <div className="md">{blocks}</div>;
}

function inline(text) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}
