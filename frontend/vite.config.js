import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    allowedHosts: ['lesson-kansas-prescription-meter.trycloudflare.com'],
    proxy: {
      '/api': {
        target: 'http://localhost:2000',
        changeOrigin: true,
      },
    },
  },
})
