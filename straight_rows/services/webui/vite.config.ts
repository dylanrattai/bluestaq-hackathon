import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The evaluator API (FastAPI, services/evaluator) is proxied so the UI calls it on the same origin, which means
// the evaluator needs no CORS changes. In compose, API_TARGET=http://evaluator:8000. If the API is unreachable,
// the UI falls back to DEMO.
const target = process.env.API_TARGET ?? 'http://localhost:8000'
const api = ['/status', '/alarms', '/watchdog', '/feed', '/stream', '/health', '/submission', '/webui']

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ['.ts.net'],          // Tailscale MagicDNS names (IPs are always allowed)
    proxy: Object.fromEntries(api.map((p) => [p, { target, changeOrigin: true }])),
  },
})
