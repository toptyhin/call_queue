import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const apiProxy = process.env.VITE_API_PROXY || 'http://localhost:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: apiProxy, changeOrigin: true },
      '/rpc': { target: apiProxy, changeOrigin: true },
      '/dev': { target: apiProxy, changeOrigin: true },
      '/healthz': { target: apiProxy, changeOrigin: true },
      '/webhooks': { target: apiProxy, changeOrigin: true },
    },
  },
})
