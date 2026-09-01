import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The API host is configurable so the dashboard can run against a backend on a
// non-default port (two dev stacks side by side) or a deployed one, without
// editing this file.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
