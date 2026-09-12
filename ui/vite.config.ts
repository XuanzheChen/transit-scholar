import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Local development keeps Vite on its own port and proxies the frozen
 * `/api/v1/*` product API to the local FastAPI process, so the browser always
 * talks to exactly one origin.
 *
 * Production keeps the default single-origin model: `vite build` emits
 * `ui/dist/`, which `transit_scholar.api.app` serves as static assets from the
 * same local FastAPI server that answers `/api/v1/health`. No Node server is
 * required at runtime.
 *
 * The npm scripts pass `--configLoader native` because Vite's default config
 * bundling probes Windows network drive mappings by spawning `net use`, which
 * restricted/sandboxed Windows shells block (spawn EPERM). The native loader
 * loads this same config through Node's TypeScript support instead.
 */
const apiTarget = process.env.TRANSIT_SCHOLAR_API_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/api/v1': { target: apiTarget, changeOrigin: false },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api/v1': { target: apiTarget, changeOrigin: false },
    },
  },
})
