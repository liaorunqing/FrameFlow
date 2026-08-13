import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/uploads': 'http://127.0.0.1:8001',
      '/benchmark-artifacts': 'http://127.0.0.1:8001',
    },
  },
})
