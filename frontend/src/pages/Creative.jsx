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

const LOGO_POSITIONS = ["auto", "bottom-right", "bottom-left", "top-right", "top-left", "bottom-center"];

const LOGO_MODES = [
  { value: "model", label: "AI places the logo", hint: "Logo sent to the model, integrated into the scene" },
  { value: "overlay", label: "Exact overlay", hint: "Pasted on afterwards, pixel-perfect" },
  { value: "none", label: "No logo", hint: "" },
];

const DESIGNS = [
  { value: "full_ad", label: "Full ad (text + layout)" },
  { value: "visual", label: "Visual only (no text)" },
];

const EXAMPLES = [
  'Un sablier géant rempli de conteneurs, un seul conteneur bleu ULTEx passe. Titre : "Le temps perdu. / Se paie cher." Sous-titre : "On dédouane pendant que vous vendez." CTA : "Parlez à un expert"',
  'Un puzzle de 6 pièces étiquetées Fournisseur, Transport, Douane, Stockage, Suivi, Livraison, la dernière pièce bleue ULTEx s\'emboîte. Titre : "Six métiers. / Un seul partenaire."',
  'Deux balances : l\'une déborde de factures et frais cachés, l\'autre équilibrée avec un seul devis ULTEx. Titre : "Coût estimé. / Coût réel."',
  'Une boussole posée sur une carte Chine–Maroc, l\'aiguille en forme de flèche ULTEx. Titre : "Importer sans repères ? / Suivez la bonne direction."',
];

/** On-image text of an idea, appended to its prompt so it is rendered verbatim. */
function ideaImagePrompt(idea) {
  const lines = [idea.image_prompt];
  const quoted = [
    idea.image_headline && `Headline: "${idea.image_headline}"`,
    idea.image_subline && `Subline: "${idea.image_subline}"`,
    idea.image_cta && `CTA: "${idea.image_cta}"`,
    idea.image_benefits?.length && `Benefit band: ${idea.image_benefits.map((b) => `"${b}"`).join(", ")}`,
  ].filter(Boolean);
  // The model is asked to quote the text inside image_prompt too; only add it
  // when it forgot, so the text is not given twice in two versions.
  if (quoted.length && !(idea.image_headline && idea.image_prompt.includes(idea.image_headline))) {
    lines.push(`Exact on-image text:\n${quoted.join("\n")}`);
  }
  return lines.join("\n\n");
}

export default function Creative() {
  const { accountId, window: win } = useAudit();
  const [kind, setKind] = useState("image");
  const [ideas, setIdeas] = useState(null);
  const [ideaBrief, setIdeaBrief] = useState("");
  const [ideasBusy, setIdeasBusy] = useState(false);
  const [useTrends, setUseTrends] = useState(true);
  const [ideaAudience, setIdeaAudience] = useState("");
  const [ideaService, setIdeaService] = useState("");
  const [ideaLanguage, setIdeaLanguage] = useState("fr");
  const [ideaCount, setIdeaCount] = useState(4);
  const [copied, setCopied] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [brand, setBrand] = useState(null);

  const [size, setSize] = useState("square");
  const [model, setModel] = useState("");
  const [quality, setQuality] = useState("");
  const [logoMode, setLogoMode] = useState("model");
  const [design, setDesign] = useState("full_ad");
  const [useReferences, setUseReferences] = useState(true);
  const [seconds, setSeconds] = useState("4");
  const [usePersona, setUsePersona] = useState(true);
  const [logo, setLogo] = useState("");
  const [logoPosition, setLogoPosition] = useState("auto");
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
        // The company's own logo (brand/) is the default, not the dashboard's.
        setLogo(b.defaultLogo || b.logos?.[0] || "none");
        setModel(b.defaultImageModel || "");
        const q = b.qualityByModel?.[b.defaultImageModel];
        setQuality(q ? q[q.length - 1] : "high");
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
      const r = await post("/api/creative/preview-prompt", {
        prompt: prompt || "…",
        use_persona: usePersona,
        logo: logo || "none",
        logo_mode: logoMode,
        logo_position: logoPosition,
        design,
        use_references: useReferences,
      });
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
          model,
          logo: logo || "none",
          logo_mode: logo && logo !== "none" ? logoMode : "none",
          logo_position: logoPosition,
          logo_scale: logoScale,
          use_persona: usePersona,
          design,
          use_references: useReferences,
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
        count: ideaCount,
        use_trends: useTrends,
        audience: ideaAudience,
        service: ideaService,
        language: ideaLanguage,
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
  function useIdea(idea, which) {
    const asVideo = which === "video";
    setKind(asVideo ? "video" : "image");
    setSize(asVideo ? "portrait" : idea.placement === "story_reel_vertical" ? "portrait" : "square");
    if (!asVideo) setDesign("full_ad");
    setPrompt(asVideo ? idea.video_prompt : ideaImagePrompt(idea));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function copyAd(idea, i) {
    const text = `${idea.primary_text}\n\n${idea.headline}\n[${idea.cta}]`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(i);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* clipboard blocked: the text is on screen to select */
    }
  }

  const sizes = kind === "image" ? ["square", "portrait", "landscape"] : ["portrait", "landscape"];
  const qualities = brand?.qualityByModel?.[model] || ["low", "medium", "high"];

  function changeModel(next) {
    setModel(next);
    const q = brand?.qualityByModel?.[next] || ["low", "medium", "high"];
    // Keep the choice if the new model supports it, otherwise take its best.
    if (!q.includes(quality)) setQuality(q[q.length - 1]);
  }

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
              ? design === "full_ad"
                ? 'Describe the visual metaphor, then the exact text — Titre : "… / …"  Sous-titre : "…"  CTA : "…" (leave the text out and it writes it)'
                : "Describe the image — subject, setting, mood…"
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
            <>
              <label className="ctl">
                <span>Model</span>
                <select className="select" value={model} onChange={(e) => changeModel(e.target.value)}>
                  {(brand?.imageModels || []).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="ctl">
                <span>Quality</span>
                <select className="select" value={quality} onChange={(e) => setQuality(e.target.value)}>
                  {qualities.map((q) => <option key={q} value={q}>{q}</option>)}
                </select>
              </label>
            </>
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
                <span>Design</span>
                <select className="select" value={design} onChange={(e) => setDesign(e.target.value)}>
                  {DESIGNS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </label>
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
                    <span>Logo handling</span>
                    <select className="select" value={logoMode} onChange={(e) => setLogoMode(e.target.value)}>
                      {LOGO_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                  </label>
                  {logoMode !== "none" && (
                    <label className="ctl">
                      <span>Position</span>
                      <select className="select" value={logoPosition}
                              onChange={(e) => setLogoPosition(e.target.value)}>
                        {LOGO_POSITIONS.filter((p) => logoMode === "model" || p !== "auto")
                          .map((p) => <option key={p} value={p}>{p}</option>)}
                      </select>
                    </label>
                  )}
                  {logoMode === "overlay" && (
                    <label className="ctl">
                      <span>Logo size {Math.round(logoScale * 100)}%</span>
                      <input
                        type="range" min="4" max="40" value={logoScale * 100}
                        onChange={(e) => setLogoScale(Number(e.target.value) / 100)}
                      />
                    </label>
                  )}
                  {logoMode !== "none" && (
                    <div className="ctl logo-preview">
                      <span>Logo sent (background removed)</span>
                      <img
                        src={`/api/creative/logo-preview/${encodeURIComponent(logo)}`}
                        alt="Logo with background removed"
                      />
                    </div>
                  )}
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

          {kind === "image" && brand?.references?.length > 0 && (
            <label className="ctl ctl-check">
              <input
                type="checkbox"
                checked={useReferences}
                onChange={(e) => setUseReferences(e.target.checked)}
              />
              <span>Match house style ({brand.references.length} reference ads)</span>
            </label>
          )}
        </div>

        {kind === "image" && useReferences && brand?.references?.length > 0 && (
          <div className="reference-strip">
            <span>House style sent with every image — layout and typography are copied, concepts are not</span>
            <div>
              {brand.references.map((r) => (
                <img key={r} src={`/api/creative/reference/${encodeURIComponent(r)}`} alt={r} title={r} />
              ))}
            </div>
          </div>
        )}

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
            your real ads + persona + importer calendar{useTrends ? " + live search" : ""}
          </span>
        </div>

        <div className="studio-controls" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
          <label className="ctl">
            <span>Audience</span>
            <select className="select" value={ideaAudience} onChange={(e) => setIdeaAudience(e.target.value)}>
              <option value="">All importers</option>
              <option value="SME importers buying from China and Turkey">SME importers (China / Turkey)</option>
              <option value="E-commerce sellers stocking imported products">E-commerce sellers</option>
              <option value="Industrial buyers: machinery, equipment, spare parts">Industrial buyers</option>
              <option value="Retailers and wholesalers of consumer goods and appliances">Retail & wholesale</option>
              <option value="First-time importers who have never imported before">First-time importers</option>
            </select>
          </label>
          <label className="ctl">
            <span>Service</span>
            <select className="select" value={ideaService} onChange={(e) => setIdeaService(e.target.value)}>
              <option value="">Whole offer (one partner)</option>
              <option value="Sourcing and supplier verification abroad">Sourcing & supplier checks</option>
              <option value="Customs clearance in Morocco">Customs clearance</option>
              <option value="International freight: sea, air and road">Freight (sea / air / road)</option>
              <option value="Groupage and consolidation for small volumes">Groupage / small volumes</option>
              <option value="Warehousing and last-mile delivery in Morocco">Warehousing & delivery</option>
              <option value="Real-time cargo tracking">Cargo tracking</option>
            </select>
          </label>
          <label className="ctl">
            <span>Copy language</span>
            <select className="select" value={ideaLanguage} onChange={(e) => setIdeaLanguage(e.target.value)}>
              <option value="fr">Français</option>
              <option value="darija">Darija</option>
              <option value="fr+darija">Français + Darija hook</option>
              <option value="ar">العربية</option>
            </select>
          </label>
          <label className="ctl">
            <span>Ideas</span>
            <select className="select" value={ideaCount} onChange={(e) => setIdeaCount(Number(e.target.value))}>
              {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="ctl ctl-check">
            <input type="checkbox" checked={useTrends} onChange={(e) => setUseTrends(e.target.checked)} />
            <span>Live web search</span>
          </label>
        </div>

        <div className="studio-controls" style={{ marginTop: 10, paddingTop: 0, borderTop: "none" }}>
          <input
            className="input"
            style={{ flex: 1, minWidth: 260 }}
            placeholder="Optional brief — e.g. 'order before Chinese New Year', 'tyre importers in Agadir', 'push WhatsApp leads'"
            value={ideaBrief}
            onChange={(e) => setIdeaBrief(e.target.value)}
          />
          <button className="btn btn-primary" onClick={getIdeas} disabled={ideasBusy}>
            {ideasBusy ? <Loader size={15} className="spin" /> : <Lightbulb size={15} />}
            {ideasBusy ? "Studying your ads…" : "Generate ideas"}
          </button>
        </div>
        {ideasBusy && (
          <p className="studio-note">
            Reading your best and worst ads, the importer calendar{useTrends ? " and live news" : ""},
            then brainstorming and discarding the generic. Usually 1–2 minutes.
          </p>
        )}

        {ideas && (
          <>
            <div className="idea-provenance">
              <p className="idea-strategy">{ideas.strategy_summary}</p>
              <div className="idea-meta">
                <span>{ideas.model}</span>
                <span>{ideas.webSearchUsed ? "web search used" : "no web search"}</span>
                <span>{ideas.imagesSeen} winning creative{ideas.imagesSeen === 1 ? "" : "s"} viewed</span>
                <span>{ideas.personaApplied ? "persona applied" : "no persona"}</span>
              </div>
              {ideas.learnedFrom?.length > 0 && (
                <div className="idea-learned">
                  <strong>Learned from:</strong>{" "}
                  {ideas.learnedFrom.map((w, i) => (
                    <span key={i} className="tag" style={{ textTransform: "none" }}>
                      “{w.headline || "untitled"}” · {Math.round(w.results)} results
                      {w.costPerResult != null ? ` · ${w.costPerResult}/result` : ""} · {w.reliability}
                    </span>
                  ))}
                </div>
              )}
              {ideas.evidence_notes?.length > 0 && (
                <ul className="idea-notes">
                  {ideas.evidence_notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
              {ideas.trends_used?.length > 0 && (
                <details className="idea-trends-box">
                  <summary>Calendar & trends used ({ideas.trends_used.length})</summary>
                  <ul className="idea-trends">
                    {ideas.trends_used.map((tr, i) => <li key={i}>{tr}</li>)}
                  </ul>
                </details>
              )}
            </div>

            <div className="idea-grid">
              {(ideas.ideas || []).map((idea, i) => (
                <article key={i} className="idea">
                  <div className="idea-top">
                    <span className="tag">{idea.framework.replace(/_/g, " ")}</span>
                    <span className="idea-placement">{idea.placement.replace(/_/g, " ")}</span>
                  </div>
                  <h4>{idea.title}</h4>
                  <p className="idea-insight"><strong>Insight:</strong> {idea.insight}</p>
                  <p className="idea-audience">{idea.audience}</p>

                  <div className="idea-ad">
                    <p className="idea-hook">{idea.hook}</p>
                    <p className="idea-body">{idea.primary_text}</p>
                    <div className="idea-ad-foot">
                      <span className="idea-headline">{idea.headline}</span>
                      <span className="idea-cta">{idea.cta.replace(/_/g, " ")}</span>
                    </div>
                  </div>

                  {idea.image_headline && (
                    <div className="idea-onimage">
                      <span className="idea-onimage-label">On the image</span>
                      <p className="idea-onimage-head">
                        {idea.image_headline.split(" / ").map((beat, b) => (
                          <span key={b} className={b === 1 ? "punch" : undefined}>{beat}</span>
                        ))}
                      </p>
                      {idea.image_subline && <p className="idea-onimage-sub">{idea.image_subline}</p>}
                      {idea.image_cta && <span className="idea-onimage-cta">{idea.image_cta} →</span>}
                      {idea.image_benefits?.length > 0 && (
                        <div className="idea-onimage-band">
                          {idea.image_benefits.map((b) => <span key={b}>{b}</span>)}
                        </div>
                      )}
                    </div>
                  )}

                  <details className="idea-prompts">
                    <summary>Image & video prompts</summary>
                    <p><strong>Image:</strong> {idea.image_prompt}</p>
                    <p><strong>Video:</strong> {idea.video_prompt}</p>
                  </details>

                  <p className="idea-why"><strong>Based on:</strong> {idea.why}</p>
                  <p className="idea-why"><strong>Tests:</strong> {idea.test_hypothesis}</p>

                  <div className="idea-actions">
                    <button className="btn btn-primary" onClick={() => useIdea(idea, "image")}>
                      Make image
                    </button>
                    <button className="btn" onClick={() => useIdea(idea, "video")}>Make video</button>
                    <button className="btn" onClick={() => copyAd(idea, i)}>
                      {copied === i ? "Copied" : "Copy ad text"}
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
