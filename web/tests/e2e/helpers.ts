// 端到端共用：本機帳號登入（密碼＋TOTP）與環境變數。
import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';

export const ACCOUNTS = process.env.E2E_ACCOUNTS || '';
export const PASSWORD = process.env.E2E_PASSWORD || '';

export function totp(secretB32: string, at = Date.now()): string {
  const a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
  for (const c of secretB32.replace(/=+$/, '').toUpperCase()) bits += a.indexOf(c).toString(2).padStart(5, '0');
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const ctr = Buffer.alloc(8); ctr.writeUInt32BE(Math.floor(at / 30000), 4);
  const h = createHmac('sha1', key).update(ctr).digest(); const o = h[h.length - 1] & 15;
  return (((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).toString().padStart(6, '0');
}
export function secretOf(user: string): string {
  const j = JSON.parse(readFileSync(ACCOUNTS, 'utf8')) as { accounts: Record<string, { totp_secret: string }> };
  return j.accounts[user].totp_secret;
}
/** 登入：密碼 → TOTP。被擋有兩種：同一個 30 秒窗口的碼不能重用（防重放），以及每帳號每分鐘 5 次（密碼與 TOTP 各算一次）。
 *  demo_fill 剛用同一個帳號登入過時兩者都可能碰到，所以失敗後等 61 秒（跨過一個窗口，也讓一分鐘的次數歸零）再試。 */
export async function login(page: Page, user: string): Promise<void> {
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
