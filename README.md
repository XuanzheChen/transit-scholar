# TransitScholar

Local-first research workspace for transit-control literature.

## One-command Web UI

Requirements:

- Python 3.11 or newer
- Node.js with `npm`

From the repository root:

**Windows (recommended):**

```powershell
.\start-web.cmd
```

You can also use PowerShell directly:

```powershell
.\start-web.ps1
```

**Linux / macOS:**

```bash
./start-web.sh
```

If the executable bit was lost while copying the checkout, use:

```bash
sh start-web.sh
```

The launcher will:

1. create `.venv` when needed;
2. install/update the editable Python package only when `pyproject.toml` changes or imports are broken;
3. run `npm ci` only when `ui/package-lock.json` changes or `node_modules` is missing;
4. rebuild `ui/dist` only when frontend inputs change;
5. start the FastAPI application on `http://127.0.0.1:8000`;
6. wait for `/api/v1/health`, then open the Web UI in your browser.

FastAPI remains the only production server: it serves both `/api/v1/*` and the built React SPA from the same origin.

Useful options:

```text
--port 8001
--host 0.0.0.0
--no-open
--force-build
--skip-install
--startup-timeout 120
```

For frontend development, architecture details, smoke tests, and the two-terminal Vite workflow, see [`ui/README.md`](ui/README.md).
