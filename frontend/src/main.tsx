import { StrictMode, useEffect, useReducer, useRef } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Option = { code: string; label: string; level?: string };
type Crop = Option & {
  stages: Array<Option & { day_from: number; day_to: number; reference_event: string }>;
};
type Taxonomy = {
  language: string;
  crops: Crop[];
  seasons: Option[];
  regions: Option[];
  topics: Option[];
};
type Citation = {
  chunk_id: number;
  document_title: string;
  publisher?: string | null;
  page_start: number;
  page_end: number;
  section?: string | null;
  section_path?: string | null;
  publication_date?: string | null;
  version_label?: string | null;
};
type Clarification = { slot: string; options: Array<{ value: string; label: string }> };
type ChatMessage = {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  clarification?: Clarification;
  warnings?: Array<{ type: string; text: string }>;
};
type State = {
  language: string;
  taxonomy: Taxonomy | null;
  error: string | null;
  input: string;
  messages: ChatMessage[];
  sessionId: string | null;
  isLoading: boolean;
};
type Action =
  | { type: "taxonomy"; value: Taxonomy }
  | { type: "error"; value: string }
  | { type: "input"; value: string }
  | { type: "language"; value: string }
  | { type: "user"; value: string }
  | { type: "assistant"; value: ChatMessage; sessionId: string }
  | { type: "loading"; value: boolean }
  | { type: "reset" };

const initialState: State = {
  language: "en",
  taxonomy: null,
  error: null,
  input: "",
  messages: [],
  sessionId: null,
  isLoading: false,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "taxonomy":
      return { ...state, taxonomy: action.value, error: null };
    case "error":
      return { ...state, error: action.value };
    case "input":
      return { ...state, input: action.value };
    case "language":
      return { ...state, language: action.value, taxonomy: null };
    case "user":
      return { ...state, messages: [...state.messages, { role: "user", text: action.value }], input: "" };
    case "assistant":
      return {
        ...state,
        messages: [...state.messages, action.value],
        sessionId: action.sessionId,
        isLoading: false,
      };
    case "loading":
      return { ...state, isLoading: action.value };
    case "reset":
      return { ...initialState, language: state.language };
  }
}

function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const requestVersion = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    fetch(`/api/v1/taxonomy?lang=${state.language}`)
      .then((response) => response.ok ? response.json() as Promise<Taxonomy> : Promise.reject(new Error("Taxonomy unavailable")))
      .then((taxonomy) => dispatch({ type: "taxonomy", value: taxonomy }))
      .catch((reason: Error) => dispatch({ type: "error", value: reason.message }));
  }, [state.language]);

  async function sendMessage(message = state.input.trim()) {
    if (!message || state.isLoading) return;
    const version = ++requestVersion.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    dispatch({ type: "user", value: message });
    dispatch({ type: "loading", value: true });
    try {
      const response = await fetch("/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          session_id: state.sessionId,
          as_of_date: new Date().toISOString().slice(0, 10),
        }),
        signal: controller.signal,
      });
      const payload = await response.json() as {
        session_id: string;
        type: string;
        message?: string;
        answer?: string;
        citations?: Citation[];
        clarification?: Clarification;
        warnings?: Array<{ type: string; text: string }>;
      };
      if (!response.ok) throw new Error(payload.message ?? "Advisory service unavailable");
      if (version !== requestVersion.current) return;
      dispatch({
        type: "assistant",
        sessionId: payload.session_id,
        value: {
          role: "assistant",
          text: payload.message ?? payload.answer ?? "No advisory was returned.",
          citations: payload.citations ?? [],
          clarification: payload.clarification,
          warnings: payload.warnings,
        },
      });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (version !== requestVersion.current) return;
      dispatch({
        type: "assistant",
        sessionId: state.sessionId ?? "",
        value: { role: "assistant", text: reason instanceof Error ? reason.message : "Advisory service unavailable." },
      });
    } finally {
      if (version === requestVersion.current) activeRequest.current = null;
    }
  }

  function chooseClarification(value: string) {
    dispatch({ type: "input", value });
    void sendMessage(value);
  }

  function resetConversation() {
    requestVersion.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    dispatch({ type: "reset" });
  }

  return (
    <main>
      <header>
        <p className="eyebrow">KRISHIMITRA / FOUNDATION</p>
        <h1>Crop context, made clear.</h1>
        <p className="intro">A grounded advisory workspace for Telangana farmers and extension teams.</p>
      </header>
      <section className="toolbar" aria-label="Language selection">
        {[["en", "English"], ["te", "తెలుగు"], ["hi", "हिन्दी"]].map(([code, label]) => (
          <button key={code} className={state.language === code ? "active" : ""} onClick={() => dispatch({ type: "language", value: code })}>
            {label}
          </button>
        ))}
        <button onClick={resetConversation}>New question</button>
      </section>
      <section className="chat-panel">
        <div className="chat-box" aria-live="polite">
          {state.messages.length === 0 && <p className="placeholder">Ask about a crop in ordinary English, or include crop, region, season, stage and topic.</p>}
          {state.messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
              <p>{message.text}</p>
              {message.clarification && index === state.messages.length - 1 && (
                <div className="chips" aria-label={`Choose ${message.clarification.slot}`}>
                  {message.clarification.options.map((option) => (
                    <button key={option.value} onClick={() => chooseClarification(option.value)}>{option.label}</button>
                  ))}
                </div>
              )}
              {message.warnings?.map((warning) => <p className="warning" key={`${warning.type}-${warning.text}`}>{warning.text}</p>)}
              {message.citations && message.citations.length > 0 && <CitationList citations={message.citations} />}
            </div>
          ))}
          {state.isLoading && <div className="message assistant"><p>Checking the advisory evidence...</p></div>}
        </div>
        <div className="composer">
          <textarea value={state.input} onChange={(event) => dispatch({ type: "input", value: event.target.value })} rows={4} placeholder="What should I do for paddy in Warangal?" />
          <button className="send-button" onClick={() => void sendMessage()} disabled={state.isLoading}>Ask advisor</button>
        </div>
      </section>
      {state.error ? <p role="alert" className="error">{state.error}</p> : !state.taxonomy ? <p className="loading">Loading taxonomy...</p> : (
        <section className="grid">
          <TaxonomyGroup title="Crops" items={state.taxonomy.crops} />
          <TaxonomyGroup title="Seasons" items={state.taxonomy.seasons} />
          <TaxonomyGroup title="Regions" items={state.taxonomy.regions} />
          <TaxonomyGroup title="Topics" items={state.taxonomy.topics} />
        </section>
      )}
      <aside className="safety-note">Advisories summarize source documents. For agrochemical decisions, consult your local agriculture officer or KVK.</aside>
      <footer>Session-backed advisory chat · taxonomy language: {state.language}</footer>
    </main>
  );
}

function CitationList({ citations }: { citations: Citation[] }) {
  return (
    <ul className="citations">
      {citations.map((citation) => (
        <li key={`${citation.document_title}-${citation.chunk_id}`}>
          <strong>{citation.document_title}</strong>
          <span>p. {citation.page_start}-{citation.page_end}</span>
          <small>{citation.section ?? citation.section_path ?? "No section"}</small>
          <em>{citation.publication_date ?? "date unavailable"}{citation.version_label ? ` · ${citation.version_label}` : ""}</em>
          <button onClick={() => window.open(`/api/v1/chunks/${citation.chunk_id}`, "_blank", "noopener,noreferrer")}>View source</button>
        </li>
      ))}
    </ul>
  );
}

function TaxonomyGroup({ title, items }: { title: string; items: Option[] }) {
  return <article><h2>{title}</h2><ul>{items.map((item) => <li key={item.code}><span>{item.label}</span><small>{item.code}</small></li>)}</ul></article>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
