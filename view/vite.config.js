import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy keeps the graph API on :8090 without touching CORS.
// Same-origin in dev; Fly routing can keep the same shape later.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/graph': 'http://127.0.0.1:8090',
      '/health': 'http://127.0.0.1:8090',
    },
  },
})
