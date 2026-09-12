"""UI-S5 UX polish deliverables (T-009).

These tests pin the polish iteration to the implementation:

* responsive layout rules exist for typical desktop widths and for narrow
  windows, including a keyboard skip link and a footer-free compact section
  navigation;
* loading, empty, disabled, conflict, and failure states stay explicit and
  API-derived, with per-failure guidance that never invents backend state;
* a completed answer's citations can be navigated by arrow keys and by
  previous/next controls, and the local PDF opens through the existing
  file-content endpoint with an optional `#page=` viewer hint;
* lightweight keyboard shortcuts exist for section navigation, focusing the
  active view's main input, submitting the research prompt, and opening the
  shortcut reference — and they never fire while the user is typing.

The offline DOM smoke (`ui/scripts/smoke-ux-polish.mjs`) is wired into the
frontend `verify` script and is also executed here when Node.js and the built
bundle are available.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
UI_DIST = UI_ROOT / "dist"
UX_SMOKE = UI_ROOT / "scripts" / "smoke-ux-polish.mjs"

CSS = UI_SRC / "index.css"
SHORTCUTS_HOOK = UI_SRC / "app" / "useKeyboardShortcuts.ts"
SHORTCUTS_DIALOG = UI_SRC / "components" / "ShortcutsDialog.tsx"
MODAL = UI_SRC / "components" / "Modal.tsx"
ASYNC_STATE = UI_SRC / "components" / "AsyncState.tsx"
ANSWER_CITATIONS = UI_SRC / "features" / "conversations" / "AnswerCitations.tsx"
PROMPT_COMPOSER = UI_SRC / "features" / "conversations" / "PromptComposer.tsx"
ACTIVE_RUN = UI_SRC / "features" / "runs" / "ActiveRunPanel.tsx"
APP_SHELL = UI_SRC / "app" / "AppShell.tsx"
NAVIGATION = UI_SRC / "app" / "navigation.ts"
PDF_LIB = UI_SRC / "lib" / "pdf.ts"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------- responsive


def test_responsive_rules_cover_desktop_and_narrow_widths():
    css = _text(CSS)
    for breakpoint in (
        "@media (min-width: 901px)",
        "@media (max-width: 1180px)",
        "@media (max-width: 900px)",
        "@media (max-width: 640px)",
    ):
        assert breakpoint in css, f"missing responsive breakpoint {breakpoint}"
    # The conversation list stays reachable in a long conversation on desktop.
    assert ".research-nav {\n    position: sticky;" in css.replace("\r\n", "\n")
    # Narrow windows collapse the section navigation into a horizontal strip.
    assert ".sidebar__nav {\n    flex-direction: row;" in css.replace("\r\n", "\n")
    # Long dialogs own their scrolling instead of overflowing the viewport.
    assert "max-height: calc(100vh - 96px)" in css
    assert ".modal__body {\n  min-height: 0;\n  overflow-y: auto;\n}" in css.replace("\r\n", "\n")


def test_shell_provides_a_keyboard_skip_link_and_content_target():
    shell = _text(APP_SHELL)
    assert 'className="skip-link"' in shell
    assert 'href="#main-content"' in shell
    assert 'id="main-content"' in shell
    assert "tabIndex={-1}" in shell
    assert ".skip-link" in _text(CSS)


# --------------------------------------------------------- explicit states


def test_failure_states_explain_the_next_step_without_inventing_status():
    state = _text(ASYNC_STATE)
    assert "FAILURE_GUIDANCE" in state
    for guidance in (
        "Confirm the local TransitScholar server is running",
        "reports this capability as unavailable",
        "The backend rejected these values",
        "reports a conflicting state",
        "not present on this local server",
    ):
        assert guidance in state, f"failure guidance is missing: {guidance}"
    assert 'data-testid="error-state"' in state
    assert "data-failure-kind={kind}" in state
    # The API-provided code and message still drive the user-visible state.
    assert "apiError.code" in state
    assert "apiError.message" in state


def test_disabled_and_loading_controls_stay_visible_and_explicit():
    button = _text(UI_SRC / "components" / "Button.tsx")
    assert "aria-busy={busy}" in button
    assert "disabled={disabled || busy}" in button
    # The run status is announced from the API-projected Run state.
    active = _text(ACTIVE_RUN)
    assert 'aria-live="polite"' in active
    assert "display_status" in active


# -------------------------------------------------- citation/PDF ergonomics


def test_pdf_page_hint_is_pure_viewer_presentation():
    pdf = _text(PDF_LIB)
    assert "export function pdfUrlForPage" in pdf
    assert "export function formatPageList" in pdf
    assert "export function firstPage" in pdf
    # The fragment is a page locator appended to the same content URL.
    assert "fragment.set('page'" in pdf


def test_citation_dialog_supports_arrow_and_previous_next_navigation():
    citations = _text(ANSWER_CITATIONS)
    assert 'data-testid="citation-position"' in citations
    assert 'data-testid="citation-previous"' in citations
    assert 'data-testid="citation-next"' in citations
    assert "'ArrowRight'" in citations
    assert "'ArrowLeft'" in citations
    # The plain file-content URL stays the default open target, with an
    # optional page-specific hint beside it.
    assert 'data-testid="open-citation-pdf"' in citations
    assert 'data-testid="open-citation-pdf-page"' in citations
    assert "pdfUrlForPage(pdfHref, targetPage)" in citations
    assert "api.papers.fileContentUrl(" in citations
    assert 'target="_blank"' in citations


# --------------------------------------------------------------- shortcuts


def test_shortcuts_are_safe_and_never_fire_while_typing():
    hook = _text(SHORTCUTS_HOOK)
    assert "export function useKeyboardShortcuts" in hook
    assert "export function focusPrimaryInput" in hook
    assert "NAVIGATION_CHORDS" in hook
    assert "isTypingTarget" in hook
    assert "hasModifier" in hook
    for key in ("'?'", "'/'", "'g'"):
        assert key in hook, f"the shortcut hook does not handle {key}"
    assert "'INPUT'" in hook and "'TEXTAREA'" in hook


def test_shell_wires_shortcuts_and_the_reference_dialog():
    shell = _text(APP_SHELL)
    assert "useKeyboardShortcuts(" in shell
    assert "focusPrimaryInput" in shell
    assert "ShortcutsDialog" in shell
    assert 'data-testid="shortcuts-help-button"' in shell
    assert "document.title" in shell

    dialog = _text(SHORTCUTS_DIALOG)
    assert 'testId="shortcuts-dialog"' in dialog
    assert "Keyboard shortcuts" in dialog
    assert 'data-testid="shortcut-list"' in dialog

    # The chord letters live in the product navigation model.
    navigation = _text(NAVIGATION)
    for shortcut in ("shortcut: 'w'", "shortcut: 'r'", "shortcut: 'l'", "shortcut: 'k'", "shortcut: 's'"):
        assert shortcut in navigation, f"navigation is missing {shortcut}"


def test_research_prompt_is_sendable_from_the_keyboard():
    composer = _text(PROMPT_COMPOSER)
    assert 'data-primary-input="research-prompt"' in composer
    assert "handlePromptKeyDown" in composer
    assert "event.ctrlKey" in composer and "event.metaKey" in composer
    assert "event.preventDefault()" in composer


def test_modal_traps_and_restores_focus_and_locks_page_scroll():
    modal = _text(MODAL)
    assert "FOCUSABLE_SELECTOR" in modal
    assert "event.key !== 'Tab'" in modal
    assert "restoreFocusRef" in modal
    assert "document.body.classList.add('modal-open')" in modal
    assert "document.body.classList.remove('modal-open')" in modal
    assert ".modal-open" in _text(CSS)


# ------------------------------------------------------------- evidence


def test_ux_polish_smoke_harness_is_available_and_wired():
    assert UX_SMOKE.is_file(), "the UX polish smoke harness is missing"
    body = _text(UX_SMOKE)
    for marker in (
        "shortcuts-help-button",
        "shortcuts-dialog",
        "citation-reference-1",
        "citation-reference-2",
        "citation-position",
        "citation-previous",
        "citation-next",
        "open-citation-pdf",
        "open-citation-pdf-page",
        "prompt-input",
        "/content#page=",
    ):
        assert marker in body, f"the UX polish smoke does not exercise {marker}"

    scripts = _package()["scripts"]
    assert "smoke:ux" in scripts
    assert "smoke-ux-polish.mjs" in scripts["smoke:ux"]
    assert "npm run smoke:ux" in scripts["verify"]


def test_ux_polish_smoke_passes_against_the_built_bundle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to drive the built frontend")
    if not (UI_DIST / "index.html").is_file():
        pytest.skip("run `npm run build` in ui/ before the UX polish smoke")

    completed = subprocess.run(
        [node, str(UX_SMOKE), "--dist", str(UI_DIST)],
        cwd=str(UI_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, f"UX polish smoke failed:\n{output}"
    assert "PASS: UX polish smoke completed" in completed.stdout
