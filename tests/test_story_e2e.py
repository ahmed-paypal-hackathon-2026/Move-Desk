"""TST-MD-07 (Person 4): the ten-step guided story runs in a real browser.

Runs against the hosted Render URL for the MD-G1 gate and audit AUD-04, or against a
local simulator for a pre-deploy check:

    pip install playwright
    python -m playwright install chromium
    MOVEDESK_E2E_URL=https://<service>.onrender.com python -m pytest tests/test_story_e2e.py -v

Without MOVEDESK_E2E_URL set (or without playwright installed) everything here skips,
so the offline suite stays green in CI and on every teammate's laptop.
"""
import os

import pytest

pwa = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("MOVEDESK_E2E_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not BASE_URL, reason="set MOVEDESK_E2E_URL, e.g. MOVEDESK_E2E_URL=https://movedesk.onrender.com"
)

DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}
STORY_STEPS = 10


def _open(page):
    """Open the dashboard and prove the demo bar is there (simulator mode only)."""
    page.set_default_timeout(120_000)  # a sleeping Render free service wakes in about a minute
    page.goto(BASE_URL + "/", wait_until="domcontentloaded")
    if not page.locator("#demo.on #play").is_visible():
        pytest.fail(
            "Play the story is unavailable: the app is not in simulator mode. "
            "The hosted demo must run with no PayPal keys and DEMO_SEED=1 (see render.yaml)."
        )


def _run_story(page):
    """Click through all ten steps; fail on any browser error along the way."""
    problems = []
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )

    page.locator("#play").click()
    step_text = page.locator("#tourStep")
    next_btn = page.locator("#tourNext")

    for i in range(1, STORY_STEPS + 1):
        pwa.expect(step_text).to_contain_text(f"Step {i} of {STORY_STEPS}")
        next_btn.click()
        if i == STORY_STEPS:
            pwa.expect(page.locator("#tour")).to_be_hidden()
        else:
            pwa.expect(step_text).to_contain_text(f"Step {i + 1} of {STORY_STEPS}")

    assert not problems, "browser errors during the story: " + "; ".join(problems)


def test_story_runs_all_ten_steps_without_console_errors():
    with pwa.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport=DESKTOP)
            _open(page)
            _run_story(page)
        finally:
            browser.close()


@pytest.mark.parametrize("path", ["/", "/chat"])
def test_mobile_has_no_horizontal_scroll(path):
    with pwa.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport=MOBILE)
            page.set_default_timeout(120_000)
            page.goto(BASE_URL + path, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)  # let AG Grid lay the board out
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 0, f"{path} overflows horizontally by {overflow}px on a 390px screen"
        finally:
            browser.close()
