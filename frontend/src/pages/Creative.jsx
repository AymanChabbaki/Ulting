import { useCallback, useEffect, useRef, useState } from "react";
import {
  Clapperboard,
  Download,
  FastForward,
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
 * Video (Google Veo) is a polled job rather than a request — renders take a
 * minute or more, so the page starts the job and checks back. A video can
 * start from a generated ad, so it opens on the real logo and layout. The poll is cleared on unmount so
 * navigating away does not leave a timer running against a dead component.
 */

const LOGO_POSITIONS = ["auto", "bottom-right", "bottom-left", "top-right", "top-left", "bottom-center"];

const usd = (n) => `$${n.toFixed(2)}`;

/**
 * "Veo 3.1 fast — $0.10–0.30/s · 4–8s · up to 4k · extendable", from the
 * prices and limits the API sends (veo.py VIDEO_MODEL_INFO).
 */
function videoModelLabel(m, info, resolutionsByModel) {
  const i = info?.[m];
  if (!i) return m;
  const prices = Object.values(i.pricePerSecond);
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  const approx = i.priceNote ? "≈" : "";
  const price = lo === hi ? `${approx}$${lo.toFixed(2)}/s` : `$${lo.toFixed(2)}–${hi.toFixed(2)}/s`;
  const secs = i.seconds ? `${Math.min(...i.seconds)}–${Math.max(...i.seconds)}s per render` : i.lengthNote;
  const res = (resolutionsByModel?.[m] || Object.keys(i.pricePerSecond)).slice(-1)[0];
  const extend = i.canExtend ? ` · extend +${i.extendSeconds}s up to ${i.maxTotalSeconds}s` : " · no extend";
  return `${i.label} — ${price} · ${secs} · up to ${res}${extend}`;
}

/**
 * Estimated price of seconds of video at a resolution, or null if unknown.
 * Omni is token-billed and Google only publishes its 720p rate, so other
 * Omni resolutions are estimated at that rate.
 */
function videoCost(m, resolution, seconds, info) {
  const i = info?.[m];
  const per = i?.pricePerSecond?.[resolution] ?? (i?.priceNote ? i.pricePerSecond["720p"] : null);
  return per == null ? null : per * seconds;
}

const isOmni = (m, info) => info?.[m]?.provider === "omni";
const extendStep = (m, info) => info?.[m]?.extendSeconds || 7;
const maxTotal = (m, info) => info?.[m]?.maxTotalSeconds || 148;
// Veo renders exactly what is asked; Omni picks ~5-10s, so 8s is the estimate.
const FIRST_SCENE_SECONDS = 8;
// Lite cannot extend; a multi-scene Veo video runs on fast instead.
const chainModelFor = (m) => (m.includes("lite") ? "veo-3.1-fast-generate-preview" : m);

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
  const [ideaScenes, setIdeaScenes] = useState(3);
  // Scenes 2..N of a video: each is a Veo extension (+7s) of the one before.
  const [scenePlan, setScenePlan] = useState([]);
  const [chainRunning, setChainRunning] = useState(false);
  const [copied, setCopied] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [brand, setBrand] = useState(null);

  const [size, setSize] = useState("square");
  const [model, setModel] = useState("");
  const [quality, setQuality] = useState("");
  const [logoMode, setLogoMode] = useState("model");
  const [design, setDesign] = useState("full_ad");
  const [useReferences, setUseReferences] = useState(true);
  const [seconds, setSeconds] = useState("8");
  const [videoModel, setVideoModel] = useState("");
  const [resolution, setResolution] = useState("720p");
  const [startImage, setStartImage] = useState("");
  // Extension panel: which gallery video, what happens next, how many +7s steps.
  const [extendFor, setExtendFor] = useState(null);
  const [extendPrompt, setExtendPrompt] = useState("");
  const [extendSteps, setExtendSteps] = useState(1);
  const [extendModel, setExtendModel] = useState("veo-3.1-fast-generate-preview");
  const [extendStatus, setExtendStatus] = useState(null);
  const [extendBusy, setExtendBusy] = useState(false);
  const [extendError, setExtendError] = useState(null);
  const extendCancel = useRef(false);
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
        setVideoModel(b.defaultVideoModel || "");
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

  /** Poll one Veo job until it finishes; resolves with its final payload. */
  async function waitForVideo(id, onProgress) {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 6000));
      if (extendCancel.current) throw new Error("Stopped. The render already started still finishes on Google's side.");
      const r = await fetch(`/api/creative/video/${id}`, { credentials: "same-origin" });
      const v = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(v?.detail?.message || `Polling failed (${r.status})`);
      if (v.status === "completed") return v;
      if (v.status === "failed") throw new Error(v.error || "Render failed");
      onProgress?.(v);
    }
  }

  /**
   * Run N extensions back to back: each step continues the file the previous
   * step produced, so the result is one continuous shot. Every step is its own
   * file in the gallery, so a bad step can be retried from the one before.
   */
  async function runExtensions() {
    if (!extendFor || !extendPrompt.trim() || extendBusy) return;
    setExtendBusy(true);
    setExtendError(null);
    extendCancel.current = false;
    let source = extendFor.file;
    let length = extendFor.veo?.seconds || null;
    const stepSecs = extendStep(extendModel, brand?.videoModelInfo);
    try {
      for (let step = 1; step <= extendSteps; step += 1) {
        const target = length ? ` → ~${Math.round(length + stepSecs)}s` : "";
        setExtendStatus(`Step ${step}/${extendSteps}: starting${target}…`);
        const job = await post("/api/creative/video/extend", {
          source,
          prompt: extendPrompt,
          model: extendModel,
        });
        const started = Date.now();
        const done = await waitForVideo(job.id, () => {
          const secs = Math.round((Date.now() - started) / 1000);
          setExtendStatus(`Step ${step}/${extendSteps}: rendering${target} · ${secs}s`);
        });
        source = done.file;
        length = done.seconds || (length ? length + stepSecs : null);
        loadGallery();
      }
      setExtendStatus(`Done${length ? ` — ${length}s video` : ""}. It is at the top of the gallery.`);
      setExtendFor(null);
    } catch (cause) {
      setExtendError(cause.message);
      setExtendStatus(null);
    } finally {
      setExtendBusy(false);
      loadGallery();
    }
  }

  function openExtend(asset) {
    setExtendFor(asset);
    setExtendError(null);
    setExtendStatus(null);
    const m = asset.veo?.model;
    // Omni continues its own interaction, so the model is fixed to the source's.
    const next = asset.veo?.provider === "omni"
      ? m
      : brand?.extendModels?.includes(m) ? m : "veo-3.1-fast-generate-preview";
    setExtendModel(next);
    const info = brand?.videoModelInfo;
    const room = Math.floor((maxTotal(next, info) - (asset.veo?.seconds || 8)) / extendStep(next, info));
    setExtendSteps((s) => Math.max(1, Math.min(s, room)));
  }

  /**
   * Render a multi-scene video: scene 1 from the main prompt (8s, 720p so it
   * can be extended), then each later scene as an extension of the previous
   * result. One continuous shot, ~8 + 7 x (N-1) seconds.
   */
  async function runScenePlan() {
    const later = scenePlan.map((s) => s.trim()).filter(Boolean);
    const total = later.length + 1;
    const info = brand?.videoModelInfo;
    const chainModel = chainModelFor(videoModel);
    const step = extendStep(chainModel, info);
    const omni = isOmni(chainModel, info);
    if (FIRST_SCENE_SECONDS + step * later.length > maxTotal(chainModel, info)) {
      throw new Error(
        `${info?.[chainModel]?.label || chainModel} videos stop at ${maxTotal(chainModel, info)}s — ` +
        `use at most ${Math.floor((maxTotal(chainModel, info) - FIRST_SCENE_SECONDS) / step) + 1} scenes.`
      );
    }
    extendCancel.current = false;
    setChainRunning(true);
    setExtendStatus(null);
    const tick = (label) => {
      const started = Date.now();
      return () => setStatus(`${label} · ${Math.round((Date.now() - started) / 1000)}s`);
    };
    try {
      setStatus(`Scene 1/${total}: starting…`);
      const first = await post("/api/creative/video", {
        // Veo extends 720p only; Omni keeps the chosen resolution.
        prompt, size, seconds: "8", resolution: omni ? resolution : "720p", model: chainModel,
        start_image: startImage || null, use_persona: usePersona,
      });
      let done = await waitForVideo(first.id, tick(`Scene 1/${total}: rendering (${omni ? "~" : ""}8s)`));
      loadGallery();
      let source = done.file;
      let length = done.seconds || FIRST_SCENE_SECONDS;
      for (let i = 0; i < later.length; i += 1) {
        const label = `Scene ${i + 2}/${total}: rendering (→ ~${Math.round(length + step)}s)`;
        setStatus(`Scene ${i + 2}/${total}: starting…`);
        const job = await post("/api/creative/video/extend", { source, prompt: later[i], model: chainModel });
        done = await waitForVideo(job.id, tick(label));
        source = done.file;
        length = done.seconds || length + step;
        loadGallery();
      }
      setExtendStatus(`Done — ${total} scenes, ${length}s video. It is at the top of the gallery; the shorter steps are kept too.`);
    } finally {
      setChainRunning(false);
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
      } else if (scenePlan.some((s) => s.trim())) {
        await runScenePlan();
        setStatus(null);
        setBusy(false);
      } else {
        setStatus("Starting render…");
        const job = await post("/api/creative/video", {
          prompt,
          size,
          seconds,
          resolution,
          model: videoModel,
          start_image: startImage || null,
          use_persona: usePersona,
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
        video_scenes: ideaScenes,
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
    if (!asVideo) {
      setDesign("full_ad");
      setPrompt(ideaImagePrompt(idea));
    } else {
      const scenes = idea.video_scenes || [];
      setPrompt(scenes[0]?.prompt || idea.video_prompt || "");
      setScenePlan(scenes.slice(1).map((s) => s.prompt));
      setStartImage("");
      setSeconds("8");
      if (scenes.length > 1) {
        setResolution("720p");
        if (videoModel.includes("lite")) setVideoModel("veo-3.1-fast-generate-preview");
      }
    }
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

  const vInfo = brand?.videoModelInfo;
  const sceneModel = chainModelFor(videoModel || "");
  const sceneStep = extendStep(sceneModel, vInfo);
  const sceneMax = Math.floor((maxTotal(sceneModel, vInfo) - FIRST_SCENE_SECONDS) / sceneStep);
  const omniSelected = isOmni(videoModel, vInfo);

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

        {kind === "video" && (
          <div className="scene-plan">
            {scenePlan.length > 0 && (
              <p className="scene-plan-note">
                The prompt above is <strong>scene 1</strong> ({omniSelected ? "~" : ""}8s). Each scene
                below continues the previous one from its last frame (+{sceneStep}s). Total{" "}
                <strong>~{FIRST_SCENE_SECONDS + sceneStep * scenePlan.length}s</strong>
                {omniSelected ? "" : ", rendered at 720p"}, one scene after another
                — {vInfo?.[sceneModel]?.label || sceneModel} allows up to {sceneMax + 1} scenes.
              </p>
            )}
            {scenePlan.map((s, i) => (
              <div key={i} className="scene-row">
                <span className="scene-label">Scene {i + 2}<small>+{sceneStep}s</small></span>
                <textarea
                  className="input"
                  rows={3}
                  value={s}
                  placeholder="What happens next, from the last frame of the previous scene…"
                  onChange={(e) => setScenePlan(scenePlan.map((x, j) => (j === i ? e.target.value : x)))}
                />
                <button className="btn" title="Remove scene" onClick={() => setScenePlan(scenePlan.filter((_, j) => j !== i))}>
                  ×
                </button>
              </div>
            ))}
            <button
              className="btn scene-add"
              onClick={() => setScenePlan([...scenePlan, ""])}
              disabled={scenePlan.length >= sceneMax || busy}
            >
              + Add scene (+{sceneStep}s)
            </button>
          </div>
        )}

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
            <>
              <label className="ctl">
                <span>Model</span>
                <select
                  className="select"
                  value={videoModel}
                  onChange={(e) => {
                    setVideoModel(e.target.value);
                    const allowed = brand?.videoResolutionsByModel?.[e.target.value];
                    if (allowed && !allowed.includes(resolution)) setResolution("720p");
                  }}
                >
                  {(brand?.videoModels || []).map((m) => (
                    <option key={m} value={m}>{videoModelLabel(m, vInfo, brand?.videoResolutionsByModel)}</option>
                  ))}
                </select>
              </label>
              <label className="ctl">
                <span>Resolution</span>
                <select
                  className="select"
                  value={resolution}
                  onChange={(e) => {
                    setResolution(e.target.value);
                    // Veo renders 1080p and 4k only at 8 seconds.
                    if (!omniSelected && e.target.value !== "720p") setSeconds("8");
                  }}
                >
                  {(brand?.videoResolutionsByModel?.[videoModel] || brand?.videoResolutions || ["720p", "1080p"])
                    .map((r) => {
                      const per = vInfo?.[videoModel]?.pricePerSecond?.[r];
                      const note = omniSelected
                        ? (r === "1080p" || r === "4k" ? " · upscaled" : "")
                        : r === "720p" ? " · extendable" : " · 8s only";
                      return (
                        <option key={r} value={r}>
                          {r}{per != null ? ` · ${usd(per)}/s` : ""}{note}
                        </option>
                      );
                    })}
                </select>
              </label>
              {omniSelected ? (
                <div className="ctl">
                  <span>Duration</span>
                  <span className="ctl-static">{vInfo?.[videoModel]?.lengthNote || "~5–10s"}</span>
                </div>
              ) : (
                <label className="ctl">
                  <span>Duration</span>
                  <select className="select" value={seconds} onChange={(e) => setSeconds(e.target.value)}>
                    {(brand?.videoSeconds || ["4", "6", "8"]).map((s) => (
                      <option key={s} value={s} disabled={resolution !== "720p" && s !== "8"}>{s}s</option>
                    ))}
                  </select>
                </label>
              )}
              <label className="ctl">
                <span>Start from</span>
                <select className="select" value={startImage} onChange={(e) => setStartImage(e.target.value)}>
                  <option value="">Prompt only</option>
                  {assets.filter((a) => a.kind === "image").map((a) => (
                    <option key={a.file} value={a.file}>{a.file.replace(/^\d{8}-\d{6}-/, "").slice(0, 48)}</option>
                  ))}
                </select>
              </label>
              {startImage && (
                <div className="ctl logo-preview start-frame">
                  <span>First frame</span>
                  <img src={`/api/creative/asset/${encodeURIComponent(startImage)}`} alt="First frame" />
                </div>
              )}
            </>
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
          {kind === "video" && (() => {
            const scenes = scenePlan.filter((s) => s.trim()).length;
            const chainModel = scenes ? sceneModel : videoModel;
            const res = scenes && !omniSelected ? "720p" : resolution;
            const secs = scenes
              ? FIRST_SCENE_SECONDS + sceneStep * scenes
              : omniSelected ? FIRST_SCENE_SECONDS : Number(seconds);
            const cost = videoCost(chainModel, res, secs, vInfo);
            if (cost == null) return null;
            return (
              <span className="cost-estimate" title={`Prices: ${brand?.pricingSource || "Google pricing page"}`}>
                ≈ {usd(cost)} · {omniSelected ? "~" : ""}{secs}s at {res} · {vInfo[chainModel].label}
              </span>
            );
          })()}
          {chainRunning && (
            <button className="btn" onClick={() => { extendCancel.current = true; }}>
              Stop after this scene
            </button>
          )}
          <button className="btn btn-primary" onClick={generate} disabled={!prompt.trim() || busy}>
            {busy ? <Loader size={15} className="spin" /> : <Sparkles size={15} />}
            {busy ? status || "Working…" : `Generate ${kind}`}
          </button>
        </div>

        {kind === "video" && brand && !brand.videoProvider && (
          <div className="warn-box" style={{ marginTop: 12 }}>
            <Info size={16} />
            <span>Video needs <code>GEMINI_API_KEY</code> in <code>.env.local</code>.</span>
          </div>
        )}
        {kind === "video" && (
          <p className="studio-note">
            Google Veo 3.1, with sound. Renders take 1–3 minutes and are billed per second of
            video — Veo 3.1 costs several times more than fast or lite, so draft with lite. Start
            from one of your generated ads to keep the real logo and text. The job keeps running if
            you navigate away — it appears in the gallery when done.
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
          <label className="ctl">
            <span>Video scenes</span>
            <select className="select" value={ideaScenes} onChange={(e) => setIdeaScenes(Number(e.target.value))}>
              {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                <option key={n} value={n}>
                  {n} scene{n > 1 ? "s" : ""} · ~{FIRST_SCENE_SECONDS + sceneStep * (n - 1)}s
                  {videoCost(sceneModel, "720p", FIRST_SCENE_SECONDS + sceneStep * (n - 1), vInfo) != null
                    ? ` · ≈ ${usd(videoCost(sceneModel, "720p", FIRST_SCENE_SECONDS + sceneStep * (n - 1), vInfo))}`
                    : ""}
                  {n - 1 > sceneMax ? ` · too long for ${vInfo?.[sceneModel]?.label}` : ""}
                </option>
              ))}
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
                    <summary>Image prompt & video scenes{idea.video_scenes?.length ? ` (${idea.video_scenes.length})` : ""}</summary>
                    <p><strong>Image:</strong> {idea.image_prompt}</p>
                    {idea.video_continuity && (
                      <p><strong>Video continuity:</strong> {idea.video_continuity}</p>
                    )}
                    {(idea.video_scenes || []).map((sc, k) => (
                      <p key={k}>
                        <strong>Scene {k + 1} ({k === 0 ? "8s" : "+7s"}) — {sc.beat}:</strong> {sc.prompt}
                      </p>
                    ))}
                    {idea.video_prompt && <p><strong>Video:</strong> {idea.video_prompt}</p>}
                  </details>

                  <p className="idea-why"><strong>Based on:</strong> {idea.why}</p>
                  <p className="idea-why"><strong>Tests:</strong> {idea.test_hypothesis}</p>

                  <div className="idea-actions">
                    <button className="btn btn-primary" onClick={() => useIdea(idea, "image")}>
                      Make image
                    </button>
                    <button className="btn" onClick={() => useIdea(idea, "video")}>
                      {idea.video_scenes?.length > 1
                        ? `Make video (${idea.video_scenes.length} scenes · ${8 + 7 * (idea.video_scenes.length - 1)}s)`
                        : "Make video"}
                    </button>
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

        {extendStatus && !extendFor && <div className="studio-note" style={{ marginBottom: 12 }}>{extendStatus}</div>}

        {extendFor && (
          <div className="card card-pad extend-panel">
            <div className="extend-head">
              <video src={extendFor.url} muted preload="metadata" />
              <div>
                <strong>Extend this video</strong>
                <p>
                  {vInfo?.[extendModel]?.label || "Veo"} continues it from the last frame, as one
                  continuous shot — +{extendStep(extendModel, vInfo)}s per step.
                  {extendFor.veo?.seconds ? ` Now ${extendFor.veo.seconds}s.` : ""}
                  {extendFor.veo?.provider === "omni" ? "" : " 720p only,"} up to{" "}
                  {maxTotal(extendModel, vInfo)}s in total. Each step is saved as a new video; the
                  original is kept.
                </p>
              </div>
            </div>
            <textarea
              className="input studio-prompt"
              rows={2}
              placeholder="What happens next — e.g. 'the camera follows the container onto a truck leaving Tanger Med, ends on a calm wide shot of the port at sunset'"
              value={extendPrompt}
              onChange={(e) => setExtendPrompt(e.target.value)}
              disabled={extendBusy}
            />
            <div className="studio-controls" style={{ borderTop: "none", paddingTop: 0 }}>
              <label className="ctl">
                <span>Model</span>
                <select className="select" value={extendModel} onChange={(e) => setExtendModel(e.target.value)} disabled={extendBusy}>
                  {(extendFor.veo?.provider === "omni" ? [extendFor.veo.model] : brand?.extendModels || [])
                    .map((m) => <option key={m} value={m}>{videoModelLabel(m, vInfo, brand?.videoResolutionsByModel)}</option>)}
                </select>
              </label>
              <label className="ctl">
                <span>Steps</span>
                <select className="select" value={extendSteps} onChange={(e) => setExtendSteps(Number(e.target.value))} disabled={extendBusy}>
                  {Array.from({ length: 20 }, (_, i) => i + 1)
                    .filter((n) => (extendFor.veo?.seconds || 8) + extendStep(extendModel, vInfo) * n
                      <= maxTotal(extendModel, vInfo))
                    .map((n) => {
                      const add = extendStep(extendModel, vInfo) * n;
                      const res = extendFor.veo?.resolution || "720p";
                      const cost = videoCost(extendModel, res, add, vInfo);
                      return (
                        <option key={n} value={n}>
                          +{add}s{extendFor.veo?.seconds ? ` (→ ~${Math.round(extendFor.veo.seconds + add)}s)` : ""}
                          {cost != null ? ` · ≈ ${usd(cost)}` : ""}
                        </option>
                      );
                    })}
                </select>
              </label>
              <div className="extend-actions">
                {extendBusy ? (
                  <button className="btn" onClick={() => { extendCancel.current = true; }}>Stop after this step</button>
                ) : (
                  <button className="btn" onClick={() => setExtendFor(null)}>Cancel</button>
                )}
                <button className="btn btn-primary" onClick={runExtensions} disabled={extendBusy || !extendPrompt.trim()}>
                  {extendBusy ? <Loader size={15} className="spin" /> : <FastForward size={15} />}
                  {extendBusy ? "Extending…" : "Extend"}
                </button>
              </div>
            </div>
            {extendStatus && <p className="studio-note">{extendStatus}</p>}
            {extendError && <div className="error-box" style={{ marginTop: 10 }}>{extendError}</div>}
          </div>
        )}

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
                    {a.kind}
                    {a.veo?.seconds ? ` · ${a.veo.seconds}s` : ""}
                    {a.veo?.resolution ? ` · ${a.veo.resolution}` : ""}
                    {" · "}{(a.bytes / 1024 / 1024).toFixed(1)} MB
                  </span>
                  {a.kind === "video" && a.veo && (
                    <button
                      className="btn"
                      disabled={
                        extendBusy ||
                        (a.veo.provider !== "omni" && a.veo.resolution && a.veo.resolution !== "720p")
                      }
                      title={
                        a.veo.provider === "omni"
                          ? "Extend by 10s with Omni Flash"
                          : a.veo.resolution && a.veo.resolution !== "720p"
                            ? `Veo only extends 720p videos (this one is ${a.veo.resolution})`
                            : "Extend by 7s with Veo"
                      }
                      onClick={() => {
                        openExtend(a);
                        window.scrollTo({ top: document.querySelector(".asset-grid")?.offsetTop - 220 || 0, behavior: "smooth" });
                      }}
                    >
                      <FastForward size={14} />
                    </button>
                  )}
                  {a.kind === "image" && (
                    <button
                      className="btn"
                      title="Animate this image with Veo"
                      onClick={() => {
                        setKind("video");
                        setSize("portrait");
                        setStartImage(a.file);
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      <Clapperboard size={14} />
                    </button>
                  )}
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
