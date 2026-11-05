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
