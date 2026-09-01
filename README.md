# JobPilot

A local web app that finds remote / visa-sponsoring jobs, scores them against your
resume, rewrites your resume for each one, and fills in the application forms.

Everything runs on your machine. There is no account, no server, no telemetry, and
no analytics. Your resume, your answers and your application history live in
`data/` as a SQLite file and a folder of documents. Nothing is uploaded anywhere
except the job boards you choose to fetch from, and the AI tailoring calls if you
opt into them by adding an API key.

---

## Setup

From PowerShell, in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

That creates a virtual environment, installs dependencies, downloads the Chromium
build used for form filling, and copies `.env.example` to `.env`.

Then start it:

```powershell
.\run.ps1
```

Your browser opens at <http://127.0.0.1:8765>.

### Optional: AI tailoring

**AI is not required.** Fetching, scoring, visa detection, salary, document
generation and — importantly — **filling in application forms** are all plain
Python with no model involved. The form-filler has zero AI in it.

The one thing AI changes is the *wording* of the tailored summary, bullets and
cover letter. Without a provider, a rule-based engine re-ranks and reorders the
bullets already in your resume and fills a template. That is perfectly usable
for volume applying, and free.

Pick a provider in `.env` (see `.env.example` for the full list):

**Free and private — a model on your own machine.** Install
[Ollama](https://ollama.com), run `ollama pull llama3.1:8b`, and JobPilot finds
it automatically. Nothing leaves your computer, which is the only option that
keeps that promise intact. On a machine without a usable GPU it runs on the CPU:
expect a few minutes per application, and prefer a small model
(`qwen2.5:3b`, `llama3.2:3b`) over an 8B one.

```
JOBPILOT_LLM_PROVIDER=ollama
JOBPILOT_OLLAMA_MODEL=llama3.1:8b
```

**Free tiers on a hosted API.** Anything speaking the OpenAI
`/chat/completions` shape works — Groq, OpenRouter (models ending `:free`),
Google's OpenAI-compatible endpoint, LM Studio, llama.cpp. Much faster than
local CPU, but your resume and the job description are sent to that provider.

```
JOBPILOT_LLM_PROVIDER=openai
JOBPILOT_OPENAI_BASE_URL=https://api.groq.com/openai/v1
JOBPILOT_OPENAI_API_KEY=your-key
JOBPILOT_OPENAI_MODEL=llama-3.3-70b-versatile
```

**Paid, best quality.** `ANTHROPIC_API_KEY=sk-ant-...` with `claude-opus-5`,
`claude-sonnet-5` or `claude-haiku-4-5`.

Small local models drift from the required JSON shape; JobPilot validates every
reply against the schema and feeds the error back once for a correction, so a
3B model is workable. If a provider fails or is unreachable, tailoring silently
falls back to the rule-based engine rather than failing the application.

---

---

## Publishing this to GitHub

`.gitignore` already excludes everything personal, and no source file contains
your details. What stays local:

| Excluded | Why |
|---|---|
| `data/resumes/` | your resume |
| `data/generated/` | documents carrying your name, email and phone |
| `data/screenshots/` | pictures of forms filled with your details |
| `data/browser/` | the browser profile — logged-in sessions and cookies |
| `*.db` | your profile, applications, notes and tracker history |
| `.env` | API keys |

Verify before the first push, and expect to see only source files:

```bash
git init
git add -A
git status --short        # nothing under data/ and no .env should appear
```

If you have already committed something personal, removing it in a later commit
is **not** enough — it stays in the history. Rewrite the history or start a
fresh repository.

## How to use it

The Dashboard runs the pipeline in three steps, deliberately separate so you can
inspect the output of each before moving on.

**1. Upload your resume** (Resume tab). PDF, DOCX or TXT. The app extracts your
contact details, skills, roles and bullet points, and shows you exactly what it
read — check this, because everything downstream depends on it. It also pre-fills
your profile from what it found.

**2. Fill in your profile** (Profile tab). This is what gets typed into
application forms: name, contact details, work-authorisation answers, notice
period, salary expectation. Also set what you are looking for — target titles,
required keywords, exclusions, preferred countries, minimum match score.

**3. Fetch and score jobs** (Dashboard → *Fetch & score jobs*). Pulls from every
enabled board and scores each posting against your resume.

**4. Review the matches** (Jobs tab). Sort by match, filter by visa status,
remote, or source. *Why this score* shows the full breakdown and the exact
sentences that drove the visa call.

**5. Queue and apply.** *Queue top matches* prepares your highest-scoring jobs —
each gets its own tailored resume PDF/DOCX and cover letter. *Start applying*
then walks the browser through each application form.

---

## About auto-submit

By default the applier **fills the form and stops**. It completes every field it
can answer, uploads the tailored resume, screenshots the finished form, and marks
the application *needs review*. You open it, check it, and submit yourself.

There is an auto-submit toggle. Before you turn it on, three things worth knowing:

- Most job boards' terms of service do not permit automated submission. Whether
  that matters to you is your call, but it is your account at risk, not the app's.
- Screening answers cannot be withdrawn. A wrong answer to "do you require
  sponsorship" is permanent, and it is usually a hard filter.
- The app tells you when it guessed. If a dropdown offers *"Yes, Netherlands
  Highly Skilled Migrant Visa"* and *"Yes, Germany Blue Card"* and you asked for
  "yes", it picks one and flags it under **check these before submitting**. With
  auto-submit on, nobody reads that flag.

Rate limiting is on by default: a configurable delay between applications and a
daily cap. Applications run one at a time, never in parallel — concurrent
sessions against one ATS look exactly like abuse.

---

## How the matching works

Each job gets a 0–100 score from four weighted components, all visible in the UI:

| Component | Weight | What it measures |
|---|---|---|
| Skill coverage | 40% | Weighted overlap between skills the posting asks for and skills in your resume. Hard skills count more than "communication". |
| Content similarity | 25% | TF-IDF cosine similarity between your full resume text and the job description. |
| Title fit | 15% | Overlap between the job title and your target titles / past titles. |
| Preferences | 20% | Remote, visa sponsorship, preferred countries, your must-have keywords. |

Then modifiers: a penalty for a large seniority gap, a penalty when the posting
asks for far more years than you have, and an outright zero if it contains one of
your exclusion keywords.

**Wrong professions are penalised.** Large employers paste the same boilerplate
into every opening, so a Legal Counsel or Benefits Analyst ad at a software
company still matches "Git, CSS, collaboration". Each title is classified into a
job family (engineering, design, data, product, finance, legal, people, sales,
marketing, support, operations); a role outside your own family takes a heavy
penalty, and an adjacent tech discipline takes a small one.

**Vague postings are damped.** A job description naming only one recognisable
skill that you happen to have would otherwise score 100% on coverage. Below six
named skills the score is blended toward neutral in proportion to how little the
posting actually said, so thin ads stop out-ranking detailed ones where you meet
eight requirements out of ten. The breakdown shows both the raw and damped figures.

---

## How visa detection works

Postings are classified into five states, each with the sentences that justify it:

- **visa sponsorship** — the posting actually offers sponsorship or relocation
  help ("we happily sponsor visas", "relocation package", "Blue Card").
- **likely sponsors** — the *employer* has a public track record of sponsoring,
  but this particular ad says nothing. Kept separate on purpose: firms like
  GitLab or Airbnb publish thousands of ads, and marking them all "sponsorship"
  made 60% of the database look sponsored and rendered the filter useless.
- **hires globally** — no sponsorship language, but the employer hires worldwide
  or through an employer of record. For a remote role the visa barrier does not
  apply, but this is *not* sponsorship and is deliberately labelled separately —
  folding "work from anywhere" into "sponsorship" fills the filter with roles
  that can never actually get you a visa.
- **no sponsorship** — explicitly ruled out. Negative phrasing always wins:
  "we are unable to offer visa sponsorship" contains the words "visa sponsorship".
- **not stated** — the posting says nothing. This is the common case; on a live
  sample of ~1,350 postings roughly nine in ten never mention it at all.

German phrasing is handled too, since the EU boards are where the sponsored roles
actually are.

---

## Tracking what happened after you applied

Submitting is the easy half. The Applications tab has a tracker for the part
that decides whether any of this is working.

Each application carries two independent states:

- **status** — what the automation did: `queued -> ready -> needs_review ->
  submitted` (or `failed`).
- **outcome** — what the employer did: `applied -> acknowledged -> screening ->
  interview -> final -> offer -> accepted`, or `rejected` / `withdrawn` /
  `ghosted`.

Set the outcome from the dropdown on each card as replies come in. Every change
is timestamped and kept as history, with an optional note, so you can see how
long each stage actually took rather than just where things ended up.

The panel at the top shows the numbers that matter:

| Metric | Meaning |
|---|---|
| Reply rate | share of submitted applications that got *any* response, including rejections |
| Interview rate | share that reached a screening call or beyond |
| Offer rate | share that ended in an offer |
| Avg days to reply | mean time from submission to first response |
| Needs follow-up | submitted, silent, and older than 14 days |
| Likely ghosted | silent for 45 days — realistically dead |

A rejection counts as a reply on purpose. A 40% reply rate with a 2% interview
rate is a resume problem; a 5% reply rate is a targeting problem. Lumping
rejections in with silence hides the difference.

Filter the list by any outcome, or by **⚑ Needs a follow-up** to see exactly
which applications are worth chasing this week.

---

## Salary expectations

One "desired salary" figure cannot serve applications to Berlin, London,
Toronto and a remote role at once. The figure typed into an "expected salary"
box is resolved from **the country of each posting**, scaled to your seniority
band (derived from your years of experience and your resume's own claim,
whichever is higher — undercutting yourself in that box costs real money).

Settings → *Salary expectations by country* shows exactly what will be typed
for every country, and lets you override any of them.

**The built-in numbers are rough starting estimates, not market data.** They are
annual gross figures in local currency for software / front-end roles. Check
them against levels.fyi, Glassdoor or a local salary survey and correct them —
your own research will always beat a table baked into an app. Countries with no
entry and no override fall back to the single figure in *Fallback*.

## Job sources

All public, documented, no-auth APIs:

| Source | Notes |
|---|---|
| Remotive | Curated remote jobs, worldwide |
| Arbeitnow | EU / Germany-heavy — the richest source of visa-sponsored roles |
| RemoteOK | High-volume remote board |
| Himalayas | Remote jobs with location-restriction metadata |
| Jobicy | Remote board with region filters |
| Greenhouse | Per-company career boards — edit the company list in Settings |
| Lever | Per-company career boards — off by default |

Greenhouse and Lever are per-company, and this is the highest-leverage setting
in the app: it is where visa-sponsoring employers actually post, and the jobs
come straight from the employer with a real ATS form the autofill handles well.
Edit the list in Settings -> *Company career boards*; the token is the name in
`boards.greenhouse.io/<token>` or `jobs.lever.co/<token>`. Tokens that do not
exist are skipped silently, so check a board loads in a browser before adding it.

---

## Application forms

Detected and handled: Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
Workday, BambooHR, Recruitee, Teamtailor, Jobvite, Personio, Breezy, plus a
generic fallback that works on many custom forms.

Field matching runs on each control's own label — never a neighbour's, which is
how autofill tools end up putting a first name in a LinkedIn box. Custom React
dropdowns are opened and clicked rather than typed into, because typing sets the
DOM value while the widget still displays "Select…" — that is how a blank answer
gets reported as answered.

Demographic and EEO questions are always answered "decline to self-identify".

The browser runs a persistent profile in `data/browser`, so if you log into a
company portal by hand once, that session is still there next time.

**Standard answers** (Settings) handle the recurring questions in your field:
when a field's label contains the phrase on the left, the answer on the right is
typed. Anything the app cannot answer confidently is listed for you rather than
guessed at.

---

## Layout

```
jobpilot/
  app/
    main.py              FastAPI app, static hosting, startup
    models.py            SQLAlchemy schema
    config.py  db.py
    routers/api.py       the HTTP API
    sources/             job-board connectors
      base.py            normalised JobPost, country/remote detection
      providers.py       one function per board
    services/
      resume_parser.py   PDF/DOCX/TXT -> structured resume
      salary.py          per-country salary expectations
      skills.py          skill taxonomy and extraction
      textutil.py        tokenizing, TF-IDF, cosine (no numpy/sklearn)
      matcher.py         the scoring model
      visa.py            sponsorship classification
      tailor.py          per-job tailoring (LLM + rule-based)
      docgen.py          ATS-safe DOCX / PDF output
      llm.py             Anthropic wrapper, degrades to None
      pipeline.py        fetch -> score -> tailor -> queue
      worker.py          background apply queue
      ats/
        fields.py        form-field patterns and option matching
        autofill.py      Playwright form driver
  web/                   single-page frontend, no build step
  data/                  SQLite, resumes, generated documents, screenshots
```

### Extending it

- **New skills** — add to `EXTRA_SKILLS` at the bottom of `services/skills.py`,
  same `{"Canonical": ["alias", ...]}` shape. Everything downstream picks it up.
- **New job board** — add a function in `sources/providers.py` decorated with
  `@source(...)`, returning `JobPost` objects. It appears in Settings automatically.
- **New form field** — add a `(regex, profile_key)` pair to `FIELD_PATTERNS` in
  `services/ats/fields.py`. Order matters: specific patterns go above generic
  ones, which is why the visa rules sit above the location rules.
- **Scoring weights** — `WEIGHTS` at the top of `services/matcher.py`.

---

## Known limits

- Scanned or image-only PDF resumes cannot be read; export a text-based PDF or
  upload the DOCX.
- Resume parsing is heuristic and PDF layouts vary wildly. Always check the
  Resume tab's "What JobPilot read from it" panel after uploading -- everything
  downstream depends on that parse. The **Re-parse** button re-runs the parser
  over the stored file without needing a re-upload.
- Tests and a second profile can be pointed at their own database with the
  `JOBPILOT_DB` environment variable, so they never touch your real data.
- Workday and LinkedIn Easy Apply usually require you to be logged in. Log in once
  with a visible browser (turn off headless in Settings) and the persistent
  profile keeps the session.
- Some ATS forms use CAPTCHAs. Those cannot be automated, and the app leaves them
  for you rather than trying.
- The rule-based tailoring engine reorders and selects your existing bullets; it
  does not rewrite them. That is what the AI engine adds.
- Neither tailoring engine will invent experience, employers, dates or skills you
  do not have. Requirements you do not meet are listed under *gaps* instead. An
  inflated resume falls apart in the first interview, and lying on an application
  is grounds for withdrawing an offer.
