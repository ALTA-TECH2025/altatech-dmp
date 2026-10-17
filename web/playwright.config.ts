// 瀏覽器端到端（對真後端）：E2E_BASE 指向 API（同源提供前端），E2E_ACCOUNTS 為本機帳號檔（含 TOTP 秘密）、E2E_PASSWORD 為 seed 密碼。
// 本機：bash deploy/quickstart/run_local.sh up && seed；python3 -m backend.tools.webhook_receiver 9099 crm-local-secret；python3 -m backend.tools.demo_fill；npx playwright test
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE || 'http://127.0.0.1:8000',
    headless: true,
    viewport: { width: 1360, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // 本機用系統 Chromium（新版只剩 new headless）：executablePath ＋ --headless=new；CI 用 playwright install 的瀏覽器，不設此變數
    ...(process.env.PLAYWRIGHT_CHROMIUM ? { headless: false, launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM, args: ['--headless=new', '--no-sandbox'] } } : {}),
  },
});
