import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendPort = process.env.STOCKS_BACKEND_PORT || '2000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    strictPort: true,
    allowedHosts: ['exchanges-isbn-shirt-val.trycloudflare.com'],
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
