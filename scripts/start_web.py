"""One-command local launcher for the TransitScholar Web UI.

The launcher keeps the production architecture intact:
- React/Vite is built into ``ui/dist`` only when inputs changed.
- FastAPI serves both ``/api/v1/*`` and the built SPA from one origin.
- Python and Node dependencies are installed only when their lock/config
  fingerprints change.

This file intentionally depends only on the Python standard library so it can
bootstrap a fresh checkout before the project's virtualenv exists.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
VENV_DIR = ROOT / ".venv"
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_LOCK = UI_DIR / "package-lock.json"

PYTHON_STAMP = VENV_DIR / ".transit-scholar-pyproject.sha256"
NODE_STAMP = UI_DIR / "node_modules" / ".transit-scholar-package-lock.sha256"
BUILD_STAMP = UI_DIR / "dist" / ".transit-scholar-build.sha256"

MIN_PYTHON = (3, 11)


class LauncherError(RuntimeError):
    """Expected launcher failure with a user-facing message."""


def log(message: str) -> None:
    print(f"[TransitScholar] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_frontend_inputs() -> Iterable[Path]:
    fixed = [
        UI_DIR / "index.html",
        UI_DIR / "package.json",
        UI_DIR / "package-lock.json",
        UI_DIR / "vite.config.ts",
        UI_DIR / "tsconfig.json",
        UI_DIR / "tsconfig.app.json",
        UI_DIR / "tsconfig.node.json",
    ]
    for path in fixed:
        if path.is_file():
            yield path

    for directory in (UI_DIR / "src", UI_DIR / "public"):
        if directory.is_dir():
            yield from sorted(path for path in directory.rglob("*") if path.is_file())


def frontend_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_frontend_inputs(), key=lambda item: item.as_posix()):
        relative = path.relative_to(UI_DIR).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def read_stamp(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def write_stamp(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")


def run_checked(command: list[str], *, cwd: Path = ROOT) -> None:
    log("$ " + " ".join(command))
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise LauncherError(f"Command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise LauncherError(
            f"Command failed with exit code {exc.returncode}: {' '.join(command)}"
        ) from exc


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        version = ".".join(map(str, sys.version_info[:3]))
        raise LauncherError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"launcher is running under Python {version}."
        )


def ensure_virtualenv() -> Path:
    python = venv_python()
    if python.is_file():
        return python

    log("Creating .venv ...")
    run_checked([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not python.is_file():
        raise LauncherError(f"Virtualenv was created but Python is missing: {python}")
    return python


def python_environment_is_ready(python: Path) -> bool:
    try:
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import transit_scholar, fastapi, uvicorn; "
                "from transit_scholar.api.app import create_app",
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def ensure_python_dependencies(python: Path, *, skip_install: bool) -> None:
    if not PYPROJECT.is_file():
        raise LauncherError(f"Missing {PYPROJECT.relative_to(ROOT)}")

    expected = sha256_file(PYPROJECT)
    ready = (
        read_stamp(PYTHON_STAMP) == expected
        and python_environment_is_ready(python)
    )
    if ready:
        log("Python environment is up to date.")
        return

    if skip_install:
        raise LauncherError(
            "Python dependencies are not ready and --skip-install was requested."
        )

    log("Installing/updating Python dependencies ...")
    run_checked(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "-e",
            ".",
        ]
    )
    if not python_environment_is_ready(python):
        raise LauncherError("Python dependencies installed, but app imports still fail.")
    write_stamp(PYTHON_STAMP, expected)


def find_npm() -> str:
    npm = shutil.which("npm")
    if npm:
        return npm
    raise LauncherError(
        "npm was not found. Install Node.js (which includes npm) and run again."
    )


def ensure_node_dependencies(npm: str, *, skip_install: bool) -> None:
    if not PACKAGE_LOCK.is_file():
        raise LauncherError("ui/package-lock.json is missing.")

    expected = sha256_file(PACKAGE_LOCK)
    node_modules = UI_DIR / "node_modules"
    ready = node_modules.is_dir() and read_stamp(NODE_STAMP) == expected
    if ready:
        log("Node dependencies are up to date.")
        return

    if skip_install:
        raise LauncherError(
            "Node dependencies are not ready and --skip-install was requested."
        )

    log("Installing/updating UI dependencies with npm ci ...")
    run_checked([npm, "ci"], cwd=UI_DIR)
    write_stamp(NODE_STAMP, expected)


def ensure_frontend_build(npm: str, *, force_build: bool) -> None:
    expected = frontend_fingerprint()
    index = UI_DIR / "dist" / "index.html"
    ready = (
        not force_build
        and index.is_file()
        and read_stamp(BUILD_STAMP) == expected
    )
    if ready:
        log("Frontend build is up to date.")
        return

    log("Building Web UI ...")
    run_checked([npm, "run", "build"], cwd=UI_DIR)
    if not index.is_file():
        raise LauncherError("UI build completed without producing ui/dist/index.html.")
    write_stamp(BUILD_STAMP, expected)


def health_host(host: str) -> str:
    if host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return host


def wait_until_ready(
    process: subprocess.Popen[bytes],
    *,
    host: str,
    port: int,
    timeout_seconds: float,
) -> str:
    url = f"http://{health_host(host)}:{port}/api/v1/health"
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None

    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise LauncherError(
                f"Server exited before becoming ready (exit code {exit_code})."
            )

        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 300:
                    return f"http://{health_host(host)}:{port}/"
                last_error = f"health returned HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)

        time.sleep(0.4)

    detail = f" Last error: {last_error}" if last_error else ""
    raise LauncherError(
        f"Server did not become ready within {timeout_seconds:g} seconds.{detail}"
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def serve(
    python: Path,
    *,
    host: str,
    port: int,
    open_browser: bool,
    startup_timeout: float,
) -> int:
    command = [
        str(python),
        "-m",
        "uvicorn",
        "transit_scholar.api.app:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        str(port),
    ]
    log("$ " + " ".join(command))

    popen_kwargs: dict[str, object] = {"cwd": ROOT}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    try:
        app_url = wait_until_ready(
            process,
            host=host,
            port=port,
            timeout_seconds=startup_timeout,
        )
        log(f"Web UI ready: {app_url}")
        log("Press Ctrl+C to stop TransitScholar.")
        if open_browser:
            webbrowser.open(app_url, new=2)
        return process.wait()
    except KeyboardInterrupt:
        log("Stopping ...")
        stop_process(process)
        return 0
    except BaseException:
        stop_process(process)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap and launch the TransitScholar local Web UI."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Uvicorn bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Uvicorn port.")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the browser after the health check succeeds.",
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Rebuild the frontend even when its fingerprint is unchanged.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Never install Python or Node dependencies; fail if they are stale/missing.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for /api/v1/health (default: 60).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ensure_python_version()
        python = ensure_virtualenv()
        ensure_python_dependencies(python, skip_install=args.skip_install)
        npm = find_npm()
        ensure_node_dependencies(npm, skip_install=args.skip_install)
        ensure_frontend_build(npm, force_build=args.force_build)
        return serve(
            python,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            startup_timeout=args.startup_timeout,
        )
    except LauncherError as exc:
        log(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        log("Stopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
