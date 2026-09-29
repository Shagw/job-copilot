import { defineConfig, devices } from '@playwright/test'

const PORT = 8790

// Real browser + real backend + real Groq. Slow on purpose (agents take 5-30s each).
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e-artifacts/results',
  timeout: 5 * 60_000,
  expect: { timeout: 15_000 },
  workers: 1, // shared Groq quota; run serially
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  // channel 'chromium' = full Chromium in new headless mode; the default headless shell has no PDF viewer.
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: 'chromium' } }],
  webServer: {
    command: 'sh e2e/serve.sh',
    env: { E2E_PORT: String(PORT) },
    url: `http://localhost:${PORT}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
})
