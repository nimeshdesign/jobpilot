"""Playwright-driven application autofill.

Design notes
------------
* Runs a *persistent* browser profile (data/browser) so any logins you do by
  hand — LinkedIn, a company portal — survive between runs.
* Defaults to `auto_submit=False`: it fills everything, screenshots the finished
  form and stops. You look, then submit. Turn auto-submit on per run once you
  trust what it produces.
* Every field it touches is recorded, and every field it could NOT confidently
  fill is reported back, so you always know what to check.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ...config import BROWSER_PROFILE_DIR, SCREENSHOT_DIR, USER_AGENT
from . import fields as F

log = logging.getLogger("jobpilot.autofill")

SUBMIT_RE = re.compile(
    r"^\s*(submit(\s+application)?|send\s+application|apply(\s+now)?|finish|"
    r"complete\s+application)\s*$",
    re.IGNORECASE,
)
NEXT_RE = re.compile(r"^\s*(next|continue|proceed|save\s+(and|&)\s+continue)\s*$", re.IGNORECASE)
APPLY_LINK_RE = re.compile(r"^\s*(apply|apply\s+now|apply\s+for\s+this\s+job|"
                           r"i'?m\s+interested|start\s+application)\s*$", re.IGNORECASE)
COOKIE_RE = re.compile(r"^\s*(accept( all)?( cookies)?|allow all|agree|i agree|got it|ok)\s*$",
                       re.IGNORECASE)
SUCCESS_RE = re.compile(
    r"(thank you|application (was )?(submitted|received|complete)|we('| ha)ve received|"
    r"successfully (submitted|applied)|your application is in)",
    re.IGNORECASE,
)

# JS that tags every form control and reports what we need to fill it.
COLLECT_JS = r"""
() => {
  const out = [];
  const controls = document.querySelectorAll('input, textarea, select, [role="combobox"]');
  let idx = 0;
  const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g,' ').trim();

  for (const el of controls) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || tag).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    if (tag === 'div' && el.getAttribute('role') !== 'combobox') continue;
    // Mirror inputs that custom widgets keep around purely to drive native
    // validation. Filling them does nothing and listing them as "needs your
    // attention" just duplicates the real control.
    if (el.getAttribute('aria-hidden') === 'true') continue;
    if (el.getAttribute('tabindex') === '-1') continue;

    // A text input that is really a dropdown in disguise. Typing into one sets
    // the DOM value while the component still displays "Select...", so these
    // must be opened and clicked, never filled.
    const cls = (el.className || '').toString();
    const isCombobox = el.getAttribute('role') === 'combobox'
      || ['listbox','menu','true'].includes(el.getAttribute('aria-haspopup'))
      || el.getAttribute('aria-autocomplete') === 'list'
      || el.hasAttribute('aria-expanded')
      || /select__input|select-input|combobox/i.test(cls);

    // --- label resolution -------------------------------------------------
    // Only labels that provably belong to THIS control are trusted. An earlier
    // version walked up the DOM and grabbed the first label-ish node it saw,
    // which happily handed one field's label to the field next to it -- that is
    // how a LinkedIn box ends up holding a first name.
    const CONTROL_SEL = 'input:not([type=hidden]):not([type=submit]):not([type=button]),'
                      + 'textarea, select, [role="combobox"]';
    let label = '', labelTrusted = false;

    if (el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl && textOf(lbl)) { label = textOf(lbl); labelTrusted = true; }
    }
    if (!label) {
      const anc = el.closest('label');
      if (anc && textOf(anc)) { label = textOf(anc); labelTrusted = true; }
    }
    if (!label) {
      const ref = el.getAttribute('aria-labelledby');
      if (ref) {
        const parts = ref.split(/\s+/).map(id => {
          const node = document.getElementById(id);
          return node ? textOf(node) : '';
        }).filter(Boolean);
        if (parts.length) { label = parts.join(' '); labelTrusted = true; }
      }
    }
    if (!label) {
      // Ancestor scan, but only through ancestors that contain exactly one
      // control -- then any label inside can only be describing this one.
      let node = el.parentElement, depth = 0;
      while (node && depth < 4) {
        if (node.querySelectorAll(CONTROL_SEL).length !== 1) break;
        const cand = node.querySelector('label, legend, [class*="label"], [class*="Label"]');
        if (cand && textOf(cand)) { label = textOf(cand); labelTrusted = true; break; }
        node = node.parentElement; depth++;
      }
    }

    // --- group (the question a set of radios/checkboxes answers) -----------
    // Computed for choice controls only; blending it into a text field's blob
    // is another way to inherit a neighbour's wording.
    let group = '';
    const isChoice = type === 'radio' || type === 'checkbox';
    if (isChoice) {
      const fs = el.closest('fieldset');
      if (fs) {
        const legend = fs.querySelector('legend');
        if (legend) group = textOf(legend);
      }
      if (!group && el.name) {
        // The question usually sits just above the first option of the group.
        const first = document.querySelector('[name="' + CSS.escape(el.name) + '"]');
        let node = first ? first.parentElement : null, depth = 0;
        while (node && depth < 5 && !group) {
          const cand = node.querySelector('legend, h2, h3, h4, [class*="question"], [class*="Question"]');
          if (cand && textOf(cand)) group = textOf(cand);
          node = node.parentElement; depth++;
        }
      }
    }

    // Loosely-associated nearby wording. NEVER used to decide what to type --
    // only to give an unlabelled custom widget a name a human can recognise in
    // the "needs your attention" list.
    let nearbyText = '';
    if (!label) {
      let node = el.parentElement, depth = 0;
      while (node && depth < 5 && !nearbyText) {
        const cand = node.querySelector('label, legend, h2, h3, h4, [class*="label"], [class*="question"]');
        if (cand && textOf(cand)) nearbyText = textOf(cand);
        node = node.parentElement; depth++;
      }
    }

    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = type === 'file'
      ? style.display !== 'none' || true
      : (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden');

    el.setAttribute('data-jp-idx', String(idx));
    out.push({
      idx: idx,
      tag: tag,
      type: type,
      name: el.name || '',
      elId: el.id || '',
      placeholder: el.placeholder || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      label: (label || '').slice(0, 300),
      labelTrusted: labelTrusted,
      nearbyText: (nearbyText || '').slice(0, 300),
      group: (group || '').slice(0, 300),
      isCombobox: isCombobox,
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      value: (el.value || '').slice(0, 200),
      checked: !!el.checked,
      visible: visible,
      accept: el.getAttribute('accept') || '',
      options: tag === 'select'
        ? Array.from(el.options).map(o => ({ value: o.value, text: (o.text||'').trim() }))
        : []
    });
    idx++;
  }
  return out;
}
"""


@dataclass
class FillReport:
    ats: str = ""
    url: str = ""
    filled: dict[str, str] = field(default_factory=dict)
    unfilled: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    screenshot: str = ""
    submitted: bool = False
    success_detected: bool = False
    error: str = ""

    def note(self, message: str) -> None:
        self.log.append(message)
        log.info(message)

    def needs_human(self, field: str) -> None:
        """Record a field we would not guess at. Deduped: custom widgets often
        expose the same question as several elements."""
        if field not in self.unfilled:
            self.unfilled.append(field)


def detect_ats(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    table = {
        "greenhouse": ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"),
        "lever": ("lever.co", "jobs.lever.co"),
        "ashby": ("ashbyhq.com",),
        "workable": ("workable.com",),
        "smartrecruiters": ("smartrecruiters.com",),
        "workday": ("myworkdayjobs.com", "workday.com"),
        "bamboohr": ("bamboohr.com",),
        "recruitee": ("recruitee.com",),
        "teamtailor": ("teamtailor.com",),
        "jobvite": ("jobvite.com",),
        "personio": ("personio.de", "jobs.personio.com"),
        "breezy": ("breezy.hr",),
        "linkedin": ("linkedin.com",),
        "indeed": ("indeed.com",),
    }
    for name, domains in table.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return "generic"


# Text a bot-check interstitial shows instead of the page you asked for.
CHALLENGE_MARKERS = (
    "just a moment", "checking your browser", "enable javascript and cookies",
    "verify you are human", "attention required", "cf-browser-verification",
    "please stand by, while we are checking",
)

# Real ATS links that may be buried in an aggregator's job description.
ATS_LINK_RE = re.compile(
    r"https?://(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|jobs\.lever\.co"
    r"|jobs\.ashbyhq\.com|apply\.workable\.com|[\w.-]+\.recruitee\.com"
    r"|[\w.-]+\.teamtailor\.com|jobs\.smartrecruiters\.com)/[^\s\"'<>)]+",
    re.IGNORECASE,
)


# Job boards that publish a description page, never an application form. A link
# to one of these can never be filled in, whatever the autofiller does.
AGGREGATOR_HOSTS = (
    "jobicy.com", "remotive.com", "remoteok.com", "remoteok.io", "weworkremotely.com",
    "himalayas.app", "arbeitnow.com", "arbeitnow.co.uk", "linkedin.com", "indeed.com",
    "glassdoor.com", "ziprecruiter.com", "monster.com", "dice.com",
)


def is_aggregator(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == a or host.endswith("." + a) for a in AGGREGATOR_HOSTS)


def resolve_apply_url(url: str, description: str = "") -> tuple[str, str]:
    """Turn a listing URL into the URL of the actual application form.

    Aggregators and company careers pages hand out links to a *description*,
    not a form. Filling nothing on those pages looked like a broken autofiller
    when the real problem was that we were never on an application page.
    Returns (url, note) where note explains any substitution.
    """
    url = (url or "").strip()
    if not url:
        return url, ""

    host = (urlparse(url).hostname or "").lower()

    # Careers sites that embed Greenhouse expose the job id as gh_jid. The
    # embed endpoint renders the form directly and resolves the employer itself.
    match = re.search(r"[?&]gh_jid=(\d+)", url)
    if match and "greenhouse.io" not in host:
        return (
            f"https://boards.greenhouse.io/embed/job_app?token={match.group(1)}",
            "used the Greenhouse form behind this careers page (gh_jid)",
        )

    # A Lever posting page; its form lives one level down.
    if re.match(r"^https?://jobs\.lever\.co/[^/]+/[^/?#]+/?$", url):
        return url.rstrip("/") + "/apply", "used the Lever application form"

    # An Ashby posting page.
    match = re.match(r"^(https?://jobs\.ashbyhq\.com/[^/]+/[^/?#]+)/?$", url)
    if match:
        return match.group(1) + "/application", "used the Ashby application form"

    # Aggregator listing: look for a real ATS link inside the description text.
    if is_aggregator(url):
        found = ATS_LINK_RE.search(description or "")
        if found:
            return found.group(0), "used an ATS link found in the job description"

    return url, ""


def looks_like_a_form(page) -> tuple[bool, str]:
    """Is there actually something to fill here?"""
    try:
        counts = page.evaluate("""() => ({
          text: document.querySelectorAll(
            'input[type=text],input[type=email],input[type=tel],input:not([type])').length,
          file: document.querySelectorAll('input[type=file]').length,
          area: document.querySelectorAll('textarea').length,
          combo: document.querySelectorAll('[role=combobox], select').length,
        })""")
    except Exception:
        return False, "the page could not be inspected"

    if counts["file"] or counts["text"] >= 3 or (counts["text"] and counts["area"]):
        return True, ""

    try:
        body = (page.inner_text("body", timeout=4000) or "").lower()[:3000]
    except Exception:
        body = ""

    if any(marker in body for marker in CHALLENGE_MARKERS):
        return False, (
            "the site showed a bot check instead of the page. Open this one "
            "yourself — anti-bot pages cannot be automated"
        )
    if not body.strip():
        return False, "the page loaded empty"
    return False, (
        "no application form on this page — the link goes to a job-board "
        "listing rather than an employer's form. Open it and use its own "
        "Apply button"
    )


def playwright_available() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, (
            "Playwright is not installed. Run:  pip install playwright  "
            "then:  python -m playwright install chromium"
        )
    return True, ""


# --------------------------------------------------------------------------- #
def _dismiss_cookies(page, report: FillReport) -> None:
    try:
        for text in ("Accept all", "Accept All Cookies", "Accept cookies", "Accept", "I agree",
                     "Allow all", "Got it"):
            button = page.get_by_role("button", name=re.compile(f"^{re.escape(text)}$", re.I))
            if button.count() and button.first.is_visible():
                button.first.click(timeout=2500)
                report.note(f"Dismissed cookie banner ('{text}')")
                page.wait_for_timeout(400)
                return
    except Exception:
        pass


def _open_application_form(page, ats: str, report: FillReport) -> None:
    """Some boards show the posting first; click through to the real form."""
    if ats == "lever" and "/apply" not in page.url:
        candidate = page.url.rstrip("/") + "/apply"
        try:
            page.goto(candidate, wait_until="domcontentloaded", timeout=30000)
            report.note(f"Navigated to Lever application form: {candidate}")
            return
        except Exception:
            pass

    # Generic: if the page has no text inputs yet, look for an Apply button.
    try:
        if page.locator("input[type='text'], input[type='email']").count() >= 2:
            return
        for name in ("Apply for this job", "Apply now", "Apply", "Submit application",
                     "I'm interested"):
            control = page.get_by_role("button", name=re.compile(re.escape(name), re.I))
            if not control.count():
                control = page.get_by_role("link", name=re.compile(re.escape(name), re.I))
            if control.count() and control.first.is_visible():
                control.first.click(timeout=4000)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                page.wait_for_timeout(1200)
                report.note(f"Clicked '{name}' to open the application form")
                return
    except Exception as exc:
        report.note(f"Could not auto-open the form ({type(exc).__name__}); continuing anyway")


def _locator(page, idx: int):
    return page.locator(f"[data-jp-idx='{idx}']").first


def _read_back(page, idx: int) -> str:
    """What the control actually holds now, as the page sees it."""
    try:
        return page.evaluate(
            "(i) => { const e = document.querySelector(`[data-jp-idx='${i}']`);"
            " if (!e) return ''; return (e.value !== undefined && e.value !== null && e.value !== '')"
            " ? String(e.value) : (e.innerText || e.textContent || '').trim(); }",
            idx,
        ) or ""
    except Exception:
        return ""


def _pick_from_listbox(
    page, item: dict, value: str, report: FillReport, display: str = ""
) -> bool:
    """Drive a custom dropdown: open it, then click the option that matches.

    React/Ashby/Greenhouse comboboxes ignore a plain `fill()` -- the DOM value
    changes but component state does not, so the widget still reads "Select...".
    """
    locator = _locator(page, item["idx"])
    try:
        locator.scroll_into_view_if_needed(timeout=2500)
        locator.click(timeout=4000)
        page.wait_for_timeout(450)
    except Exception:
        return False

    option_selectors = (
        "[role='option']",
        "li[id*='option']",
        "[class*='select__option']",
        "[class*='option']:not(select):not(optgroup)",
    )

    def try_pick(min_score: int) -> bool:
        for selector in option_selectors:
            try:
                options = page.locator(selector)
                count = min(options.count(), 250)
                if not count:
                    continue

                best_score, best_index = 0, -1
                for i in range(count):
                    option = options.nth(i)
                    if not option.is_visible():
                        continue
                    text = (option.inner_text(timeout=1500) or "").strip()
                    score = F.option_matches(text, value)
                    if score > best_score:
                        best_score, best_index = score, i

                if best_index >= 0 and best_score >= min_score:
                    chosen = (options.nth(best_index).inner_text(timeout=1500) or "").strip()
                    options.nth(best_index).click(timeout=3000)
                    page.wait_for_timeout(400)
                    # A loose match means the list offered qualified variants --
                    # "Yes, Netherlands Highly Skilled Migrant Visa" for a plain
                    # "yes". Filling it beats leaving it blank, but the choice is
                    # a guess and the human has to see it before submitting.
                    if best_score < 90 and chosen:
                        report.needs_human(
                            f"{display or item.get('label') or 'dropdown'} — auto-picked "
                            f"\"{chosen[:70]}\", confirm this is right"
                        )
                    return True
            except Exception:
                continue
        return False

    # Pass 1 -- short lists (Yes/No, EEO choices) render everything up front, so
    # a strong match here is trustworthy. The bar is high on purpose: a long
    # alphabetical list may not even have scrolled to the right entry yet, and a
    # weak match would happily send "India" to "British Indian Ocean Territory".
    if try_pick(min_score=70):
        return True

    # Pass 2 -- long lists (countries, universities, schools) expect you to type
    # to narrow them down. Now a looser match is safe: the list is already
    # filtered to what we asked for.
    try:
        locator.type(value[:40], delay=25, timeout=6000)
        page.wait_for_timeout(800)
        if try_pick(min_score=50):
            return True
    except Exception:
        pass

    # Nothing matched: close the popup so it does not swallow the next click,
    # and leave the field for the human rather than guessing.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    return False


def _fill_text(
    page, item: dict, value: str, report: FillReport, display: str = ""
) -> bool:
    """Fill a text-ish control and prove it took. Never report success blind."""
    idx = item["idx"]

    # Custom dropdown: `fill()` would set the DOM value and silently leave the
    # widget reading "Select...", which is how a blank answer gets reported as
    # answered. Open it and click the option instead.
    if item.get("isCombobox"):
        return _pick_from_listbox(page, item, value, report, display)

    before = _read_back(page, idx)

    try:
        locator = _locator(page, idx)
        locator.scroll_into_view_if_needed(timeout=2500)
        locator.fill(value, timeout=5000)
    except Exception as exc:
        report.note(f"Could not type into '{item['label'] or item['name']}': {type(exc).__name__}")
    else:
        after = _read_back(page, idx)
        # A real text input echoes what we typed. A combobox does not.
        if after.strip() and (value[:25].lower() in after.lower() or after.strip() != before.strip()):
            return True

    # Looks like a custom dropdown wearing an <input>. Try it as one.
    if _pick_from_listbox(page, item, value, report, display):
        after = _read_back(page, idx)
        if after.strip() and after.strip().lower() not in ("", "select...", "select"):
            return True
        # The chosen text may live in a sibling node rather than on the input.
        return True

    return False


def _fill_select(page, item: dict, value: str, report: FillReport) -> bool:
    best_score, best_option = 0, None
    for option in item.get("options", []):
        text = option.get("text", "")
        if not text or text.lower() in ("", "select...", "select an option", "choose"):
            continue
        score = F.option_matches(text, value)
        if score > best_score:
            best_score, best_option = score, option

    if not best_option or best_score < 50:
        return False
    try:
        _locator(page, item["idx"]).select_option(
            value=best_option["value"], timeout=4000
        )
        return True
    except Exception:
        try:
            _locator(page, item["idx"]).select_option(
                label=best_option["text"], timeout=4000
            )
            return True
        except Exception as exc:
            report.note(f"Select failed for '{item['label']}': {type(exc).__name__}")
            return False


def _fill_file(page, item: dict, path: str, report: FillReport) -> bool:
    if not path or not Path(path).exists():
        return False
    try:
        _locator(page, item["idx"]).set_input_files(path, timeout=10000)
        page.wait_for_timeout(1200)  # let the upload widget settle
        return True
    except Exception as exc:
        report.note(f"Upload failed for '{item['label'] or item['name']}': {type(exc).__name__}")
        return False


def _handle_radio_group(page, group_items: list[dict], value: str, report: FillReport) -> bool:
    best_score, best_item = 0, None
    for item in group_items:
        option_text = item.get("label") or item.get("ariaLabel") or item.get("value") or ""
        score = F.option_matches(option_text, value)
        if score > best_score:
            best_score, best_item = score, item
    if not best_item or best_score < 50:
        return False
    try:
        locator = _locator(page, best_item["idx"])
        locator.scroll_into_view_if_needed(timeout=2500)
        locator.check(timeout=4000, force=True)
        return True
    except Exception as exc:
        report.note(f"Radio failed for '{group_items[0].get('group', '')}': {type(exc).__name__}")
        return False


def fill_page(page, values: dict[str, str], report: FillReport, custom_answers: dict) -> None:
    """One pass over every visible control on the current page."""
    try:
        items = page.evaluate(COLLECT_JS)
    except Exception as exc:
        report.note(f"Could not enumerate form fields: {exc}")
        return

    radio_groups: dict[str, list[dict]] = {}
    for item in items:
        if item["type"] == "radio" and item.get("name"):
            radio_groups.setdefault(item["name"], []).append(item)

    handled_radio_groups: set[str] = set()

    for item in items:
        if not item.get("visible") and item["type"] != "file":
            continue

        # Only this control's own wording goes into the blob. `group` is folded
        # in for radios/checkboxes further down, where it is the actual question.
        blob = F.normalize_label(
            item.get("label", "") if item.get("labelTrusted") else "",
            item.get("ariaLabel", ""), item.get("placeholder", ""),
            item.get("name", ""), item.get("elId", ""),
        )
        display = (item.get("label") or item.get("group") or item.get("placeholder")
                   or item.get("ariaLabel") or item.get("nearbyText")
                   or item.get("name") or f"unlabelled field #{item['idx']}")[:90]

        # --- user-defined answers win over everything ---
        custom_value = None
        for fragment, answer in (custom_answers or {}).items():
            if fragment and fragment.strip().lower() in blob:
                custom_value = str(answer)
                break

        key = F.classify(blob)

        # ---------------- radios ----------------
        if item["type"] == "radio":
            name = item.get("name", "")
            if not name or name in handled_radio_groups:
                continue
            handled_radio_groups.add(name)
            group_blob = F.normalize_label(item.get("group", ""), name)
            group_key = F.classify(group_blob) or key
            desired = custom_value or (values.get(group_key, "") if group_key else "")
            if not desired:
                report.needs_human(f"{item.get('group') or name} (choice)")
                continue
            if _handle_radio_group(page, radio_groups[name], desired, report):
                report.filled[item.get("group") or name] = desired
            else:
                report.needs_human(f"{item.get('group') or name} (choice)")
            continue

        # ---------------- checkboxes ----------------
        if item["type"] == "checkbox":
            if item.get("checked"):
                continue
            if F.CONSENT_PATTERNS.search(blob):
                try:
                    _locator(page, item["idx"]).check(timeout=3000, force=True)
                    report.filled[display] = "checked (consent)"
                except Exception:
                    report.needs_human(f"{display} (consent checkbox)")
            elif custom_value and custom_value.lower() in ("yes", "true", "1"):
                try:
                    _locator(page, item["idx"]).check(timeout=3000, force=True)
                    report.filled[display] = "checked"
                except Exception:
                    report.needs_human(display)
            continue

        # ---------------- files ----------------
        if item["type"] == "file":
            accept = (item.get("accept") or "").lower()
            path = ""
            if key == "cover_letter_file" or "cover" in blob:
                path = values.get("cover_letter_file", "") or ""
            if not path:
                path = values.get("resume_file", "")
            if path and (not accept or any(
                ext in accept for ext in (".pdf", ".doc", "application", "*")
            )):
                if _fill_file(page, item, path, report):
                    report.filled[display] = Path(path).name
                else:
                    report.needs_human(f"{display} (file upload)")
            continue

        # ---------------- selects ----------------
        if item["tag"] == "select":
            desired = custom_value or (values.get(key, "") if key else "")
            if not desired:
                if item.get("required"):
                    report.needs_human(f"{display} (dropdown)")
                continue
            if _fill_select(page, item, desired, report):
                report.filled[display] = desired
            else:
                report.needs_human(f"{display} (dropdown)")
            continue

        # ---------------- text / textarea ----------------
        if item.get("value"):
            continue  # already populated (autofill or a saved profile)

        value = custom_value or (values.get(key, "") if key else "")
        if not value:
            if item.get("required"):
                report.needs_human(f"{display} (required)")
            continue

        if key == "cover_letter" and item["tag"] != "textarea":
            value = value.split("\n\n")[0][:300]

        if _fill_text(page, item, value, report, display):
            report.filled[display] = value if len(value) < 60 else value[:57] + "…"
        else:
            report.needs_human(display)


def _click_button(page, pattern: re.Pattern, report: FillReport, what: str) -> bool:
    for role in ("button", "link"):
        try:
            control = page.get_by_role(role, name=pattern)
            count = control.count()
            for i in range(min(count, 4)):
                candidate = control.nth(i)
                if candidate.is_visible() and candidate.is_enabled():
                    candidate.scroll_into_view_if_needed(timeout=2000)
                    candidate.click(timeout=6000)
                    report.note(f"Clicked {what}")
                    return True
        except Exception:
            continue

    # last resort: raw input[type=submit]
    if what == "submit":
        try:
            raw = page.locator("input[type='submit'], button[type='submit']")
            if raw.count() and raw.first.is_visible():
                raw.first.click(timeout=6000)
                report.note("Clicked submit (fallback selector)")
                return True
        except Exception:
            pass
    return False


# --------------------------------------------------------------------------- #
def apply_to_job(
    apply_url: str,
    values: dict[str, str],
    *,
    headless: bool = False,
    auto_submit: bool = False,
    custom_answers: dict | None = None,
    screenshot_name: str = "application",
    keep_open_seconds: int = 0,
    description: str = "",
) -> FillReport:
    """Open the posting, fill the application form, optionally submit."""
    from playwright.sync_api import sync_playwright

    resolved, note = resolve_apply_url(apply_url, description)
    report = FillReport(ats=detect_ats(resolved), url=resolved)
    if note:
        report.note(f"{note}: {resolved}")
    apply_url = resolved

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=headless,
            viewport={"width": 1440, "height": 960},
            user_agent=USER_AGENT,
            accept_downloads=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.set_default_timeout(20000)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            report.note(f"Opening {apply_url} (detected ATS: {report.ats})")
            page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            _dismiss_cookies(page, report)
            _open_application_form(page, report.ats, report)
            page.wait_for_timeout(800)

            # Bail out loudly rather than "filling" a page with no form on it.
            # Reporting "0 fields filled" made a wrong URL look like a broken
            # autofiller, which sent me hunting in the wrong place.
            has_form, why = looks_like_a_form(page)
            if not has_form:
                report.error = why
                report.note(f"Stopping: {why}")
                report.url = page.url
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                shot = SCREENSHOT_DIR / f"{screenshot_name}.png"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    report.screenshot = str(shot)
                except Exception:
                    pass
                return report

            # Fill, then follow up to 3 "Next" steps for multi-page forms.
            for step in range(4):
                fill_page(page, values, report, custom_answers or {})
                report.note(f"Pass {step + 1}: filled {len(report.filled)} fields")

                if _click_button(page, SUBMIT_RE, report, "submit") if auto_submit else False:
                    report.submitted = True
                    page.wait_for_timeout(4000)
                    break

                if step < 3 and _click_button(page, NEXT_RE, report, "next step"):
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1500)
                    continue
                break

            report.url = page.url

            if report.submitted:
                try:
                    body = page.inner_text("body", timeout=5000)
                    report.success_detected = bool(SUCCESS_RE.search(body))
                except Exception:
                    pass
                report.note(
                    "Submitted — confirmation text detected" if report.success_detected
                    else "Submitted — no confirmation text found, verify manually"
                )
            else:
                report.note(
                    "Form filled and left un-submitted for your review"
                    if not auto_submit else "Could not find a submit button"
                )

            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            shot = SCREENSHOT_DIR / f"{screenshot_name}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
                report.screenshot = str(shot)
            except Exception as exc:
                report.note(f"Screenshot failed: {type(exc).__name__}")

            if keep_open_seconds > 0 and not headless:
                report.note(f"Leaving the browser open for {keep_open_seconds}s")
                time.sleep(keep_open_seconds)

        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            report.note(f"Failed: {report.error}")
            try:
                shot = SCREENSHOT_DIR / f"{screenshot_name}-error.png"
                page.screenshot(path=str(shot), full_page=True)
                report.screenshot = str(shot)
            except Exception:
                pass
        finally:
            try:
                context.close()
            except Exception:
                pass

    return report
