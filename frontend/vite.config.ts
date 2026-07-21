import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8002',
      '/v1': 'http://localhost:8002',
      '/ws': { target: 'ws://localhost:8002', ws: true },
    },
  },
})
