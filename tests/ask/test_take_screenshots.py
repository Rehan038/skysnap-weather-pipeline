"""scripts/take_screenshots.py: the parts that need no browser."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import take_screenshots as ts
from skysnap_ask import service

ROOT = Path(__file__).resolve().parents[2]


def test_capture_settings_match_the_readme_spec():
    assert ts.BASE_URL == "http://127.0.0.1:8501"
    assert ts.VIEWPORT == {"width": 1440, "height": 900}
    assert ts.DEVICE_SCALE_FACTOR == 2
    assert ts.COLOR_SCHEME == "light"


def test_each_flag_maps_to_its_output_file():
    assert ts.SHOTS["dashboard"].path == Path("docs/img/dashboard.png")
    assert ts.SHOTS["ask_answer"].path == Path("docs/img/ask-answer.png")
    assert ts.SHOTS["ask_refusal"].path == Path("docs/img/ask-refusal.png")


def test_the_questions_are_the_agreed_ones():
    assert ts.SHOTS["ask_answer"].question == (
        "Which city had the most rain between 2026-10-01 and 2026-10-07?"
    )
    assert ts.SHOTS["ask_refusal"].question == "What is Infosys's share price?"
    assert ts.SHOTS["dashboard"].question is None


def test_only_the_ask_shots_can_spend_gemini_calls():
    assert ts.SHOTS["dashboard"].uses_gemini is False
    assert ts.SHOTS["ask_answer"].uses_gemini is True
    assert ts.SHOTS["ask_refusal"].uses_gemini is True


def test_dashboard_is_opened_on_its_own_page():
    assert ts.SHOTS["dashboard"].route == "/dashboard"
    assert ts.SHOTS["ask_answer"].route == "/"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--dashboard"], ["dashboard"]),
        (["--ask-answer"], ["ask_answer"]),
        (["--ask-refusal"], ["ask_refusal"]),
        (["--ask-answer", "--dashboard"], ["dashboard", "ask_answer"]),
        (["--dashboard", "--ask-answer", "--ask-refusal"], ["dashboard", "ask_answer", "ask_refusal"]),
    ],
)
def test_flags_select_shots_in_a_fixed_order(argv, expected):
    assert ts.parse_args(argv).shots == expected


def test_at_least_one_flag_is_required():
    with pytest.raises(SystemExit) as exc:
        ts.parse_args([])
    assert exc.value.code == 2


def test_base_url_can_be_overridden():
    assert ts.parse_args(["--dashboard", "--base-url", "http://127.0.0.1:9999"]).base_url == (
        "http://127.0.0.1:9999"
    )
    assert ts.parse_args(["--dashboard"]).base_url == ts.BASE_URL


def test_streamlit_toolbar_is_hidden_in_screenshots():
    """The Deploy button and menu are not part of the app and look odd in the README."""
    assert "stToolbar" in ts.HIDE_CHROME_CSS and "stDeployButton" in ts.HIDE_CHROME_CSS
    assert "display: none" in ts.HIDE_CHROME_CSS


def test_importing_the_script_does_not_import_playwright():
    """CI installs the package but not the browser; only running a capture needs it."""
    code = (
        "import sys; sys.path.insert(0, 'src'); import scripts.take_screenshots; "
        "raise SystemExit(1 if 'playwright' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=False)
    assert result.returncode == 0


@pytest.mark.parametrize(
    "text",
    [
        service.LLM_UNAVAILABLE_ERROR,
        service.DB_UNAVAILABLE_ERROR,
        service.BAD_DRAFT_ERROR,
        service.LLM_REQUEST_ERROR,
        service.LLM_EMPTY_ANSWER_ERROR,
        service.BLANK_QUESTION_ERROR,
        "Blocked: function SLEEP is not allowed",
        "The database could not run that query (error 1054).",
        "Questions are limited to 1,000 characters (yours has 2,000).",
    ],
)
def test_failure_messages_are_recognised(text):
    assert ts.looks_like_failure(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Mumbai had the most rain, 12.4 mm.",
        service.DEFAULT_REFUSAL,
        "I can't help with share prices; this data only covers weather.",
    ],
)
def test_normal_answers_and_refusals_are_not_failures(text):
    assert ts.looks_like_failure(text) is False


def test_alert_text_is_unescaped_before_the_failure_check():
    """The page escapes punctuation with backslashes; 'Blocked\\:' must still be recognised."""
    assert ts.looks_like_failure("Blocked\\: function SLEEP is not allowed") is True


def test_screenshot_paths_are_not_git_ignored():
    result = subprocess.run(
        ["git", "check-ignore", *map(str, (s.path for s in ts.SHOTS.values()))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "" and result.returncode == 1


def test_main_refuses_to_run_without_a_reachable_app(monkeypatch, capsys):
    """If the app is down, fail before launching a browser (and before any Gemini call)."""
    monkeypatch.setattr(ts, "app_is_up", lambda url: False)
    assert ts.main(["--ask-answer", "--base-url", "http://127.0.0.1:1"]) == 1
    assert "not reachable" in capsys.readouterr().err


# ---- --dashboard-hero: a viewport-only shot of the top of the dashboard -------------------


def test_hero_flag_selects_the_hero_shot():
    assert ts.parse_args(["--dashboard-hero"]).shots == ["dashboard_hero"]
    assert ts.parse_args(["--dashboard", "--dashboard-hero"]).shots == [
        "dashboard",
        "dashboard_hero",
    ]


def test_hero_shot_is_the_dashboard_page_without_gemini_and_not_full_page():
    hero = ts.SHOTS["dashboard_hero"]
    assert hero.path == Path("docs/img/dashboard-hero.png")
    assert hero.route == "/dashboard"
    assert hero.kind == "dashboard"
    assert hero.uses_gemini is False
    assert hero.viewport_only is True


def test_only_the_hero_is_viewport_only():
    assert [name for name, shot in ts.SHOTS.items() if shot.viewport_only] == ["dashboard_hero"]


def _png_size(path: Path) -> tuple[int, int]:
    import struct

    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


def test_committed_hero_image_is_exactly_one_viewport_at_2x():
    assert _png_size(ROOT / "docs" / "img" / "dashboard-hero.png") == (2880, 1800)


def test_committed_full_page_dashboard_image_is_2x_wide_and_taller_than_the_hero():
    width, height = _png_size(ROOT / "docs" / "img" / "dashboard.png")
    assert width == 2880 and height > 1800


def test_readme_images_exist_and_the_dashboard_shows_the_hero_with_a_full_page_link():
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\((docs/img/[^)]+)\)", readme):
        assert (ROOT / target).exists(), target
    assert "](docs/img/dashboard-hero.png)" in readme
    assert "[Full page](docs/img/dashboard.png)" in readme
    assert readme.index("dashboard-hero.png") < readme.index("[Full page]")
    assert "SCREENSHOT_PLACEHOLDER: docs/img/dashboard.png" not in readme
