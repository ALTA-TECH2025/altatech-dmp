// 控制項逐一驗證（對真後端）：畫面上每個選項都要「送得出去、後端接受、結果照選項變化、畫面照結果畫」。
// 選項清單從畫面讀，不寫死：畫面多一個選項，這裡就多測一個；後端不支援就紅燈（畫面有、後端沒有）。
// 資料：seed 的黃金資料集（project golden，2026-09-20～10-16）。畫面不送 project（後端預設 altatech_dmp），
// 所以在瀏覽器送出前把 project 換成 golden；其餘條件照畫面送的原樣，斷言也只看畫面送出的條件。
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { ACCOUNTS, PASSWORD, login } from './helpers';

type Row = Record<string, unknown>;
type Run = { req: Record<string, any>; status: number; body: { rows: Row[]; columns: string[]; detail?: string } };

let ctx: BrowserContext; let page: Page;

async function run(p: Page): Promise<Run> {
  const [resp] = await Promise.all([
    p.waitForResponse((r) => new URL(r.url()).pathname === '/v1/analytics/events' && r.request().method() === 'POST', { timeout: 60_000 }),
    p.getByRole('button', { name: '查詢', exact: true }).click(),
  ]);
  return { req: resp.request().postDataJSON(), status: resp.status(), body: await resp.json() };
}
async function setRange(p: Page, from: string, to: string): Promise<void> {
  await p.getByRole('button', { name: '日期區間', exact: true }).click();
  await p.getByLabel('日期區間開始').fill(from);
  await p.getByLabel('日期區間結束').fill(to);
  await p.getByRole('button', { name: '確定', exact: true }).click();
}
const grainSelect = (p: Page) => p.locator('.mantine-InputWrapper-root', { hasText: '粒度' }).locator('select');
const optionsOf = (p: Page, sel: ReturnType<Page['locator']>) => sel.evaluate((el: HTMLSelectElement) => [...el.options].map((o) => o.value));
const day = (s: string) => new Date(`${s.slice(0, 10)}T00:00:00Z`);

/** 每種粒度的 bucket 應長怎樣（與編譯器 bucket_sql 一致：週一起、月初、台北時間）。 */
function checkBuckets(grain: string, rows: Row[]): void {
  if (grain === 'total') { expect(rows.every((r) => !('bucket' in r) || r.bucket == null), '合計不應有時間桶').toBe(true); return; }
  const bs = rows.map((r) => String(r.bucket));
  expect(bs.length, `${grain} 應有時間桶`).toBeGreaterThan(0);
  for (const b of bs) {
    if (grain === 'minute') expect(b, b).toMatch(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:00)?/);
    if (grain === 'hour') expect(b, b).toMatch(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:00(:00)?/);
    if (grain === 'day') expect(b, b).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    if (grain === 'week') expect(day(b).getUTCDay(), `${b} 應是週一`).toBe(1);
    if (grain === 'month') expect(b.slice(8, 10), `${b} 應是月初`).toBe('01');
  }
  expect([...bs].sort(), '時間桶要排序').toEqual(bs);
}

test.describe.serial('控制項：事件分析與留存', () => {
  test.skip(!ACCOUNTS || !PASSWORD, '需要 E2E_ACCOUNTS 與 E2E_PASSWORD');
  test.slow();

  test.beforeAll(async ({ browser }, info) => {
    ctx = await browser.newContext({ baseURL: info.project.use.baseURL, viewport: { width: 1360, height: 900 } });
    page = await ctx.newPage();
    await login(page, 'analyst');
    await page.route('**/v1/analytics/**', async (route) => {
      const req = route.request();
      if (req.method() !== 'POST' || !req.postData()) return route.continue();
      await route.continue({ postData: JSON.stringify({ ...JSON.parse(req.postData() as string), project: 'golden' }) });
    });
    await page.goto('/events');
    await page.selectOption('select[aria-label="指標 1 事件"]', 'bond_order');
  });
  test.afterAll(async () => { await ctx?.close(); });

  test('每個粒度：後端接受、時間桶照粒度、畫面照結果畫', async () => {
    const grains = await optionsOf(page, grainSelect(page));
    expect(grains.length).toBeGreaterThan(1);
    for (const g of grains) {
      const short = g === 'minute' || g === 'hour';
      await setRange(page, short ? '2026-10-16' : '2026-10-01', '2026-10-16');
      await grainSelect(page).selectOption(g);
      const r = await run(page);
      expect(r.status, `粒度 ${g}：${r.body.detail ?? ''}`).toBe(200);
      expect(r.req.time.grain).toBe(g);
      checkBuckets(g, r.body.rows);
      if (g === 'day') expect(new Set(r.body.rows.map((x) => x.bucket)).size, '10/1～10/16 按日應有 16 個桶').toBe(16);
      if (g === 'week') expect([...new Set(r.body.rows.map((x) => x.bucket))]).toEqual(['2026-09-28', '2026-10-05', '2026-10-12']);
      if (g === 'month') expect([...new Set(r.body.rows.map((x) => x.bucket))]).toEqual(['2026-10-01']);
      if (g === 'total') expect(r.body.rows).toHaveLength(1);
      await expect(page.getByText(`${r.body.rows.length} 列`, { exact: false }).first()).toBeVisible();   // 畫面上的列數 = 後端回的列數
    }
  });

  test('對比上一期：送出 compare，結果多一組上期欄位', async () => {
    await setRange(page, '2026-10-09', '2026-10-16');
    await grainSelect(page).selectOption('day');
    await page.locator('.mantine-InputWrapper-root', { hasText: '添加對比' }).locator('select').selectOption('previous_period');
    const r = await run(page);
    expect(r.status, r.body.detail).toBe(200);
    expect(r.req.compare).toBe('previous_period');
    expect(r.body.columns.some((c) => c.endsWith('_prev')), `欄位：${r.body.columns.join(',')}`).toBe(true);
    await page.locator('.mantine-InputWrapper-root', { hasText: '添加對比' }).locator('select').selectOption('');
  });

  test('分組：送出 group_by，結果每列帶分組值，各組加總 = 不分組', async () => {
    await setRange(page, '2026-10-01', '2026-10-16');
    await grainSelect(page).selectOption('total');
    const whole = await run(page);
    const alias = whole.body.columns.find((c) => !['bucket', '_k', 'suppressed'].includes(c) && !/^g\d$/.test(c)) as string;
    await page.getByLabel('分組（最多 3）').selectOption(['platform']);
    const r = await run(page);
    expect(r.status, r.body.detail).toBe(200);
    expect(r.req.group_by).toEqual(['platform']);
    expect(r.body.rows.length).toBeGreaterThan(1);
    expect(r.body.rows.every((x) => 'g1' in x)).toBe(true);
    const sum = r.body.rows.reduce((n, x) => n + Number(x[alias] ?? 0), 0);
    expect(sum, '各平台加總應等於不分組總數').toBe(Number(whole.body.rows[0][alias]));
    await page.getByLabel('分組（最多 3）').selectOption([]);
  });

  test('且／或：兩個互斥條件，且 = 0、或 = iOS ＋ Android', async () => {
    await setRange(page, '2026-10-01', '2026-10-16');
    await grainSelect(page).selectOption('total');
    const rb = page.locator('.rb').first();
    const pick = async (i: number, v: string) => {
      await page.selectOption(`select[aria-label="條件 ${i} 欄位"]`, 'platform');
      await page.selectOption(`select[aria-label="條件 ${i} 運算子"]`, 'eq');
      const val = page.locator(`[aria-label="條件 ${i} 值"]`);
      if (await val.evaluate((el) => el.tagName === 'SELECT')) await val.selectOption(v); else await val.fill(v);
    };
    if (!(await page.locator('select[aria-label="條件 1 欄位"]').count())) await rb.getByRole('button', { name: '＋ 條件', exact: true }).click();
    await pick(1, 'iOS');
    const ios = await run(page);
    await rb.getByRole('button', { name: '＋ 條件', exact: true }).click();
    await pick(2, 'Android');
    const both = await run(page);
    expect(both.status, both.body.detail).toBe(200);
    expect(both.req.filters.op).toBe('and');
    await page.getByRole('button', { name: '條件 組合' }).click();      // 且 → 或
    const either = await run(page);
    expect(either.status, either.body.detail).toBe(200);
    expect(either.req.filters.op).toBe('or');
    const alias = ios.body.columns.find((c) => !['bucket', '_k', 'suppressed'].includes(c) && !/^g\d$/.test(c)) as string;
    const val = (x: Run) => Number(x.body.rows[0]?.[alias] ?? 0);
    expect(val(ios)).toBeGreaterThan(0);
    expect(val(both), '平台同時是 iOS 且 Android：應為 0').toBe(0);
    await page.getByRole('button', { name: '刪除條件 1' }).click();     // 只剩 Android
    const android = await run(page);
    expect(android.req.filters.items).toHaveLength(1);
    expect(val(android)).toBeGreaterThan(0);
    expect(val(either), '或：平台互斥，應等於 iOS ＋ Android').toBe(val(ios) + val(android));
    await page.getByRole('button', { name: '刪除條件 1' }).click();
  });

  test('留存：每個粒度 × 每個模型，後端接受、同期群照粒度對齊', async () => {
    await page.goto('/retention');
    await page.selectOption('select[aria-label="初始行為"]', 'bond_order');
    await setRange(page, '2026-09-21', '2026-10-16');
    const sel = (label: string) => page.locator('.mantine-InputWrapper-root', { hasText: label }).locator('select').first();
    const units = await optionsOf(page, sel('粒度')); const models = await optionsOf(page, sel('模型'));
    expect(units.length).toBeGreaterThan(1); expect(models.length).toBeGreaterThan(1);
    for (const u of units) for (const m of models) {
      await sel('粒度').selectOption(u); await sel('模型').selectOption(m);
      const [resp] = await Promise.all([
        page.waitForResponse((r) => new URL(r.url()).pathname === '/v1/analytics/retention' && r.request().method() === 'POST', { timeout: 60_000 }),
        page.getByRole('button', { name: '查詢', exact: true }).click(),
      ]);
      const req = resp.request().postDataJSON(); const body = await resp.json() as { rows: Row[]; detail?: string };
      expect(resp.status(), `留存 ${u}／${m}：${body.detail ?? ''}`).toBe(200);
      expect([req.unit, req.model]).toEqual([u, m]);
      expect(body.rows.length, `留存 ${u}／${m} 應有同期群`).toBeGreaterThan(0);
      for (const r of body.rows) {
        const c = String(r.cohort);
        if (u === 'week') expect(day(c).getUTCDay(), `${c} 應是週一`).toBe(1);
        if (u === 'month') expect(c.slice(8, 10), `${c} 應是月初`).toBe('01');
        expect(Number(r.retained), '留存人數不超過同期群人數').toBeLessThanOrEqual(Number(r.users));
      }
    }
  });
});
