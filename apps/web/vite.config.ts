import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * Local `yarn dev` / `pnpm dev`: proxy API calls to the Docker-published API
 * (`localhost:8080`). In compose the web service overrides this with
 * `VITE_API_PROXY=http://api:8080`.
 */
function apiProxyTarget(mode: string): string {
  const env = loadEnv(mode, process.cwd(), '')
  return (
    env.VITE_API_PROXY ||
    process.env.VITE_API_PROXY ||
    'http://localhost:8080'
  )
}

function proxyToApi(target: string) {
  return {
    target,
    changeOrigin: true,
    // Long-lived SSE (/api/.../stream, /api/call_attempts/stream).
    timeout: 0,
    proxyTimeout: 0,
  }
}

export default defineConfig(({ mode }) => {
  const api = apiProxyTarget(mode)
  const toApi = proxyToApi(api)

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: true,
      port: 5173,
      proxy: {
        '/api': toApi,
        '/rpc': toApi,
        '/dev': toApi,
        '/healthz': toApi,
        '/webhooks': toApi,
      },
    },
  }
})
