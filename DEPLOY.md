# Deploying to Vercel

This puts **your** instance online, password-protected, so you can find, score
and tailor from any device. It is not a public demo — nobody without the
password sees anything.

## What does not go with it

**Form autofill stays on your machine, permanently.** It drives a real Chromium
(~430 MB against Vercel's 250 MB function limit) and needs to hold that browser
open for minutes per application, which a serverless function cannot do. The
hosted instance refuses that one action with a message saying so.

Everything else works: job fetching, match scoring, visa detection, per-country
salary, resume parsing, tailored resume and cover-letter generation, downloads,
and the application tracker.

The intended split: **hosted** to find and prepare, **local** to actually fill
the forms. Both talk to the same database if you point your local `.env` at the
hosted `DATABASE_URL`.

---

## 1. A Postgres database

The serverless filesystem is wiped between requests, so SQLite cannot be used.
Any hosted Postgres works — Vercel Postgres, Neon, Supabase all have free tiers.

Create one and copy its connection string. It looks like:

```
postgresql://user:password@host/dbname?sslmode=require
```

## 2. Generate a session key

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Without a stable key, every serverless instance signs sessions differently and
you are logged out constantly. The app refuses to start without one.

## 3. Deploy

Push to GitHub, then on **vercel.com → Add New → Project**, import the repo.
Framework preset: **Other**. Leave build and output settings empty — the
`vercel.json` in the repo handles it.

Add these environment variables before deploying:

| Variable | Value |
|---|---|
| `DATABASE_URL` | your Postgres connection string |
| `JOBPILOT_PASSWORD` | the password you will sign in with |
| `JOBPILOT_SECRET_KEY` | the key from step 2 |
| `JOBPILOT_LLM_PROVIDER` | `openai` (or leave unset for no AI) |
| `JOBPILOT_OPENAI_BASE_URL` | `https://api.groq.com/openai/v1` |
| `JOBPILOT_OPENAI_API_KEY` | your Groq key |
| `JOBPILOT_OPENAI_MODEL` | leave empty — it picks a current model itself |

`VERCEL=1` is set by the platform, which is what switches the app into hosted
mode: password required, documents stored in the database rather than on disk.

## 4. First run

Open the deployment URL, sign in, upload your resume, then **Fetch & score
jobs**.

Fetching is the one slow operation. Vercel's free tier caps a function at 60
seconds, and pulling every source plus 30 company boards takes longer than
that. Fetch a few sources at a time, or run the fetch locally against the same
`DATABASE_URL` and let the hosted instance read the results.

---

## Safety notes

- The app **refuses to start** in hosted mode without `JOBPILOT_PASSWORD`,
  rather than quietly publishing your resume, phone number, salary
  expectations and application history to anyone with the URL.
- The session cookie is HttpOnly, SameSite=lax and Secure.
- Use a long, unique password. Anyone who has it has your whole job search.
- Your data now lives on Vercel and your database host, not only on your
  machine. That is the trade for reaching it from anywhere; if that matters
  more than convenience, stay local-only.

## Running local and hosted together

Point your local `.env` at the same database and both see the same jobs and
applications:

```
JOBPILOT_DATABASE_URL=postgresql://...same as above...
```

Then use the local instance for **Start applying** — it has the browser.
