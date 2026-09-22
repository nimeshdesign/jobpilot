"""Hosted-mode test: auth gate, DB-backed files, applier refusal.

Runs with JOBPILOT_SERVERLESS=1 against an isolated database, which is exactly
the code path Vercel will take (minus Postgres, which needs a real DATABASE_URL).
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

PORT = 8796
ROOT = Path(__file__).resolve().parent.parent
TDATA = str(Path(tempfile.gettempdir()) / "jobpilot-test-hosted")
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
B = f"http://127.0.0.1:{PORT}"
PASSWORD = "correct-horse-battery-staple"
FAIL = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
shutil.rmtree(TDATA, ignore_errors=True)


def check(label, cond, detail=""):
    global FAIL
    if not cond:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  --> {detail}" if detail else ""))


def call(path, method="GET", body=None, cookie=None, expect_error=True):
    req = urllib.request.Request(B + path, method=method)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        if not expect_error:
            raise
        return e.code, e.read(), e.headers
    except Exception as e:
        return 0, str(e).encode(), {}


# ---- 1. refuses to start hosted with no password --------------------------
print("=== refuses to run hosted without a password ===")
env = {**os.environ, "JOBPILOT_SERVERLESS": "1", "JOBPILOT_DATA_DIR": TDATA,
       "JOBPILOT_PORT": str(PORT)}
# Empty, not absent: config.py also reads the developer's own .env, and
# load_dotenv never overwrites a variable that is already set. Popping these
# would let a real password leak in and this check would test nothing.
env["JOBPILOT_PASSWORD"] = ""
env["JOBPILOT_SECRET_KEY"] = ""
p = subprocess.run([PY, "-m", "app.main"], cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=90,
                   encoding="utf-8", errors="replace")
combined = (p.stdout or "") + (p.stderr or "")
check("startup blocked", "JOBPILOT_PASSWORD is not set" in combined,
      combined.strip().splitlines()[-1][:80] if combined.strip() else "no output")

# ---- 2. start it properly -------------------------------------------------
print("\n=== hosted instance with a password ===")
env["JOBPILOT_PASSWORD"] = PASSWORD
env["JOBPILOT_SECRET_KEY"] = "0123456789abcdef" * 4
srv = subprocess.Popen([PY, "-m", "app.main"], cwd=str(ROOT), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(45):
        time.sleep(1)
        code, _, _ = call("/login")
        if code == 200:
            up = True
            break
    check("started", up)

    # ---- 3. the gate ------------------------------------------------------
    print("\n=== auth gate ===")
    code, _, _ = call("/api/status")
    check("API blocked when signed out", code == 401, str(code))
    code, body, headers = call("/", )
    check("UI redirects to /login when signed out",
          code in (302, 307) or b"Sign in" in body, str(code))
    code, _, _ = call("/login")
    check("login page itself is reachable", code == 200, str(code))
    code, _, _ = call("/static/styles.css")
    check("login page CSS is reachable", code == 200, str(code))

    code, body, _ = call("/api/login", "POST", {"password": "wrong"})
    check("wrong password rejected", code == 401, str(code))

    code, _, headers = call("/api/login", "POST", {"password": PASSWORD},
                            expect_error=False)
    raw_cookie = headers.get("set-cookie", "")
    check("correct password accepted", code == 200, str(code))
    check("session cookie issued", "jobpilot_session=" in raw_cookie)
    check("cookie is HttpOnly", "HttpOnly" in raw_cookie, raw_cookie[:70])
    check("cookie is Secure (https only)", "Secure" in raw_cookie, raw_cookie[:70])
    check("cookie is SameSite=lax", "lax" in raw_cookie.lower())

    session = raw_cookie.split(";")[0]
    code, body, _ = call("/api/status", cookie=session)
    check("API works once signed in", code == 200, str(code))
    status = json.loads(body)
    check("reports hosted mode", status.get("hosted") is True)

    # a forged cookie must not work
    code, _, _ = call("/api/status", cookie="jobpilot_session=9999999999.deadbeef")
    check("forged cookie rejected", code == 401, str(code))

    # ---- 4. files must live in the database -------------------------------
    print("\n=== documents stored in the database, not on disk ===")
    resume = ("Test User\ntest@example.com | +91 90000 00000\n\n"
              "PROFESSIONAL SUMMARY\nFront-end developer, 5 years, React and Vue.\n\n"
              "TECHNICAL SKILLS\nReact, Vue.js, TypeScript, JavaScript, CSS, HTML\n\n"
              "WORK EXPERIENCE\nFront-End Developer | Acme | Jan 2021 - Present\n"
              "- Built dashboards in React.\n")
    boundary = "----jp"
    payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
               f"filename=\"r.txt\"\r\nContent-Type: text/plain\r\n\r\n").encode() \
        + resume.encode() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(B + "/api/resumes", data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Cookie", session)
    parsed = json.load(urllib.request.urlopen(req, timeout=120))
    check("resume uploaded", parsed["skill_count"] >= 5, str(parsed["skill_count"]))

    os.environ["JOBPILOT_DATA_DIR"] = TDATA
    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal
    from app.models import Application, Job, Resume

    with SessionLocal() as db:
        r = db.query(Resume).first()
        check("resume bytes stored in the row", bool(r.file_bytes), f"{len(r.file_bytes or b'')} bytes")
        check("no path recorded (disk is ephemeral)", not r.stored_path, r.stored_path)

        job = Job(source="t", external_id="j1", title="Senior Frontend Engineer",
                  company="Acme", location="Remote", is_remote=True,
                  url="https://job-boards.greenhouse.io/acme/jobs/1",
                  apply_url="https://job-boards.greenhouse.io/acme/jobs/1",
                  description="React, Vue.js, TypeScript.")
        db.add(job)
        db.commit()
        job_id = job.id

    code, body, _ = call("/api/applications", "POST",
                         {"job_id": job_id, "prepare": True}, cookie=session,
                         expect_error=False)
    app_row = json.loads(body)
    check("application prepared", app_row["status"] == "ready", app_row.get("error", ""))

    with SessionLocal() as db:
        a = db.query(Application).first()
        check("resume PDF bytes in the row", bool(a.resume_pdf_bytes),
              f"{len(a.resume_pdf_bytes or b'')} bytes")
        check("DOCX bytes in the row", bool(a.resume_docx_bytes),
              f"{len(a.resume_docx_bytes or b'')} bytes")
        check("cover letter bytes in the row", bool(a.cover_pdf_bytes),
              f"{len(a.cover_pdf_bytes or b'')} bytes")
        app_id = a.id

    for kind, sig in (("pdf", b"%PDF"), ("docx", b"PK"), ("cover", b"%PDF")):
        code, blob, _ = call(f"/api/files/{app_id}/{kind}", cookie=session,
                             expect_error=False)
        check(f"download {kind} served from the DB",
              code == 200 and blob.startswith(sig) and len(blob) > 1500,
              f"{code}, {len(blob)} bytes")

    # ---- 5. the applier must refuse, clearly ------------------------------
    print("\n=== form autofill on a hosted instance ===")
    code, body, _ = call("/api/apply/start", "POST", {"limit": 1}, cookie=session)
    detail = json.loads(body).get("detail", "")
    check("refused", code == 400, str(code))
    check("explains why and what to do",
          "browser" in detail and "locally" in detail, detail[:90])
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

print("\nFAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
