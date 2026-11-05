"""Take the README screenshots of the running app (headless Chromium via Playwright).

    python scripts/take_screenshots.py --dashboard --dashboard-hero
    python scripts/take_screenshots.py --ask-answer --ask-refusal     # spends Gemini calls

The app must be running (docker compose up -d ask) at http://127.0.0.1:8501. Each shot waits
for real page state instead of sleeping: Streamlit's own script-state flag, no spinner or
skeleton, charts and tables actually drawn. If the page does not look right (an error box
instead of an answer, a blank chart), the script exits non-zero and writes no file.

Only --ask-answer and --ask-refusal make Gemini calls (about 1-3 each). --dashboard does not.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "http://127.0.0.1:8501"
VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 2
COLOR_SCHEME = "light"
TIMEOUT_MS = 120_000  # a real Gemini call plus the SQL can take a while
MAX_PAGE_HEIGHT = 8000
DASHBOARD_CHARTS = 5  # rows per day, temperature, rainfall, hourly profile, humidity

ASK_ANSWER_QUESTION = "Which city had the most rain between 2026-10-01 and 2026-10-07?"
ASK_REFUSAL_QUESTION = "What is Infosys's share price?"


@dataclass(frozen=True)
class Shot:
    path: Path
    route: str
    question: str | None
    kind: str  # "dashboard" | "answer" | "refusal"
    uses_gemini: bool
    viewport_only: bool = False  # True: one viewport (1440x900), not the whole page


SHOTS: dict[str, Shot] = {
    "dashboard": Shot(Path("docs/img/dashboard.png"), "/dashboard", None, "dashboard", False),
    "dashboard_hero": Shot(
        Path("docs/img/dashboard-hero.png"), "/dashboard", None, "dashboard", False, True
    ),
    "ask_answer": Shot(Path("docs/img/ask-answer.png"), "/", ASK_ANSWER_QUESTION, "answer", True),
    "ask_refusal": Shot(
        Path("docs/img/ask-refusal.png"), "/", ASK_REFUSAL_QUESTION, "refusal", True
    ),
}

_FAILURE_PREFIXES = (
    "Blocked",
    "The database could not run that query",
    "Questions are limited to",
)


# Streamlit's own toolbar (Deploy button, menu) is not part of the app; hide it in the images.
HIDE_CHROME_CSS = (
    '[data-testid="stToolbar"], [data-testid="stDeployButton"], '
    '[data-testid="stHeader"] { display: none !important; }'
)


class ScreenshotError(RuntimeError):
    """The page was not in the state a README screenshot needs."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Take README screenshots of the running app.")
    parser.add_argument("--dashboard", action="store_true", help="docs/img/dashboard.png")
    parser.add_argument(
        "--dashboard-hero",
        action="store_true",
        help="docs/img/dashboard-hero.png (top of the dashboard, one viewport)",
    )
    parser.add_argument(
        "--ask-answer", action="store_true", help="docs/img/ask-answer.png (spends Gemini calls)"
    )
    parser.add_argument(
        "--ask-refusal",
        action="store_true",
        help="docs/img/ask-refusal.png (spends Gemini calls)",
    )
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args(argv)
    args.shots = [name for name in SHOTS if getattr(args, name)]
    if not args.shots:
        parser.error("choose at least one of --dashboard, --ask-answer, --ask-refusal")
    return args


def looks_like_failure(text: str) -> bool:
    """True if an alert's text is one of the app's error messages rather than an answer."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from skysnap_ask import service

    plain = re.sub(r"\\(.)", r"\1", text).strip()  # the page backslash-escapes punctuation
    known = (
        service.LLM_UNAVAILABLE_ERROR,
        service.DB_UNAVAILABLE_ERROR,
        service.BAD_DRAFT_ERROR,
        service.LLM_REQUEST_ERROR,
        service.LLM_EMPTY_ANSWER_ERROR,
        service.BLANK_QUESTION_ERROR,
    )
    return plain.startswith(_FAILURE_PREFIXES) or any(message in plain for message in known)


def app_is_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/_stcore/health", timeout=5) as response:
            return response.read().strip() == b"ok"
    except OSError:
        return False


# ---- browser steps (need Playwright and a running app) ----------------------------------

_IDLE = """() => {
  const app = document.querySelector('[data-testid="stApp"]');
  return !!app
    && app.getAttribute('data-test-script-state') === 'notRunning'
    && !document.querySelector('[data-testid="stStatusWidget"]')
    && !document.querySelector('[data-testid="stSpinner"]')
    && !document.querySelector('[data-testid="stSkeleton"]');
}"""

_CHARTS_DRAWN = """(n) => {
  const charts = [...document.querySelectorAll('[data-testid="stVegaLiteChart"]')];
  return charts.length >= n && charts.every((c) => {
    const el = c.querySelector('canvas, svg');
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 50 && r.height > 50;
  });
}"""

_TABLE_DRAWN = """() => {
  const grid = document.querySelector('[data-testid="stDataFrame"] canvas');
  return !!grid && grid.getBoundingClientRect().width > 50;
}"""

_CONTENT_HEIGHT = """() => {
  const main = document.querySelector('[data-testid="stMain"]');
  return main ? main.scrollHeight : document.documentElement.scrollHeight;
}"""


def _wait_idle(page) -> None:
    page.wait_for_function(_IDLE, timeout=TIMEOUT_MS)


def _wait_charts(page, count: int) -> None:
    page.wait_for_function(_CHARTS_DRAWN, arg=count, timeout=TIMEOUT_MS)


def _alert_texts(page) -> list[str]:
    return page.locator('[data-testid="stAlert"]').all_inner_texts()


def _ask(page, shot: Shot) -> None:
    if _alert_texts(page):
        raise ScreenshotError("the page already shows an alert before asking anything")
    page.get_by_label("Your question").fill(shot.question)
    page.get_by_role("button", name="Ask", exact=True).click()
    if shot.kind == "answer":
        page.wait_for_selector('[data-testid="stCode"]', timeout=TIMEOUT_MS)
        page.wait_for_function(_TABLE_DRAWN, timeout=TIMEOUT_MS)
    else:
        page.wait_for_selector('[data-testid="stAlert"]', timeout=TIMEOUT_MS)
    _wait_idle(page)

    alerts = _alert_texts(page)
    if not alerts:
        raise ScreenshotError("no answer or refusal appeared")
    failures = [text for text in alerts if looks_like_failure(text)]
    if failures:
        raise ScreenshotError(f"the app reported a failure instead: {failures[0][:120]!r}")
    has_sql = page.locator('[data-testid="stCode"]').count() > 0
    if shot.kind == "answer" and not has_sql:
        raise ScreenshotError("the answer shows no SQL block")
    if shot.kind == "refusal" and has_sql:
        raise ScreenshotError("expected a refusal, but the app ran a query")


def _fit_to_content(page, charts: int) -> None:
    """Streamlit scrolls inside its main pane, so grow the viewport to the content height."""
    height = page.evaluate(_CONTENT_HEIGHT)
    height = min(max(int(height) + 24, VIEWPORT["height"]), MAX_PAGE_HEIGHT)
    page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
    _wait_idle(page)
    if charts:
        _wait_charts(page, charts)


def take(page, base_url: str, shot: Shot) -> Path:
    page.set_viewport_size(VIEWPORT)
    page.goto(base_url + shot.route)
    page.wait_for_selector('[data-testid="stApp"]', timeout=TIMEOUT_MS)
    page.add_style_tag(content=HIDE_CHROME_CSS)
    _wait_idle(page)
    charts = DASHBOARD_CHARTS if shot.kind == "dashboard" else 0
    if charts:
        _wait_charts(page, charts)
        if page.get_by_text("Could not load").count():
            raise ScreenshotError("the dashboard shows a 'Could not load' error")
        if page.locator('[data-testid="stException"]').count():
            raise ScreenshotError("the page shows an exception")
    else:
        _ask(page, shot)
    if not shot.viewport_only:
        _fit_to_content(page, charts)
    shot.path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shot.path), full_page=not shot.viewport_only)
    return shot.path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not app_is_up(args.base_url):
        print(f"error: the app is not reachable at {args.base_url}; nothing was done", file=sys.stderr)
        return 1
    from playwright.sync_api import sync_playwright  # imported late: CI has no browser

    status = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            color_scheme=COLOR_SCHEME,
            locale="en-US",
        )
        page = context.new_page()
        for name in args.shots:
            shot = SHOTS[name]
            try:
                print(f"{name}: wrote {take(page, args.base_url, shot)}")
            except Exception as exc:  # noqa: BLE001  (ScreenshotError or a Playwright timeout)
                print(f"{name}: FAILED, no file written: {exc}", file=sys.stderr)
                status = 1
        browser.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
