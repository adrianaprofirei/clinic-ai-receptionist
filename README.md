# Clinica Zâmbet · AI receptionist and lost-revenue calculator

[![CI](https://github.com/adrianaprofirei/clinic-ai-receptionist/actions/workflows/ci.yml/badge.svg)](https://github.com/adrianaprofirei/clinic-ai-receptionist/actions/workflows/ci.yml)

A sales demo and portfolio project for an agency that sells chatbots and booking
automation to clinics and salons in Iași, Romania. It shows a **real AI receptionist**
for a **fictional dental clinic**: it answers questions about services, prices, hours
and location, checks real availability, books appointments in the chat, and the
booking appears live on the reception dashboard. Below it, a calculator shows clinic
owners how many patients (and how much revenue) unanswered messages cost them.

![A visitor books in the chat (left) and the booking appears live on the reception dashboard (right)](docs/demo.gif)

*Demo mode (no API key), recorded in real time: the booking shows up on `/admin` about 50 ms after the visitor confirms.*

**Stack:** Python 3.12 · FastAPI · SQLAlchemy + SQLite · Uvicorn · plain HTML/CSS/JS
(no framework, no build step) · OpenAI tool calling · Docker Compose behind nginx.

---

## Contents

- [What's inside](#whats-inside)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [How the agent and its tools work](#how-the-agent-and-its-tools-work)
- [Protecting the API key (and the visitors)](#protecting-the-api-key-and-the-visitors)
- [The calculator](#the-calculator)
- [Privacy and data](#privacy-and-data)
- [Run locally](#run-locally)
- [Run with Docker](#run-with-docker)
- [Configuration](#configuration)
- [Deploy on an Ubuntu VPS (nginx + Certbot)](#deploy-on-an-ubuntu-vps-nginx--certbot)
- [Tests](#tests)
- [Decisions and assumptions](#decisions-and-assumptions)
- [Limitations and next steps](#limitations-and-next-steps)

## What's inside

| Page | What it does |
| --- | --- |
| **Landing page** `/` | Clinic hero with an example conversation, services and prices, how booking works, hours, address (click-to-load map), an emergency card, the calculator, and a contact section for the agency. Romanian copy; every page carries a "Demo – clinică fictivă" notice. |
| **Chat widget** | Floating launcher with a greeting bubble; a 400 × 640 panel on desktop and a full-screen chat on phones. Answers in the visitor's language (Romanian or English), shows free times as buttons, shows a privacy notice before asking for name and phone, asks for confirmation on a summary card, then shows a confirmation card with an "Add to calendar" (.ics) button. |
| **Demo mode** | Without `OPENAI_API_KEY` (or when the daily AI budget is spent, or the provider fails) the same widget runs a scripted flow with buttons: service → day → slot → name/phone → summary → confirm. The demo always works. |
| **Reception dashboard** `/admin` | Password-protected week view (Mon–Sat, 80 px per hour) that updates live over Server-Sent Events. New bookings from the assistant get a red ring, a "Nouă · asistent" tag and a toast. Staff can call or cancel; cancelling frees the slot in the chat immediately. On a phone: day tabs, a list with free gaps, and a bottom sheet. |
| **Calculator** | "Câți pacienți pierzi lunar?": four editable example inputs, live results per month and per year, and the formula in plain words, followed by the "Vrei asistentul ăsta pentru clinica ta?" call to action. |

The UI implements the project's design canvas (13 artboards: desktop and phone
landing, chat states for AI mode, demo mode and safety, desktop and phone admin, and
the visual system), using the same colours, type (Bricolage Grotesque and Figtree,
self-hosted) and components.

## Screenshots

| Landing page | Reception dashboard, right after a chat booking |
| --- | --- |
| ![Landing page](docs/screenshots/landing.webp) | ![Admin week view with a new booking and a toast](docs/screenshots/admin.webp) |

**AI mode: availability, summary, confirmation**

![Chat in AI mode: slot buttons, booking summary, confirmation card](docs/screenshots/chat-ai.webp)

**Safety and demo mode: emergency call card, refused prompt injection, scripted booking with consent**

![Emergency card, refusal of a prompt injection, demo-mode details form](docs/screenshots/chat-safety-demo.webp)

**Phone: landing page, calculator, reception day view**

![Phone views](docs/screenshots/mobile.webp)

**Calculator**

![Lost revenue calculator](docs/screenshots/calculator.webp)

<sub>The AI-mode screenshots were taken with a scripted stand-in for the model (no API key in the build environment), so a real model's wording will differ; the cards, buttons, validation and booking flow are the real application.</sub>

## Architecture

```mermaid
flowchart LR
    V["Visitor<br/>landing page + chat widget"]
    R["Reception<br/>/admin week view"]
    O["OpenAI API"]

    subgraph VPS["Ubuntu VPS"]
        N["nginx + Certbot<br/>TLS, unbuffered streams"]
        subgraph APP["FastAPI app · Uvicorn, 1 worker"]
            PG["Pages<br/>Jinja2 + static JS/CSS"]
            CH["/api/chat<br/>NDJSON stream"]
            GR["Guardrails<br/>length · rate limits · daily caps<br/>injection · emergency · phone masking"]
            AG["AI agent<br/>tool calling, last ~20 messages"]
            DM["Scripted demo flow"]
            FL["Booking flow<br/>propose, then explicit confirm"]
            SC["Scheduling rules<br/>hours · duration · no overlap · no past"]
            EB["Event bus"]
            AD["/api/admin<br/>week · cancel · SSE"]
            MT["Maintenance job<br/>24h cleanup + example bookings"]
        end
        DB[("SQLite<br/>bookings · slot claims<br/>chat sessions · usage")]
    end

    V --> N
    R --> N
    N --> PG
    N --> CH
    N --> AD
    CH --> GR
    GR -->|AI available| AG
    GR -->|no key, budget spent, provider down| DM
    AG <-->|chat completions| O
    AG -->|tool calls| FL
    DM --> FL
    FL --> SC
    SC --> DB
    FL --> EB
    EB -->|Server-Sent Events| AD
    AD --> DB
    MT --> DB
```

- **One source of truth.** Services, prices, durations, hours and contact details live in
  [`app/clinic.py`](app/clinic.py). The landing page, the system prompt, the scripted demo
  and the admin calendar all read from it.
- **All rules on the server.** [`app/scheduling.py`](app/scheduling.py) validates every
  booking, whichever channel it comes from: 15-minute grid, slot length = service duration,
  inside opening hours, not in the past (30-minute lead time), at most 30 days ahead, and
  never overlapping another booking.
- **Double booking is impossible, not just unlikely.** Besides the overlap check, every
  booking claims its 15-minute blocks in a `slot_claims` table whose primary key is the
  block start, so two requests racing for the same time cannot both commit. A test runs
  four threads against one slot and expects exactly one booking.
- **Live updates.** Booking writes publish to an in-process event bus; `/api/admin/stream`
  forwards them as Server-Sent Events with heartbeats. The dashboard falls back to polling
  every 3 seconds while the stream is down.
- **Streaming chat.** `/api/chat` answers with NDJSON events (`status`, `message`, `update`,
  `session`, `done`), so the widget can show "Caut orele libere de joi…" while a tool runs.
  A turn finishes on the server even if the visitor closes the tab.

## How the agent and its tools work

```mermaid
sequenceDiagram
    autonumber
    participant V as Visitor (chat widget)
    participant API as /api/chat
    participant LLM as OpenAI model
    participant T as Tools (server)
    participant DB as SQLite
    participant R as Reception (/admin)
    V->>API: Aveți loc mâine pentru un detartraj?
    API->>LLM: system prompt + last ~20 messages
    LLM->>T: get_available_slots(detartraj, 2026-11-05)
    T->>DB: free 15-minute starts where 45 min fit
    T-->>LLM: suggested times + free ranges
    API-->>V: reply + slot buttons
    V->>API: 11:00, then "Andreea Popescu, 0712 345 678"
    Note over API,LLM: the phone number reaches the model as [TEL-1]
    LLM->>T: create_booking(..., confirmed=false)
    T-->>API: validated proposal, nothing written yet
    API-->>V: privacy notice + summary card [Confirm] [Change]
    V->>API: presses Confirm (handled without the model)
    API->>DB: booking + slot claims
    API-->>V: confirmation card + .ics
    API-)R: SSE booking.created: toast + red ring
```

**Tools** (defined in [`app/chat/tools.py`](app/chat/tools.py)); they are the only way the
model can touch data, and every argument is validated again on the server:

| Tool | What it does |
| --- | --- |
| `list_services()` | Services with duration and price. |
| `get_available_slots(service, date)` | Free start times for that service on that day: about six well-spread suggestions (shown as buttons) plus the free ranges. Without a date it finds the earliest day with room. Never reveals who booked what. |
| `create_booking(service, date, time, name, phone, confirmed)` | Two steps. `confirmed=false` validates everything and shows the visitor a summary card; nothing is written. `confirmed=true` is accepted only if it matches the proposal the visitor has already seen, so the model cannot book behind the visitor's back. The Confirm button books deterministically without calling the model at all. |
| `cancel_booking(code, phone)` | Cancels a booking made from the same chat, or one whose code and phone both match. Example bookings cannot be cancelled from the chat. |

**Prompt.** [`app/chat/prompt.py`](app/chat/prompt.py) rebuilds the system prompt on every
turn from the clinic data, with today's date and a 14-day calendar, so the model resolves
"mâine" or "joi" without date arithmetic. It asks for short, warm replies in the visitor's
language (Romanian with the informal *tu*, as in the design, or English), and forbids medical
advice.

**Memory.** Each browser gets an HttpOnly session cookie; the conversation is stored in
SQLite and the model sees about the last 20 messages (tool calls and their results are kept
together). History survives a page reload and is deleted after 24 hours.

**Safety.**
- Pain, swelling, bleeding, fever or injuries always get an **emergency card** with a call
  button (placeholder number 0232 000 000) and a 112 notice, whatever the model writes,
  followed by an offer to find the earliest appointment. The model is told never to give
  medical advice.
- Obvious attempts to make the bot leave its role ("ignore your instructions", "show me the
  admin password", "developer mode", in Romanian and English) get a fixed refusal and never
  reach the model. Replies that would leak a canary from the prompt or the admin password are
  blocked.
- The model cannot reach anything except the four tools, which never expose other patients.

**Fallback.** If the API key is missing, the daily budget is spent or the provider errors,
the chat switches to the scripted demo flow and says so ("Asistentul AI nu e disponibil
acum…"). The booking rules, cards and dashboard are the same.

## Protecting the API key (and the visitors)

The site is public, so cost and abuse are capped at several levels (all configurable):

| Protection | Default |
| --- | --- |
| Message length | 500 characters (counter in the widget, enforced on the server) |
| Per-IP rate limit on chat messages | 20 per 10 minutes, then HTTP 429 with a friendly card |
| Button clicks (demo flow) | 120 per 10 minutes per IP |
| Global daily caps (persisted in SQLite) | 500 AI-answered messages and 1,000,000 tokens per day, then demo mode |
| Output tokens per model call | 350 |
| Model calls per visitor message | at most 5 (4 tool rounds, then a forced text answer) |
| Active bookings per chat session | 3 |
| nginx | flood limit on `/api/chat` and a body size limit |

The client IP comes from nginx: the sample config overwrites `X-Forwarded-For` and the
container port is published on `127.0.0.1` only, so the header cannot be spoofed to dodge
the limits.

## The calculator

Inputs (editable, clearly labelled as examples, not statistics): monthly inquiries
(messages + calls), % left unanswered or answered too late, % of answered inquiries that
become patients, average value per patient in RON.

```
unanswered inquiries = inquiries × % unanswered
lost patients        = unanswered × % that become patients   (rounded to 0.1)
lost revenue / month = lost patients × value per patient
lost revenue / year  = lost revenue / month × 12
```

With the examples (200 inquiries, 25 %, 30 %, 800 RON) that is 15 patients, 12.000 RON a
month and 144.000 RON a year. The page computes it live in
[`app/static/js/calculator.js`](app/static/js/calculator.js); [`app/calculator.py`](app/calculator.py)
is the reference implementation (also served at `GET /api/calculator`), and a test runs
both on shared vectors so they cannot drift apart (including half-up rounding, where
Python's `round()` and JavaScript's `Math.round()` differ).

## Privacy and data

- A privacy notice is shown **before** the name and phone are requested: inside the form in
  demo mode, as a card in AI mode.
- **Phone numbers never reach the model.** They are replaced by placeholders (`[TEL-1]`) before
  the conversation is sent to OpenAI and swapped back only on the server.
- Bookings and chats are deleted **24 hours after they were created** by a job that runs every
  15 minutes. Example bookings for the current and next week are generated so the calendar is
  never empty, and they are regenerated after they expire. To reset by hand before a demo:
  `docker compose exec app python -m app.maintenance --reset`.
- Only a functional cookie for the chat (`zb_chat`) and, for staff, the admin session cookie.
  No analytics, no third-party requests: fonts are self-hosted and the OpenStreetMap map loads
  only after a click. A short privacy page lives at `/confidentialitate`.
- Strict Content-Security-Policy (no inline scripts or styles), `X-Frame-Options: DENY`,
  `nosniff`, and a hardened container (non-root, read-only filesystem, no capabilities).

## Run locally

Requirements: Python 3.12 (Node.js is optional; it only runs one parity test).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then, optionally, set OPENAI_API_KEY and ADMIN_PASSWORD in it
uvicorn app.main:app --reload --env-file .env
```

Open <http://127.0.0.1:8000> (and <http://127.0.0.1:8000/admin>). Without `OPENAI_API_KEY`
the chat runs in demo mode. Without `ADMIN_PASSWORD`, a password is generated and printed in
the log. Run the tests with `pytest`.

## Run with Docker

```bash
docker compose up -d --build
docker compose logs -f
```

The app listens on <http://127.0.0.1:8000>. With no `.env` file it starts in demo mode and
logs a generated admin password; with `cp .env.example .env` you set the key, password and
contact details. Data lives in the `zambet-data` volume.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)); every one has a
safe default.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Enables AI mode. Empty = scripted demo mode. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Any chat model with tool calling. |
| `OPENAI_BASE_URL` | *(OpenAI)* | Any OpenAI-compatible endpoint. |
| `OPENAI_REASONING_EFFORT` | *(unset)* | For reasoning models (e.g. `minimal`); raise `MAX_REPLY_TOKENS` too. |
| `OPENAI_TEMPERATURE` | *(model default)* | Optional sampling temperature. |
| `OPENAI_TIMEOUT_SECONDS` | `20` | Per request, one retry. |
| `ADMIN_PASSWORD` | *(generated)* | Password for `/admin`. If empty, a random one is logged at startup. |
| `SECRET_KEY` | *(generated)* | Signs the admin cookie; set it so sessions survive restarts. |
| `RATE_LIMIT_MESSAGES` / `RATE_LIMIT_WINDOW_SECONDS` | `20` / `600` | Chat messages per IP per window. |
| `DAILY_MESSAGE_CAP` / `DAILY_TOKEN_CAP` | `500` / `1000000` | Global daily AI budget, then demo mode. |
| `MAX_REPLY_TOKENS` | `350` | Output tokens per model call. |
| `MAX_MESSAGE_CHARS` | `500` | Longest visitor message. |
| `HISTORY_MESSAGES` | `20` | Conversation memory sent to the model. |
| `MAX_BOOKINGS_PER_SESSION` | `3` | Active bookings one chat can hold. |
| `DATA_RETENTION_HOURS` | `24` | Bookings and chats older than this are deleted. |
| `MAINTENANCE_INTERVAL_MINUTES` | `15` | How often the cleanup runs (0 = only at startup). |
| `SEED_DEMO_DATA` | `true` | Example bookings so the calendar is never empty. |
| `AGENCY_NAME` / `CONTACT_EMAIL` | `Liminova` / `contact@exemplu.ro` | Agency name and placeholder email in the contact section. |
| `TIMEZONE` | `Europe/Bucharest` | Clinic time zone (bookings are stored in clinic-local time). |
| `COOKIE_SECURE` | `auto` | `auto` = Secure cookies when the request came over HTTPS. |
| `API_DOCS` | `false` | `true` serves OpenAPI docs at `/api/docs`. |
| `DATABASE_URL` | `sqlite:///./data/clinic.db` | Docker Compose uses `/app/data/clinic.db` in a volume. |
| `LOG_LEVEL` | `info` | |

Clinic data (services, prices, durations, opening hours, address, phone) is edited in
[`app/clinic.py`](app/clinic.py).

## Deploy on an Ubuntu VPS (nginx + Certbot)

The container listens on `127.0.0.1:8000`; nginx on the host terminates TLS and proxies to
it. Replace `demo.example.ro` with your domain (its DNS A/AAAA records must point to the VPS).

1. Install Docker (with the Compose plugin), nginx and Certbot:

   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
   ```

2. Get the code and configure it:

   ```bash
   git clone https://github.com/adrianaprofirei/clinic-ai-receptionist.git
   cd clinic-ai-receptionist
   cp .env.example .env
   nano .env   # OPENAI_API_KEY, ADMIN_PASSWORD, SECRET_KEY, CONTACT_EMAIL
   ```

3. Start the app and check it:

   ```bash
   sudo docker compose up -d --build
   curl http://127.0.0.1:8000/healthz
   ```

4. Add the nginx site from [`deploy/nginx/zambet.conf`](deploy/nginx/zambet.conf):

   ```bash
   sudo cp deploy/nginx/zambet.conf /etc/nginx/sites-available/zambet
   sudo sed -i 's/demo.example.ro/your-domain.ro/' /etc/nginx/sites-available/zambet
   sudo ln -s /etc/nginx/sites-available/zambet /etc/nginx/sites-enabled/zambet
   sudo nginx -t && sudo systemctl reload nginx
   ```

   The config turns buffering off for the streamed chat and the SSE stream, keeps the SSE
   connection open for an hour, and **overwrites** `X-Forwarded-For` (the app trusts it for
   rate limiting).

5. Get a certificate (Certbot edits the site to add HTTPS and the HTTP→HTTPS redirect, and
   renews automatically):

   ```bash
   sudo certbot --nginx -d your-domain.ro --redirect
   ```

   Then add `add_header Strict-Transport-Security "max-age=31536000" always;` to the `443`
   server block and reload nginx.

6. Firewall: only SSH, HTTP and HTTPS.

   ```bash
   sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
   ```

Updating: `git pull && sudo docker compose up -d --build`. Logs: `sudo docker compose logs -f`.
A fresh calendar before a sales meeting: `sudo docker compose exec app python -m app.maintenance --reset`.

Keep **one Uvicorn worker** (as in the Dockerfile): SQLite, the rate limiters and the event
bus that feeds the live dashboard live in that process. That is plenty for a demo site.

## Tests

`pytest` runs 168 tests in a few seconds (CI also builds the image and checks `/healthz`):

| File | Covers |
| --- | --- |
| `tests/test_scheduling.py` (46) | Slot generation (grid, durations, Saturday hours, Sunday, lead time, horizon), opening-hours validation, double-booking prevention (overlap, back-to-back, the database constraint, a 4-thread race), phone/name normalisation, cancellation |
| `tests/test_calculator.py` (16) | The calculator math, half-up rounding, clamping, Romanian formatting, JS/Python parity, the API |
| `tests/test_chat_demo.py` (17) | Chat API in demo mode: full booking, consent before contact fields, validation errors, slot taken before confirmation, summary expiry, double click, per-session cap, rate limit, length cap |
| `tests/test_agent.py` (19) | AI mode with a scripted model: tool calls, two-step confirmation, Confirm without the model, phone masking, injection refusal, emergency card, leak blocking, bounded tool rounds, history window, cancellations, daily caps, provider errors, the real OpenAI SDK adapter over a mock transport |
| `tests/test_guardrails.py` (42) | Injection and emergency detection (with false-positive cases), phone masking, reply scrubbing, language detection |
| `tests/test_admin.py` (14) | Login, lockout, signed cookies, auth on every endpoint, week data, cancellation, cross-site rejection, chat booking visible to the admin, the SSE stream |
| `tests/test_maintenance.py` (6) | Example bookings (no overlaps, deterministic), 24-hour cleanup, re-seeding, reset |
| `tests/test_pages.py` (8) | Pages render clinic data, demo notice, CSP and no inline scripts, asset caching |

## Decisions and assumptions

The brief asked me not to stop for questions, so these are the calls I made:

1. **One chair.** The clinic has a single dentist, so no two appointments may overlap. Supporting several doctors would add a resource id to the slot claims.
2. **Services, prices and durations** come from the design: consultation 30 min / 150 lei, scaling 45 min / 250 lei, whitening 90 min / 1.200 lei, filling 60 min / 300 lei, extraction 45 min / 350 lei, implant consultation 45 min / 200 lei (only the consultation is bookable).
3. **15-minute slot grid**, at least **30 minutes' notice**, at most **30 days ahead**; no public-holiday calendar.
4. **Confirmation is enforced by the server**, not just by the prompt: the model can only finalise a proposal the visitor has already seen, and the Confirm button bypasses the model.
5. **Name and phone in AI mode are typed into the chat** (as in the design), with the privacy notice shown first; phone numbers are masked before reaching OpenAI. Names are not masked.
6. **Cancelling from the chat** needs the booking code plus either the same chat session or the matching phone number.
7. **Romanian informal "tu"** in the copy, like the design; the model mirrors the visitor's language (Romanian or English) and server-written messages use a small RO/EN detector.
8. **Retention is rolling 24 hours by creation time**, for bookings (even future appointments) and chats. Example bookings are re-created when they expire, so the "daily reset" never leaves an empty calendar.
9. **Admin** uses one shared password from `ADMIN_PASSWORD` (generated and logged if unset), a signed 12-hour cookie and a 15-minute lockout after 5 wrong attempts.
10. **Live updates use SSE** with a 3-second polling fallback; on Sundays (closed) the dashboard opens on the coming week.
11. **Default model `gpt-4.1-mini`**, configurable, through the Chat Completions API so any OpenAI-compatible provider works.
12. **Streaming:** the chat streams progress events, but each model answer is shown whole (tool calls need the full response first).
13. **Jinja2** renders the pages so clinic data has one source of truth; there is still no frontend framework or build step.
14. **No database migrations.** Tables are created at startup; the data is ephemeral demo data. After a schema change, delete the volume.
15. **"Liminova" and `contact@exemplu.ro`** in the contact section come from the design and are placeholders, configurable with `AGENCY_NAME` and `CONTACT_EMAIL`. The address, phone (0232 000 000) and map location are fictional.
16. **Self-hosted fonts and a click-to-load map** to avoid third-party requests (GDPR).
17. The hero's example conversation always talks about the **next real weekday**, so it never shows a stale date.

## Limitations and next steps

- No SMS, WhatsApp or email confirmations yet; the same agent and tools could serve a WhatsApp channel.
- Rate limits and the live-update bus are per process, which is why the app runs one worker; scaling out would move them to Redis.
- Pattern-based injection detection is a first filter, not the security boundary: the real protection is that the model can only call four validated tools.
- No accessibility audit beyond the design's rules (44 px targets, visible focus, labels, live regions), and no automated end-to-end browser tests in CI.

## Project layout

```
app/
  clinic.py            services, prices, hours, contact (single source of truth)
  scheduling.py        slot generation and booking rules
  chat/                sessions, cards, booking flow, demo flow, agent, tools, prompt, LLM adapter
  routers/             pages, chat API, admin API + SSE
  guardrails.py        injection / emergency detection, phone masking, reply scrubbing
  maintenance.py       24-hour cleanup and example bookings (also a CLI)
  templates/, static/  HTML, CSS, JS, self-hosted fonts
deploy/nginx/          nginx site for the VPS
tests/                 pytest suite
```

---

Fonts: [Bricolage Grotesque](https://github.com/ateliertriay/bricolage) and
[Figtree](https://github.com/erikdkennedy/figtree), both under the SIL Open Font License
(licenses in `app/static/fonts`). Clinica Dentară Zâmbet is fictional; any resemblance to a
real clinic is accidental.
