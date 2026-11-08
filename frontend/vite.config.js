import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendPort = process.env.STOCKS_BACKEND_PORT || '6000'

// https://vite.dev/config/
export default defineConfig({
  // Served under /stocks/ by the Arthur.OS gateway. Without this, index.html
  // points at root-absolute assets (/src/main.jsx, /@vite/client), which carry
  // no hint of which app they belong to — the gateway then has to guess from
  // Referer or the global __gw_app cookie, and when that guess misses it hands
  // back the portal's index.html as text/html with a 200. The browser rejects
  // an HTML module script and StockRadar renders blank, with nothing in any log.
  // Setting base makes every asset and the HMR websocket path-routable instead.
  // Direct :4000 access redirects to /stocks/ and keeps working.
  base: '/stocks/',
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'wouter'],
          'vendor-recharts': ['recharts'],
          'vendor-lwcharts': ['lightweight-charts'],
          'vendor-markdown': ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    strictPort: true,
    // Quick tunnels get a new random hostname on every cloudflared restart, so
    // pinning one hostname here goes stale and Vite starts 403-ing. Allow the
    // whole trycloudflare domain instead (leading dot = any subdomain).
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
