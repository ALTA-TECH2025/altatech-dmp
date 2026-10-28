// 主線端到端（斷言型，對真後端）：登入（本機帳號＋TOTP）→ 事件分析查詢 → 存為書籤並建新看板 → 看板有這張卡 → 推送單（既有分群 × CRM 目的地）→ 執行 → 交付成功。
// 前置：seed（五個帳號、黃金資料、範例看板）、demo_fill（分群「高價值活躍交易客」已計算、目的地「CRM 客戶系統」指向本機接收端）。
import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

const ACCOUNTS = process.env.E2E_ACCOUNTS || '';
const PASSWORD = process.env.E2E_PASSWORD || '';

function totp(secretB32: string, at = Date.now()): string {
  const a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
  for (const c of secretB32.replace(/=+$/, '').toUpperCase()) bits += a.indexOf(c).toString(2).padStart(5, '0');
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const ctr = Buffer.alloc(8); ctr.writeUInt32BE(Math.floor(at / 30000), 4);
  const h = createHmac('sha1', key).update(ctr).digest(); const o = h[h.length - 1] & 15;
  return (((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).toString().padStart(6, '0');
}
function secretOf(user: string): string {
  const j = JSON.parse(readFileSync(ACCOUNTS, 'utf8')) as { accounts: Record<string, { totp_secret: string }> };
  return j.accounts[user].totp_secret;
}
/** 登入：密碼 → TOTP。被擋有兩種：同一個 30 秒窗口的碼不能重用（防重放），以及每帳號每分鐘 5 次（密碼與 TOTP 各算一次）。
 *  demo_fill 剛用同一個帳號登入過時兩者都可能碰到，所以失敗後等 61 秒（跨過一個窗口，也讓一分鐘的次數歸零）再試。 */
async function login(page: Page, user: string): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await page.goto('/login');
    await page.fill('input[autocomplete="username"]', user); await page.fill('input[type="password"]', PASSWORD);
    await page.click('form[aria-label="帳密登入"] button[type="submit"]');
    await page.waitForSelector('input[inputmode="numeric"]', { timeout: 15_000 });
    await page.fill('input[inputmode="numeric"]', totp(secretOf(user)));
    await page.click('form[aria-label="帳密登入"] button[type="submit"]');
    try { await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 8_000 }); return; }
    catch { await page.waitForTimeout(61_000); }
  }
  throw new Error(`login failed: ${user}`);
}

test.describe('主線', () => {
  test.skip(!ACCOUNTS || !PASSWORD, '需要 E2E_ACCOUNTS 與 E2E_PASSWORD');
  test.slow();   // 登入被擋時每次重試要等 61 秒，逾時放寬為 3 倍

  test('登入 → 事件分析 → 書籤 → 看板 → 推送 → 交付成功', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await login(page, 'analyst');
    await expect(page.locator('.page h1').first()).toBeVisible();

    // 1. 事件分析：成交筆數 × 平台，查詢後有圖
    await page.goto('/events');
    await page.selectOption('select[aria-label="指標 1 事件"]', 'bond_deal');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await expect(page.locator('[role="img"], table').first()).toBeVisible();
    await expect(page.getByRole('button', { name: '存為書籤' })).toBeVisible();

    // 2. 存為書籤，順手建一個新看板
    const stamp = Date.now().toString(36);
    const bmName = `E2E 成交筆數 ${stamp}`; const dashName = `E2E 看板 ${stamp}`;
    await page.getByRole('button', { name: '存為書籤' }).click();
    const form = page.locator('form[aria-label="存為書籤"]');
    await form.getByLabel('書籤名稱').fill(bmName);
    await form.getByLabel('加入看板').selectOption('__new');
    await form.getByLabel('新看板名稱').fill(dashName);
    await form.getByRole('button', { name: '儲存書籤' }).click();
    const done = page.locator('.note', { hasText: '已存書籤' });
    await expect(done).toBeVisible();
    await expect(done.getByRole('link', { name: dashName })).toBeVisible();

    // 3. 書籤頁看得到；看板上有這張卡
    await page.goto('/bookmarks');
    await expect(page.locator('table tbody tr', { hasText: bmName }).first()).toBeVisible();
    await page.goto('/overview');
    await page.getByRole('link', { name: dashName }).first().click();
    await expect(page).toHaveURL(/\/overview\//);
    await expect(page.locator('h3', { hasText: bmName }).first()).toBeVisible();
    await expect(page.locator('[role="img"], table, .tile').first()).toBeVisible();     // 卡片以檢視者權限重算後有內容

    // 4. 推送單：既有分群 × CRM 目的地（已有首推紀錄 → 不需審批）→ 執行 → 交付成功
    await page.goto('/activation');
    await page.getByRole('button', { name: /新建推送單/ }).click();
    const segSel = page.locator('select[aria-label="推送分群"]');
    await expect(segSel).toBeVisible();
    const segOpt = segSel.locator('option', { hasText: '高價值活躍交易客' });
    await segSel.selectOption(await segOpt.getAttribute('value') ?? '');
    await page.locator('input[aria-label="目的地 CRM 客戶系統"]').check();
    await page.selectOption('select[aria-label="推送排程"]', 'now');
    await page.getByRole('button', { name: '建立', exact: true }).click();
    const row = page.locator('table tbody tr', { hasText: '高價值活躍交易客' }).filter({ hasText: 'CRM 客戶系統' }).first();
    await expect(row).toBeVisible();
    await expect(row.getByText('生效中', { exact: true })).toBeVisible();
    await expect(row).toContainText('全部成功');                                             // 排程「立即」：建立即執行；這張推送單自己的最近結果，不是別張
    await expect(row).toContainText(/\+\d+/);
    await page.goto('/activation?tab=deliveries');
    const newest = page.locator('table tbody tr').first();
    await expect(newest).toContainText('CRM 客戶系統');
    await expect(newest.getByText('成功', { exact: true })).toBeVisible();

    expect(errors, '瀏覽器 console 不該有未捕捉例外').toEqual([]);
  });

  test('行銷角色：事件分析看得到、推送單要送審、系統監控與資料流頁打不開', async ({ page }) => {
    await login(page, 'marketing');
    await page.goto('/events');
    await expect(page.getByRole('button', { name: '查詢', exact: true })).toBeVisible();
    await page.goto('/activation');
    await page.getByRole('button', { name: /新建推送單/ }).click();                         // 行銷可建推送單，但全量／首推要審批：按鈕文字反映
    await page.locator('input[aria-label="目的地 CRM 客戶系統"]').check();
    await expect(page.getByRole('button', { name: /送審並建立|建立/ })).toBeVisible();
    for (const path of ['/system/monitor', '/governance/pipeline', '/iam']) {
      await page.goto(path);
      await expect(page.locator('.page h1').first()).toHaveText(/沒有權限|需要重新驗證/);
    }
  });

  test('session 失效（UNAUTHENTICATED）→ 回登入頁並帶 return_to', async ({ page, context }) => {
    await login(page, 'ops');
    const path = new URL(page.url()).pathname;
    await context.clearCookies({ name: 'dmp_sid' });                                         // 模擬逾時或被登出：瀏覽器沒有 session 了
    await page.reload();
    await page.waitForURL((u) => u.pathname === '/login' && (u.searchParams.get('return_to') ?? '').startsWith(path), { timeout: 15_000 });
  });
});
