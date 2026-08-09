import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendPort = process.env.STOCKS_BACKEND_PORT || '6000'

// https://vite.dev/config/
export default defineConfig({
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
