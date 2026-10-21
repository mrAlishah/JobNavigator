import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
// Releases are named major.minor ("3.0"); package.json keeps full semver for npm.
const appVersion = pkg.version.split('.').slice(0, 2).join('.')

export default defineConfig({
  base: process.env.VITE_DEMO === '1' ? '/JobNavigator-demo/' : '/',
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __DEMO__: JSON.stringify(process.env.VITE_DEMO === '1'),
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    }
  }
})
