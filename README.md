# Ulting — Ads Management Made Easy

Audit and analysis dashboard for Meta ad accounts. Pulls the full account tree
plus insights, runs a rule set over it, and shows a scored report with
per-finding recommendations, trends, and audience/placement breakdowns.

Audits and AI analysis are read-only. The Ad manager can pause, resume, or change budgets only after a reviewed plan is confirmed.

- **Backend** — FastAPI (Python 3.14). Holds the Meta token, runs the audit.
- **Frontend** — React 19 + Vite SPA, dark rail + light canvas, session login.

The token carries `ads_management` (write access), so it never reaches the
browser: the SPA talks only to the API, and every data route requires a session.

For Docker deployment behind the existing server proxy, see [DEPLOY.md](DEPLOY.md).

## Setup

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
cd .. && cp .env.example .env.local                       # then fill it in

# 2. Frontend
cd frontend && npm install
```

Run both (two terminals):

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev            # http://localhost:5173
```

Vite proxies `/api` to port 8000, so the browser stays on one origin and the
HttpOnly session cookie is sent with every request.

### Configuration

Everything lives in `.env.local` at the repo root (gitignored). See
`.env.example` for the full list. The three that gate startup:

| Variable | Notes |
|---|---|
| `META_ACCESS_TOKEN` | Needs `ads_read`, `ads_management`, `business_management`, `pages_read_engagement`, `pages_show_list` |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | Login. Password may be plain text or a bcrypt hash (starts with `$2`) |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `OPENAI_API_KEY` | Strategist chat only. Everything else works without it. |

Login **fails closed**: if any of those are missing the endpoint returns 503
rather than defaulting to open. Check readiness at `GET /api/health`.

To hash a password:

```bash
python -c "import bcrypt;print(bcrypt.hashpw(b'yourpass',bcrypt.gensalt()).decode())"
```

### Long-lived tokens

Graph API Explorer tokens last ~1 hour. Exchange for 60 days:

```
GET https://graph.facebook.com/v23.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<SHORT_TOKEN>
```

60 days is the ceiling for a *user* token. For an install that should never need
re-authing, switch to a **System User token** in Business Manager — it does not
expire.

## API

| Route | Purpose |
|---|---|
| `POST /api/auth/login` · `/logout` · `GET /me` | Session |
| `GET /api/accounts` | Ad accounts the token can see + token status |
| `GET /api/audit/{id}` | Full payload: summary, findings, trend, entities |
| `GET /api/portfolio?accounts=act_1,act_2` | Several accounts audited together (max 6) |
| `GET /api/breakdown/{id}?cut=placement` | `placement, platform, device, age, gender, age_gender, country, region, hour` |
| `GET /api/export/{id}.csv` | Findings as CSV |
| `POST /api/strategy/chat` | Strategist chat, streamed as SSE |
| `GET /api/strategy/suggestions` | Opening prompts for the empty chat |
| `POST /api/creative/image` | Generate a branded image |
| `POST /api/creative/video` | Start a Sora render (poll `/video/{id}`) |
| `GET /api/creative/brand` | Persona status, logos, valid sizes |
| `POST /api/creative/preview-prompt` | The exact prompt the model will receive |
| `GET /api/creative/gallery` · `/asset/{name}` | Generated output |
| `POST /api/creative/ideas` | Creative briefs with ready-made prompts |
| `POST /api/ops/plan` | Preview changes — **never mutates** |
| `POST /api/ops/apply` | Apply a reviewed plan (fingerprint required) |
| `GET /api/ops/budget/{id}` | Budget allocation vs performance |
| `GET /api/ops/log` | Every write this app has made |
| `GET /api/health` | Config readiness, no auth |

Add `&fresh=1` to any data route to bypass the cache.

### The reporting window

Every data route takes the window in one of two shapes, never both — Meta
rejects a request carrying `date_preset` and `time_range` together:

```
?preset=last_30d               # today, yesterday, last_3d/7d/14d/28d/30d/90d,
                               # this_week_mon_today, this_month, last_month,
                               # this_quarter, maximum
?since=2026-08-01&until=2026-08-15
```

`backend/app/meta/window.py` resolves either shape to concrete dates, which is
also how the previous-period comparison is derived (Meta has no "previous
period" preset). Rolling presets **exclude today** the way Meta reports them —
"last 7 days" is the 7 complete days ending yesterday. Getting that wrong shifts
every delta by a day.

The window lives in the URL, so it survives reloads and is shareable. Sidebar
links carry the query string explicitly; a bare `to="findings"` drops it and the
window silently resets to the default.

## Layout

```
backend/app/
  main.py            FastAPI app + CORS
  config.py          settings from .env.local
  auth.py            bcrypt login, JWT in an HttpOnly cookie
  cache.py           5-minute TTL cache with per-key locks
  meta/client.py     Graph client: pagination, backoff, concurrency cap
  meta/metrics.py    insights row -> KPIs (the actions[] -> result mapping)
  meta/collect.py    account tree + insights + trend + breakdowns
  audit/rules.py     the 21 rules
  audit/engine.py    runs rules, scores, groups by category
frontend/src/
  charts/            hand-built SVG: TimeSeries, BarList, Funnel, GroupedBars, StatTile
  components/        DateRangePicker, Icons, Skeleton
  pages/             Login, Accounts, Overview, Findings, Campaigns, Breakdowns
  layout/Shell.jsx   collapsible sidebar + topbar
frontend/public/     logo assets (mark / wordmark / full lockup)
```

## The audit page

Built around triage, not enumeration. A flat list makes "this account cannot
pay its bills" a peer of "26 ads never got enough impressions", which is how
audit tools get skimmed and closed. The page answers three questions in order:

1. **Fix these first** — the top three by recoverable spend, except a
   `critical` always outranks an expensive `medium`: "the account cannot spend"
   is not a budgeting question. These deliberately ignore the filters below —
   filtering the list should not change the advice.
2. **By category** — where the damage is concentrated, doubling as a filter.
3. **The full list** — grouped by rule, sortable by recoverable spend, severity
   or entities affected.

Findings still collapse by rule (starved-ads alone fires 26 times on a real
account) and each group shows its money plus a bar of its share of the worst
finding, so the eye can rank without reading figures.

Each entity now shows its **evidence** — the raw values the rule fired on.
Every rule already recorded these and nothing displayed them; they are what let
someone check a finding instead of taking it on trust.

## The Strategist

A marketing engineer over the account: `backend/app/strategist.py` (agent +
tools), `backend/app/routers/strategy.py` (SSE), `frontend/src/pages/Strategist.jsx`
(chat). Runs on OpenAI chat completions with function calling; the model is
`OPENAI_MODEL` (default `gpt-4o`) so it can be changed without touching code.

**Tools read the snapshot already in memory, not Meta.** The account tree,
insights and audit are collected once per (account, window) and cached, and the
six tools slice that:

| Tool | Answers |
|---|---|
| `get_account_overview` | totals, trend, previous period, health score |
| `list_campaigns` | compare campaigns; filter by status/spend, sort by cost |
| `list_adsets` | delivery, budget split, learning stage, targeting |
| `list_ads` | creative performance and Meta's quality rankings |
| `get_breakdown` | placement / device / age / gender / country / hour |
| `get_audit_findings` | the rule engine's output, with its evidence |

So a ten-turn conversation costs **zero Graph calls**. Only `get_breakdown` can
reach Meta, and it shares the same 5-minute cache. That matters on a
development-access token, where a burst of insights calls trips the user-level
limit (see Rate limits).

The model gets data *access*, not a data dump: 10 campaigns / 21 ad sets / 82
ads pasted into every turn would burn context on ads nobody asked about and
still miss the breakdowns. Tool payloads run 1-5KB.

It is a **manual streaming loop** because tool activity is streamed to the
browser as it happens — the model often spends
10-20 seconds reading placements before writing a word, and a bare spinner for
that long reads as broken. The loop is capped at 12 tool rounds.

The system prompt carries the judgement, not just the tone: small samples are
noise under ~30 conversions, an ad set under ~50 weekly events is not being
optimised, editing an ad set restarts learning, attribution windows are not
comparable across ad sets, and currency never crosses accounts. It is told
never to state a figure that did not come from a tool result.

**Sample-size guardrails live in the data, not the prompt.** `get_breakdown`
tags every row with a `reliability` field and the payload carries a note. This
was not theoretical: with the rule only in the system prompt, the model read a
placement at $2.72 cost per result on *4 conversions* and recommended moving
budget into it. With the flag on the row it switched to the placement with 81
conversions and cited both sample sizes. An instruction several thousand tokens
earlier loses to a field on the row being read.

Transcripts are **not persisted** — answers are tied to one reporting window,
and showing yesterday's conclusions against today's numbers is worse than
losing them.

## Ad manager — the only write path

`backend/app/adops.py`, `backend/app/routers/adops.py`,
`frontend/src/pages/AdManager.jsx`. Pause, resume and daily-budget changes on
campaigns and ad sets. Everything else in this app is read-only; this is not,
so it is built around three rules.

**Plan, then apply.** `POST /ops/plan` returns the current and proposed value
for every entity plus a fingerprint, and mutates nothing — verified: after
seven plan calls including refused ones, the campaign was still `PAUSED` at
`2500` minor units at Meta. `POST /ops/apply` requires that fingerprint and
**re-plans against fresh data** (not the 5-minute cache) before acting. The
fingerprint hashes the `from` values as well as the `to`, so if anything moved
between review and confirm — someone editing in Ads Manager, say — the apply is
refused with a 409 rather than applied to a state nobody reviewed.

**Guardrails are server-side**, not UI politeness:

| Refused | Why |
|---|---|
| Raise > 5× in one step | The 2500 → 250000 minor-unit slip is the exact mistake |
| Daily budget < 1.00 | Stops delivery on most accounts |
| Entity not in this account | The id is checked against the snapshot, not trusted |
| Budget on an ad | Ads carry no budget; it belongs on the ad set |
| Switching budget type | Lifetime → daily is an Ads Manager job |

**Every apply is logged** to `backend/changes.jsonl`: timestamp, user,
entity, field, before, after, Meta's response, and whether it was a dry run. An
ads tool without an audit trail is one unexplained spend spike away from being
untrustworthy. Readable at `/ops/log` and in the UI.

There is also a **dry run**: "Validate with Meta" sends the change with Meta's
`execution_options: ["validate_only"]`, so Meta confirms it *would* succeed and
changes nothing. That is how the write path was proven without touching the
account.

Budgets are held in major units in the UI and converted once, at plan time, with
a single `round()` — minor-unit confusion is where money bugs live.

## Idea generator

`backend/app/promptgen.py`, surfaced in the creative studio. Returns complete
ads: framework, insight, hook, primary text, headline and CTA in the chosen
language (French, Darija, Arabic), an image prompt built to a fixed formula,
and an 8-second, three-beat video prompt.

The first version produced generic logistics clichés because of its *input*:
it told the model the winners were "Image 2" and "AD4" -- names, not content.
It now sees what a creative strategist would look at:

1. **The account's real ads** from Meta -- headline, body, CTA and the image
   itself (downloaded server-side and attached, since Meta's CDN links are
   signed and short-lived). Winners sorted by cost per result, plus ads that
   spent without converting. Ads with identical copy are merged so duplicates
   across ad sets don't crowd out everything else.
2. **The brand persona** (`brand/persona.md`).
3. **A sourcing calendar for Moroccan importers** (150 days): Golden Week,
   Canton Fair, 11.11, Black Friday, the Loi de Finances, Chinese New Year,
   Ramadan, Eid. For an importer the decision happens weeks before the event --
   the angle a generic trend search misses. Lunar dates are flagged approximate.
4. **Live web search** localised to Morocco (`country: MA`).

The model is `OPENAI_CREATIVE_MODEL` (default `gpt-5.5`) at high reasoning
effort. It is told to brainstorm at least three times as many concepts as
requested, kill the generic ones (the test: if you could swap ULTEx for any
freight forwarder, it's too generic), and return ideas that each use a
different framework *and* a different importer pain point. Every idea states
what evidence it builds on and what running it would test.

The response also reports what the model learned from, how many winning images
it actually saw, whether search ran, and any evidence notes -- e.g. that the
best-converting ads carry body copy about gold and silver investments.

## Creative studio

Brand-aware images and video from a prompt: `backend/app/creative.py`,
`backend/app/routers/creative.py`, `frontend/src/pages/Creative.jsx`. Images via
`gpt-image-2.5-flare` (`OPENAI_IMAGE_MODEL`); video via Google Veo 3.1 through the
Gemini API (`GEMINI_API_KEY`, `GEMINI_VIDEO_MODEL`, `backend/app/veo.py`) -- OpenAI shut the
Sora API down on 2026-09-24. A video can start from a generated ad as its first frame.

**The logo is composited, not drawn.** Image models garble logos and small type,
so the image is generated clean — the prompt explicitly asks for empty corner
space and forbids drawing a logo — and the real PNG is pasted on afterwards with
Pillow. Pixel-exact, free, and it cannot misspell the brand. Passing the logo as
a reference image (`images.edit`, `input_fidelity: high`) is available behind
`reference_logo`, but it *restyles* the logo rather than reproducing it, so it
is not the default.

**The wordmark variant is chosen from the backdrop.** The standard wordmark's
letters are brand blue and vanish against dark imagery — and logistics creative
is full of dusk ports and shadowed warehouses, so that is the common case. The
compositor samples the mean luminance of the exact region the logo will cover
and swaps to `ulting-wordmark-dark.png` when it is dark. Per corner, not per
image: a logo bottom-right on a dark half and top-left on a light half of the
same picture each get the right variant.

**The persona is a file, not a constant.** `brand/persona.md` is re-read on
every request — edit it and the next generation picks it up, no restart. It was
written from ultex.ma, so it carries the real positioning ("De l'usine à chez
vous", the French-first tone, port/warehouse/freight imagery) rather than
generic brand filler. Drop extra logo files into `brand/` and they appear in the
picker. `POST /api/creative/preview-prompt` shows exactly what gets sent, so the
persona is never a mystery.

**Video is a job, not a request.** Sora renders take minutes, so the route
starts the job and the client polls `progress`. Nothing blocks an HTTP worker
for the length of a render, and the download is keyed on the video id so a
re-poll reuses the file instead of fetching it again.

Output lands in `backend/generated/` (gitignored) and is served through
`/api/creative/asset/{name}`, which refuses any path resolving outside that
directory.

## Comparing several accounts

The sidebar switcher does two things: clicking a row opens that account
(client-side, no reload), and the checkboxes build a selection that opens
`/p/<id>,<id>` — several accounts audited together.

**Money is never summed across currencies.** These accounts are a mix of USD and
AED, and a headline "total spend" that adds dirhams to dollars is the classic
portfolio-dashboard bug — it reads roughly 4x too high and nobody notices.
`backend/app/portfolio.py` enforces the split:

| Combines | Does not combine |
|---|---|
| impressions, reach, clicks, results | spend, revenue |
| CTR, link CTR, landing-page rate | CPM, CPC, cost per result, ROAS |

Under a mixed selection the API returns `combined.spend: null` and populates
`spendByCurrency` instead; the UI shows a per-currency stack plus a banner, and
the comparison chart falls back to a **count** metric, because bars of dollars
beside bars of dirhams compare nothing.

`averageScore` is a straight mean, not spend-weighted: it answers "how healthy
are my accounts on average", and weighting would let one large account hide
several broken small ones.

One account failing does not sink the view — it lands in `failed[]` with its
error and the rest still render. Campaigns and Breakdowns stay single-account
only: across a portfolio they would stitch together entities that never competed
in the same auction.

## Frontend libraries

| Library | Used for |
|---|---|
| `recharts` | every chart with axes (time series, bar lists, grouped bars) |
| `lucide-react` | icons |
| `react-day-picker` + `date-fns` | the calendar inside the date picker |
| `@radix-ui/react-popover` | date picker panel — outside-click, focus trap, Escape |
| `@radix-ui/react-tooltip` | collapsed-rail labels |
| `sonner` | refresh / error toasts |
| `motion` | page fade on route change |

Recharts defaults do not match the house style — dashed gridlines, 1px strokes,
unbounded bar thickness, its own tooltip chrome. `src/charts/chartTheme.jsx`
holds the corrected props and every chart spreads them, so anything a chart
overrides is a visible exception rather than silent drift.

Recharts pulls in a dozen d3 packages and trebled the single bundle, so the
build splits `charts`, `motion` and `vendor` into their own chunks and the
routes are `React.lazy`-loaded. Login and the account picker never download the
charting code.

**Chart entry animations are off on purpose** (`isAnimationActive: false` in
`chartTheme.jsx`). Recharts replays the animation from zero on *any*
re-measure, and `ResponsiveContainer` re-measures whenever its box changes —
collapsing the sidebar, resizing the window. Left on, bars and lines blink empty
during ordinary interaction. The route-level fade in `Shell.jsx` carries the
motion instead.

Two chart rules worth keeping if you extend these:

- **No dual-axis charts.** Two measures of different scale get two charts
  (Spend/day and Results/day are deliberately separate). Aligning two y-scales
  on one plot invents a correlation that is not in the data.
- **Colour follows the entity, not its rank**, so filtering never repaints the
  survivors. One hue per bar chart; `emphasisId` highlights a single row and
  greys the rest when the story is about one campaign.

The funnel is deliberately **not** a Recharts chart: it is four labelled rows,
and a funnel plot would add axes and a legend without adding information.

## Branding & layout

Brand colours are sampled from the logo rather than guessed:

| Token | Hex | Use |
|---|---|---|
| `--brand-blue` | `#0b5cb8` | UI accent **and** chart series-1 |
| `--brand-gold` | `#f8c000` | brand chrome only — never data, never near a severity |
| `--seq-250…550` | blue ramp | ordinal (funnel) scale, derived from the brand hue |

The blue was checked against the categorical gates before being used in charts
and passes all-pairs CVD separation, so the accent and series-1 can be the same
colour. **The gold deliberately is not a data colour**: it measures ΔE 3.6
against the reserved status "warning" amber — indistinguishable — so using it
for a series or anywhere near a severity dot would make a warning and a brand
flourish look identical.

Logo assets are cropped from `public/ChatGPT Image ….png` (the original upload):

- `ulting-mark.png` — square U mark; collapsed rail, favicon, accounts page
- `ulting-wordmark.png` — mark + ULTING, no tagline; sidebar header
- `ulting-logo.png` — full lockup with tagline; login screen

The sidebar is a dark navy rail (`--sidebar-bg: #0c2544`) against the light
content canvas, with its own token set so nothing leaks into the cards or
charts. Every text and icon colour on it clears WCAG AA (lowest is the muted
label at 4.97:1).

Its active state is a light **blue**, not the brand gold. Gold sits a hair from
the status "warning" amber, and the severity alert pill lives in that same
column — a gold nav marker beside an amber alert is exactly the confusion the
palette note warns about.

`ulting-wordmark-dark.png` exists because the wordmark's letters are brand blue
and would nearly vanish on navy; it is the same asset with the blue recoloured
to white and the gold kept.

Expanded 248px / collapsed 68px, toggled from the pill on the edge and persisted
in `localStorage`. Collapsed, every label is hidden by one rule
(`.is-collapsed .nav-text`) and reachable again as a Radix tooltip — which
portals out of the rail, so the old `overflow` clipping problem is gone with it.
Below 900px the layout drops to the same icon rail rather than hiding
navigation altogether.

## Adding a rule

One entry in `RULES` at the bottom of `backend/app/audit/rules.py`. Rules are
pure functions of the snapshot — no API calls, no mutation — so they can be
tested against a saved payload.

```python
def _my_rule(s: dict) -> list[dict]:
    return [
        finding(
            "campaign", c["id"], c.get("name", ""),
            "What is wrong, with the numbers it fired on.",
            "What to actually do about it.",
            {"evidence": "raw values, so the finding can be defended"},
            impact=0.0,   # recoverable spend; leave 0 rather than guessing
        )
        for c in s["campaigns"] if ...
    ]

RULES.append(Rule("my-rule", "Short statement", Category.PERFORMANCE, Severity.HIGH, _my_rule))
```

A finding must name the entity and show its numbers. "CTR is low" with no
campaign attached is why people stop reading audit tools.

## Three Marketing API traps this code works around

**1. Level-scoped insights do not return the entity id unless you ask for it.**
`/act_X/insights?level=adset&fields=spend,impressions` returns bare metric rows
with no `adset_id`. Join on it and every row keys to `None`, so every entity
silently gets zero metrics — no error, HTTP 200, an audit that reports nothing
wrong. `collect.py` appends the id field to `fields` itself.

**2. Money comes back in minor units.** `amount_spent: "105023"` is $1,050.23;
`daily_budget: "1500"` is $15.00.

**3. Bursts trip the user-level rate limit before the account budget is near
full.** An audit needs ~10 insights calls; firing them all through one
`asyncio.gather` returns code 17 / subcode 2446079 while the account's own
`total_time` sits near 50%. The client caps concurrency at 3, which took a cold
audit from 38s (throttled, all backoff) to ~6s.

## Partial fetches

`collect_account` runs ~10 calls concurrently and the optional ones are wrapped
in `_safe`. That wrapper **records** failures into `warnings` rather than
swallowing them, and the UI shows an "this audit is incomplete" banner when the
list is non-empty.

That matters more than it looks: if the ad-level insights call is rate-limited,
every ad silently gets zero metrics, the creative rules stop firing, and the
health score goes *up*. An audit that quietly scores an account better because a
request failed is worse than an error.

## Rate limits

This app runs against Meta's **`development_access`** tier, which has a small
`ads_insights` budget. Two mitigations are built in — a 5-minute result cache
(tab switches cost nothing; Refresh forces `fresh=1`) and the concurrency cap
above — but heavy use will still hit it, surfacing as a `429` with a clear
message rather than a hang.

The real fix is to request **Advanced Access** for `ads_read` in the App
Dashboard. Check the current tier any time:

```bash
curl -sD- -o /dev/null "https://graph.facebook.com/v23.0/act_<ID>/insights?access_token=<TOKEN>&fields=spend" \
  | grep -i x-business-use-case-usage
```

## Scoring

100 minus weighted penalties, damped per rule with a square root: ten Learning
Limited ad sets is worse than one, but not ten times worse — it is one mistake
repeated. Without damping a single noisy rule drives every account to zero and
the score stops discriminating.

Severity weights: critical 25, high 12, medium 5, low 2, info 0.

## Charts

Hand-built SVG, no chart library, following one house style: 2px lines, ≥8px
markers with a 2px surface ring, bars capped at ~24px with a 4px rounded
data-end, hairline solid gridlines, tooltips on everything, and a table view so
no value is reachable only by hover.

Two rules worth keeping if you extend them:

- **No dual-axis charts.** Two measures of different scale get two charts
  (Spend/day and Results/day are deliberately separate). Aligning two y-scales
  on one plot invents a correlation that is not in the data.
- **Colour follows the entity, not its rank**, and categorical hues are assigned
  in fixed order — so filtering never repaints the survivors. The palette is
  validated for colour-vision deficiency at three simultaneous series.

## What this does not do yet

- **No persistence.** Results are cached for 5 minutes, not stored. There is no
  history, so "CPL rose 40% week over week" is not possible beyond the single
  previous-period delta the tiles already show. A `snapshots` table is the next
  step and the main thing standing between this and trend detection.
- **Single user.** One credential pair from env, not a user table.
- **Rules use fixed thresholds** (frequency 3, CTR 1%, LPV 60%). Defensible
  defaults, not tuned to your vertical.
- **The strategist has no memory across sessions.** Each conversation starts
  cold; it cannot recall what you tried last week or whether you acted on its
  advice. Persisting transcripts against the window they were asked in is the
  next step.
- **The strategist still cannot act.** It tells you the change to make; the ad
  manager is where changes happen, behind an explicit review and confirm. Wiring
  the two together is possible now that the plan/apply flow exists, but a model
  proposing a plan a human confirms is a different trust question from a model
  applying one.
- **No scheduling or rules.** Budget changes are manual. "Pause anything over
  2× target CPL on Friday" would need a scheduler and a policy engine.
