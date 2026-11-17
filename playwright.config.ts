import { defineConfig } from '@playwright/test'

const host = '127.0.0.1'
const port = 4173

export default defineConfig({
  testDir: './e2e',
  outputDir: './node_modules/.playwright-test-results',
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://${host}:${port}`,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node e2e/start-workbench-system.mjs',
    url: `http://${host}:${port}/api/campus-pulse/v1/health`,
    reuseExistingServer: false,
    timeout: 600_000,
    gracefulShutdown: {
      signal: 'SIGTERM',
      timeout: 5_000,
    },
  },
})
