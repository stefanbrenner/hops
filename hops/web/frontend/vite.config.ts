import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run build` writes into hops/web/static, which the Python server serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../static', emptyOutDir: true },
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8737', ws: true } } },
})
