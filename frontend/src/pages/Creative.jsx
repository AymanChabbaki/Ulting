import { useCallback, useEffect, useRef, useState } from "react";
import {
  Download,
  Lightbulb,
  Image as ImageIcon,
  Info,
  Loader,
  Sparkles,
  Video as VideoIcon,
  Wand2,
} from "lucide-react";

import { useAudit } from "../App.jsx";
import { windowQuery } from "../lib/api.js";

/**
 * Creative studio: brand-aware images and video from a prompt.
 *
 * Video is a polled job rather than a request — Sora renders take minutes, so
 * the page starts the job and checks back. The poll is cleared on unmount so
 * navigating away does not leave a timer running against a dead component.
 */

const LOGO_POSITIONS = ["bottom-right", "bottom-left", "top-right", "top-left", "bottom-center"];

const EXAMPLES = [
  "Un porte-conteneurs à quai au port de Casablanca au lever du soleil, grues en arrière-plan",
  "Intérieur d'entrepôt ordonné, palettes alignées, lumière naturelle",
  "Camion de fret sur autoroute marocaine, paysage dégagé, heure dorée",
  "Conteneurs empilés vus d'en haut, composition graphique, ciel dégagé",
];

export default function Creative() {
  const { accountId, window: win } = useAudit();
  const [kind, setKind] = useState("image");
  const [ideas, setIdeas] = useState(null);
  const [ideaBrief, setIdeaBrief] = useState("");
  const [ideasBusy, setIdeasBusy] = useState(false);
  const [useTrends, setUseTrends] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [brand, setBrand] = useState(null);

  const [size, setSize] = useState("square");
  const [quality, setQuality] = useState("high");
  const [seconds, setSeconds] = useState("4");
  const [usePersona, setUsePersona] = useState(true);
  const [logo, setLogo] = useState("");
  const [logoPosition, setLogoPosition] = useState("bottom-right");
  const [logoScale, setLogoScale] = useState(0.16);

  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [assets, setAssets] = useState([]);
  const [showPrompt, setShowPrompt] = useState(null);

  const pollRef = useRef(null);

  useEffect(() => {
    fetch("/api/creative/brand", { credentials: "same-origin" })
      .then((r) => r.json())
      .then((b) => {
        setBrand(b);
        if (b.logos?.length) setLogo(b.logos[0]);
      })
      .catch(() => {});
    loadGallery();
    return () => clearTimeout(pollRef.current);
  }, []);

  const loadGallery = useCallback(() => {
    fetch("/api/creative/gallery", { credentials: "same-origin" })
      .then((r) => r.json())
      .then((r) => setAssets(r.assets || []))
      .catch(() => {});
  }, []);

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.detail?.message || `Failed (${response.status})`);
    return payload;
  }

  async function previewPrompt() {
    try {
      const r = await post("/api/creative/preview-prompt", { prompt: prompt || "…", use_persona: usePersona });
      setShowPrompt(r.prompt);
    } catch (cause) {
      setError(cause.message);
    }
  }

  function pollVideo(id) {
    pollRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`/api/creative/video/${id}`, { credentials: "same-origin" });
        const v = await r.json();
        if (v.status === "completed") {
          setStatus(null);
          setBusy(false);
          loadGallery();
          return;
        }
        if (v.status === "failed") {
          setError(v.error || "Render failed");
          setStatus(null);
          setBusy(false);
          return;
        }
        setStatus(`Rendering… ${Math.round(v.progress || 0)}%`);
        pollVideo(id);
      } catch {
        setStatus("Lost contact with the render — it may still be running.");
        setBusy(false);
      }
    }, 5000);
  }

  async function generate() {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "image") {
        setStatus("Generating image…");
        await post("/api/creative/image", {
          prompt,
          size,
          quality,
          logo: logo || "none",
          logo_position: logoPosition,
          logo_scale: logoScale,
          use_persona: usePersona,
        });
        setStatus(null);
        setBusy(false);
        loadGallery();
      } else {
        setStatus("Starting render…");
        const job = await post("/api/creative/video", {
          prompt, size, seconds, use_persona: usePersona,
        });
        setStatus("Rendering… 0%");
        pollVideo(job.id);
      }
    } catch (cause) {
      setError(cause.message);
      setStatus(null);
      setBusy(false);
    }
  }

  async function getIdeas() {
    setIdeasBusy(true);
    setError(null);
    try {
      const payload = await post("/api/creative/ideas", {
        account_id: accountId,
        brief: ideaBrief,
        count: 4,
        use_trends: useTrends,
        ...Object.fromEntries(new URLSearchParams(windowQuery(win))),
      });
      setIdeas(payload);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setIdeasBusy(false);
    }
  }

  /** Drop an idea's prompt straight into the generator above. */
  function useIdea(idea) {
    setPrompt(kind === "image" ? idea.image_prompt : idea.video_prompt);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const sizes = kind === "image" ? ["square", "portrait", "landscape"] : ["portrait", "landscape"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {brand && !brand.personaConfigured && (
        <div className="warn-box">
          <Info size={16} />
          <span>
            No brand persona found at <code>{brand.personaPath}</code>. Generations will have
            no brand direction until you write one.
          </span>
        </div>
      )}

      <section className="card card-pad">
        <div className="card-head">
          <span className="card-title">Creative studio</span>
          <span className="card-sub">
            {brand?.personaConfigured ? "Brand persona applied" : "No persona"}
            {brand?.logos?.length ? ` · ${brand.logos.length} logos` : ""}
          </span>
        </div>

        <div className="studio-kind">
          <button
            className={`chip ${kind === "image" ? "active" : ""}`}
            onClick={() => { setKind("image"); setSize("square"); }}
          >
            <ImageIcon size={13} /> Image
          </button>
          <button
            className={`chip ${kind === "video" ? "active" : ""}`}
            onClick={() => { setKind("video"); setSize("portrait"); }}
          >
            <VideoIcon size={13} /> Video
          </button>
        </div>

        <textarea
          className="input studio-prompt"
          rows={3}
          placeholder={
            kind === "image"
              ? "Describe the image — subject, setting, mood…"
              : "Describe the clip — what happens, where, how it moves…"
          }
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />

        <div className="studio-examples">
          {EXAMPLES.map((e) => (
            <button key={e} className="chip" onClick={() => setPrompt(e)}>
              {e.length > 54 ? `${e.slice(0, 54)}…` : e}
            </button>
          ))}
        </div>

        <div className="studio-controls">
          <label className="ctl">
            <span>Format</span>
            <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
              {sizes.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>

          {kind === "image" ? (
            <label className="ctl">
              <span>Quality</span>
              <select className="select" value={quality} onChange={(e) => setQuality(e.target.value)}>
                {["low", "medium", "high"].map((q) => <option key={q} value={q}>{q}</option>)}
              </select>
            </label>
          ) : (
            <label className="ctl">
              <span>Duration</span>
              <select className="select" value={seconds} onChange={(e) => setSeconds(e.target.value)}>
                {(brand?.videoSeconds || ["4", "8", "12"]).map((s) => (
                  <option key={s} value={s}>{s}s</option>
                ))}
              </select>
            </label>
          )}

          {kind === "image" && (
            <>
              <label className="ctl">
                <span>Logo</span>
                <select className="select" value={logo} onChange={(e) => setLogo(e.target.value)}>
                  <option value="none">None</option>
                  {(brand?.logos || []).map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </label>
              {logo && logo !== "none" && (
                <>
                  <label className="ctl">
                    <span>Position</span>
                    <select className="select" value={logoPosition}
                            onChange={(e) => setLogoPosition(e.target.value)}>
                      {LOGO_POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </label>
                  <label className="ctl">
                    <span>Logo size {Math.round(logoScale * 100)}%</span>
                    <input
                      type="range" min="4" max="40" value={logoScale * 100}
                      onChange={(e) => setLogoScale(Number(e.target.value) / 100)}
                    />
                  </label>
                </>
              )}
            </>
          )}

          <label className="ctl ctl-check">
            <input
              type="checkbox"
              checked={usePersona}
              onChange={(e) => setUsePersona(e.target.checked)}
            />
            <span>Apply brand persona</span>
          </label>
        </div>

        <div className="studio-actions">
          <button className="btn" onClick={previewPrompt} disabled={!prompt.trim()}>
            <Wand2 size={15} /> Preview full prompt
          </button>
          <button className="btn btn-primary" onClick={generate} disabled={!prompt.trim() || busy}>
            {busy ? <Loader size={15} className="spin" /> : <Sparkles size={15} />}
            {busy ? status || "Working…" : `Generate ${kind}`}
          </button>
        </div>

        {kind === "video" && (
          <p className="studio-note">
            Video renders take a few minutes and cost noticeably more than images. The job keeps
            running if you navigate away — it will appear in the gallery when done.
          </p>
        )}

        {error && <div className="error-box" style={{ marginTop: 12 }}>{error}</div>}

        {showPrompt && (
          <div className="prompt-preview">
            <div className="prompt-preview-head">
              <strong>Prompt sent to the model</strong>
              <button className="btn" onClick={() => setShowPrompt(null)}>Close</button>
            </div>
            <pre>{showPrompt}</pre>
          </div>
        )}
      </section>

      <section className="card card-pad">
        <div className="card-head">
          <span className="card-title">
            <Lightbulb size={15} style={{ verticalAlign: "-2px", marginRight: 6 }} />
            Idea generator
          </span>
          <span className="card-sub">
            brand persona + what converts in this account{useTrends ? " + live trend search" : ""}
          </span>
        </div>

        <div className="studio-controls" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
          <input
            className="input"
            style={{ flex: 1, minWidth: 260 }}
            placeholder="Optional steer — e.g. 'angle for tyre importers before Ramadan'"
            value={ideaBrief}
            onChange={(e) => setIdeaBrief(e.target.value)}
          />
          <label className="ctl ctl-check">
            <input type="checkbox" checked={useTrends} onChange={(e) => setUseTrends(e.target.checked)} />
            <span>Search the web for trends</span>
          </label>
          <button className="btn btn-primary" onClick={getIdeas} disabled={ideasBusy}>
            {ideasBusy ? <Loader size={15} className="spin" /> : <Lightbulb size={15} />}
            {ideasBusy ? "Researching…" : "Generate ideas"}
          </button>
        </div>

        {ideas && (
          <>
            <div className="idea-provenance">
              {ideas.webSearchUsed ? "Web search used" : "No web search"} ·{" "}
              {ideas.accountDataUsed ? "account data used" : "no account data"} ·{" "}
              {ideas.personaApplied ? "persona applied" : "no persona"}
              {ideas.trends_used?.length > 0 && (
                <ul className="idea-trends">
                  {ideas.trends_used.map((tr, i) => <li key={i}>{tr}</li>)}
                </ul>
              )}
            </div>

            <div className="idea-grid">
              {(ideas.ideas || []).map((idea, i) => (
                <article key={i} className="idea">
                  <h4>{idea.title}</h4>
                  <p className="idea-angle">{idea.angle}</p>
                  {idea.french_copy && <p className="idea-copy">« {idea.french_copy} »</p>}
                  <p className="idea-why"><strong>Based on:</strong> {idea.why}</p>
                  <div className="idea-actions">
                    <button className="btn" onClick={() => useIdea(idea)}>
                      Use {kind} prompt
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      <section>
        <div className="list-toolbar" style={{ marginBottom: 12 }}>
          <h2 className="section-title" style={{ margin: 0 }}>Generated</h2>
          <button className="btn" onClick={loadGallery} style={{ marginLeft: "auto" }}>Refresh</button>
        </div>

        {assets.length === 0 ? (
          <div className="card empty">Nothing generated yet.</div>
        ) : (
          <div className="asset-grid">
            {assets.map((a) => (
              <figure key={a.file} className="asset">
                {a.kind === "video" ? (
                  <video src={a.url} controls preload="metadata" />
                ) : (
                  <img src={a.url} alt={a.file} loading="lazy" />
                )}
                <figcaption>
                  <span className="asset-meta">
                    {a.kind} · {(a.bytes / 1024 / 1024).toFixed(1)} MB
                  </span>
                  <a className="btn" href={a.url} download={a.file} title="Download">
                    <Download size={14} />
                  </a>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
