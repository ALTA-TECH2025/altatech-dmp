// 控制項逐一驗證（對真後端）第二批：漏斗、路徑、歸因。原則同 controls.spec.ts：選項從畫面讀、後端要接受、結果要照選項變化。
// 資料：seed 的黃金資料集（project golden）；送出前把 project 換成 golden，斷言只看畫面送出的條件。
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { ACCOUNTS, PASSWORD, login } from './helpers';

type Row = Record<string, unknown>;
let ctx: BrowserContext; let page: Page;

async function query(p: Page, path: string): Promise<{ req: Record<string, any>; status: number; body: { rows: Row[]; detail?: string } & Record<string, any> }> {
  const [resp] = await Promise.all([
    p.waitForResponse((r) => new URL(r.url()).pathname === path && r.request().method() === 'POST', { timeout: 60_000 }),
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
const labeled = (p: Page, label: string) => p.locator('.mantine-InputWrapper-root', { hasText: label }).locator('select').first();
const optionsOf = (sel: ReturnType<Page['locator']>) => sel.evaluate((el: HTMLSelectElement) => [...el.options].map((o) => o.value));
const day = (s: string) => new Date(`${s.slice(0, 10)}T00:00:00Z`);
const num = (v: unknown) => Number(v ?? 0);

test.describe.serial('控制項：漏斗、路徑、歸因', () => {
  test.skip(!ACCOUNTS || !PASSWORD, '需要 E2E_ACCOUNTS 與 E2E_PASSWORD');
  test.slow();

  test.beforeAll(async ({ browser }, info) => {
    ctx = await browser.newContext({ baseURL: info.project.use.baseURL, viewport: { width: 1360, height: 900 } });
    page = await ctx.newPage();
    await page.route('**/v1/analytics/**', async (route) => {
      const req = route.request();
      if (req.method() !== 'POST' || !req.postData()) return route.continue();
      await route.continue({ postData: JSON.stringify({ ...JSON.parse(req.postData() as string), project: 'golden' }) });
    });
    await login(page, 'analyst');
  });
  test.afterAll(async () => { await ctx?.close(); });

  test('漏斗：每個轉換窗口、順序、趨勢、分組都能查，結果合理', async () => {
    await page.goto('/funnel');
    await page.selectOption('select[aria-label="第 1 步事件"]', 'bond_order');
    await page.selectOption('select[aria-label="第 2 步事件"]', 'bond_deal');
    await setRange(page, '2026-09-21', '2026-10-16');
    const last = (rows: Row[]) => num(rows.filter((r) => num(r.step) === 2).reduce((n, r) => n + num(r.users), 0));
    const first = (rows: Row[]) => num(rows.filter((r) => num(r.step) === 1).reduce((n, r) => n + num(r.users), 0));

    // 轉換窗口：選項由短到長，完成最後一步的人只會變多或不變
    let prev = -1;
    for (const w of (await optionsOf(labeled(page, '轉換窗口'))).filter((x) => x !== 'custom')) {
      await labeled(page, '轉換窗口').selectOption(w);
      const r = await query(page, '/v1/analytics/funnel');
      expect(r.status, `窗口 ${w}：${r.body.detail ?? ''}`).toBe(200);
      expect(`${r.req.window.value}:${r.req.window.unit}`).toBe(w);
      expect(last(r.body.rows), '第 2 步人數不超過第 1 步').toBeLessThanOrEqual(first(r.body.rows));
      expect(last(r.body.rows), `窗口 ${w} 不應比更短的窗口少`).toBeGreaterThanOrEqual(prev);
      prev = last(r.body.rows);
    }
    expect(prev, '最長預設窗口（365 天）應有結果').toBeGreaterThan(0);
    // 自訂窗口：45 天能查；366 天在畫面上就擋下（查詢按鈕不能按）
    await labeled(page, '轉換窗口').selectOption('custom');
    await page.getByLabel('窗口數值').fill('45'); await page.getByLabel('窗口單位').selectOption('day');
    const cw = await query(page, '/v1/analytics/funnel');
    expect(cw.status, cw.body.detail).toBe(200);
    expect(cw.req.window).toEqual({ value: 45, unit: 'day' });
    await page.getByLabel('窗口數值').fill('366');
    await expect(page.getByRole('button', { name: '查詢', exact: true })).toBeDisabled();
    await expect(page.getByText('最長 365 天')).toBeVisible();
    await labeled(page, '轉換窗口').selectOption('7:day');

    // 步數上限（目錄 max_steps = 32）：一直加到「＋ 步驟」消失，正好 32 步
    for (let i = 0; i < 40 && await page.getByRole('button', { name: '＋ 步驟' }).isVisible(); i++) await page.getByRole('button', { name: '＋ 步驟' }).click();
    await expect(page.locator('select[aria-label^="第 "][aria-label$=" 步事件"]')).toHaveCount(32);
    for (let i = 32; i > 2; i--) await page.getByRole('button', { name: `刪除第 ${i} 步` }).click();
    await expect(page.locator('select[aria-label^="第 "][aria-label$=" 步事件"]')).toHaveCount(2);

    // 順序：嚴格不會比寬鬆多
    const res: Record<string, number> = {};
    for (const o of await optionsOf(labeled(page, '順序'))) {
      await labeled(page, '順序').selectOption(o);
      const r = await query(page, '/v1/analytics/funnel');
      expect(r.status, `順序 ${o}：${r.body.detail ?? ''}`).toBe(200);
      expect(r.req.order).toBe(o);
      res[o] = last(r.body.rows);
    }
    expect(res.strict, '嚴格 ≤ 寬鬆').toBeLessThanOrEqual(res.loose);
    await labeled(page, '順序').selectOption('loose');

    // 趨勢：每個選項都能查；按日／週／月的桶對齊，各桶第 1 步加總 = 合計
    const total = first((await query(page, '/v1/analytics/funnel')).body.rows);
    for (const t of await optionsOf(labeled(page, '趨勢'))) {
      await labeled(page, '趨勢').selectOption(t);
      const r = await query(page, '/v1/analytics/funnel');
      expect(r.status, `趨勢 ${t || '合計'}：${r.body.detail ?? ''}`).toBe(200);
      expect(r.req.trend ?? '').toBe(t);
      if (!t) continue;
      for (const row of r.body.rows) {
        const b = String(row.bucket);
        if (t === 'week') expect(day(b).getUTCDay(), `${b} 應是週一`).toBe(1);
        if (t === 'month') expect(b.slice(8, 10), `${b} 應是月初`).toBe('01');
      }
      // 每人只歸入第一次做第 1 步的那一桶：沒有抑制時各桶加總 = 合計；有桶因 K 匿名被抑制（users = null）時加總只會更少
      if (r.body.rows.some((x) => x.suppressed)) expect(first(r.body.rows), `趨勢 ${t}：有抑制時加總 ≤ 合計`).toBeLessThanOrEqual(total);
      else expect(first(r.body.rows), `趨勢 ${t}：各桶第 1 步加總 = 合計`).toBe(total);
    }
    await labeled(page, '趨勢').selectOption('');

    // 分組：第一個維度，各組第 1 步加總 = 合計（每人只歸一組）
    const dims = (await optionsOf(labeled(page, '分組'))).filter(Boolean);
    expect(dims.length).toBeGreaterThan(0);
    await labeled(page, '分組').selectOption('platform');
    const g = await query(page, '/v1/analytics/funnel');
    expect(g.status, g.body.detail).toBe(200);
    expect(g.req.group_by).toEqual(['platform']);
    if (!g.body.rows.some((r) => r.suppressed)) expect(first(g.body.rows), '各組第 1 步加總 = 合計').toBe(total);
    await labeled(page, '分組').selectOption('');

    // 關聯屬性 × 口徑：每個關聯屬性都能查；有關聯屬性時「流程數」也能查
    for (const rel of (await optionsOf(labeled(page, '關聯屬性'))).filter(Boolean)) {
      await labeled(page, '關聯屬性').selectOption(rel);
      for (const m of await optionsOf(labeled(page, '口徑'))) {
        await labeled(page, '口徑').selectOption(m);
        const r = await query(page, '/v1/analytics/funnel');
        expect(r.status, `關聯 ${rel}／口徑 ${m}：${r.body.detail ?? ''}`).toBe(200);
        expect([r.req.relation, r.req.metric]).toEqual([rel, m]);
      }
    }
    await labeled(page, '關聯屬性').selectOption('');

    // 限制後續事件在時間區間內（同神策）：期間 10-05～10-09（之後還有資料）、窗口 3 天；勾選後第 1 步一樣、第 2 步只會變少
    await setRange(page, '2026-10-05', '2026-10-09'); await labeled(page, '轉換窗口').selectOption('3:day');
    const box = page.getByLabel('限制後續事件在時間區間內');
    await expect(box).not.toBeChecked();
    const open = await query(page, '/v1/analytics/funnel');
    expect(open.status, open.body.detail).toBe(200);
    expect(open.req.restrict_to_range, '預設不帶（後續步驟可落在期間後）').toBeUndefined();
    await box.check();
    const lim = await query(page, '/v1/analytics/funnel');
    expect(lim.status, lim.body.detail).toBe(200);
    expect(lim.req.restrict_to_range).toBe(true);
    expect(first(lim.body.rows), '第 1 步人數不受限制影響').toBe(first(open.body.rows));
    expect(last(lim.body.rows), '勾選限制後第 2 步 ≤ 不勾').toBeLessThanOrEqual(last(open.body.rows));
    await box.uncheck();
  });

  test('路徑：每個方向 × 會話切割 × 抽樣都能查，有節點', async () => {
    await page.goto('/path');
    await page.selectOption('select[aria-label="起點事件"]', 'app_login_success');
    await setRange(page, '2026-10-01', '2026-10-16');
    const dir = page.locator('select[aria-label="方向"]'); const sess = page.locator('select[aria-label="會話切割"]'); const smp = page.locator('select[aria-label="抽樣"]');
    await smp.selectOption('null');   // 組合測試用精確（不抽樣），黃金資料量小，抽樣後人數多半低於 K
    for (const d of await optionsOf(dir)) for (const s of await optionsOf(sess)) {
      await dir.selectOption(d); await sess.selectOption(s);
      if (d === 'before') await page.selectOption('select[aria-label="起點事件"]', 'bond_deal');
      const r = await query(page, '/v1/analytics/path');
      expect(r.status, `路徑 ${d}／${s}：${r.body.detail ?? ''}`).toBe(200);
      expect([r.req.direction, r.req.session?.mode ?? 'gap']).toEqual([d, s]);
      expect(Number(r.body.anchor_users), `路徑 ${d}／${s}：起點人數 > 0`).toBeGreaterThan(0);
      // 要嘛有節點與連線；要嘛每條路徑都因 K 匿名被抑制（例：沒有 session_id 時整段期間算一條會話，路徑分散、每條人數 < K）——不可兩者皆非
      const paths = r.body.paths as { suppressed?: boolean }[];
      if (r.body.nodes.length === 0) expect(paths.length > 0 && paths.every((x) => x.suppressed), `路徑 ${d}／${s}：沒有節點時，所有路徑都應是 K 匿名抑制`).toBe(true);
      else expect(r.body.links.length, `路徑 ${d}／${s} 有節點就應有連線`).toBeGreaterThan(0);
    }
    for (const v of await optionsOf(smp)) {
      await smp.selectOption(v);
      const r = await query(page, '/v1/analytics/path');
      expect(r.status, `抽樣 ${v}：${r.body.detail ?? ''}`).toBe(200);
      expect(String(r.req.sample), '送出的抽樣值 = 畫面選的').toBe(v);
      expect(r.body.sampled ?? null, '回應標示的抽樣 = 送出的').toEqual(v === 'null' ? null : Number(v));
    }
    const steps = await optionsOf(page.locator('select[aria-label="步數"]'));
    for (const n of [steps[0], steps[steps.length - 1]]) {
      await page.selectOption('select[aria-label="步數"]', n);
      const r = await query(page, '/v1/analytics/path');
      expect(r.status, `步數 ${n}：${r.body.detail ?? ''}`).toBe(200);
      expect(String(r.req.steps)).toBe(n);
    }
  });

  test('歸因：每個模型與觸點拆分都能查，各模型歸因合計一致', async () => {
    await page.goto('/attribution');
    await page.selectOption('select[aria-label="轉化事件"]', 'bond_deal');
    await page.selectOption('select[aria-label="觸點 1 事件"]', 'push_click');
    await page.getByRole('button', { name: '＋ 觸點' }).click();
    await page.selectOption('select[aria-label="觸點 2 事件"]', 'act_click');
    await setRange(page, '2026-10-01', '2026-10-16');
    const boxes = page.locator('.mantine-Checkbox-root');
    const n = await boxes.count();
    for (let i = 0; i < n; i++) { const cb = boxes.nth(i).locator('input'); if (!(await cb.isChecked())) await cb.check(); }   // 全部模型都勾
    const r = await query(page, '/v1/analytics/attribution');
    expect(r.status, r.body.detail).toBe(200);
    expect(r.req.models.length, '畫面上的模型都送出').toBe(n);
    // 每次有觸點的轉化只分配 1 份功勞：沒有抑制時，每個模型各觸點的歸因轉化加總 = 有觸點的轉化數
    const rows = r.body.rows as { suppressed: boolean; models: Record<string, { conversions: number | null }> }[];
    expect(rows.length).toBeGreaterThan(0);
    if (!rows.some((x) => x.suppressed)) for (const m of r.req.models as string[]) {
      const sum = rows.reduce((t, x) => t + Number(x.models[m]?.conversions ?? 0), 0);
      expect(Math.abs(sum - Number(r.body.summary.touched)), `模型 ${m}：歸因加總 ${sum} 應等於有觸點的轉化 ${r.body.summary.touched}`).toBeLessThan(0.01);
    }
    for (const sp of (await optionsOf(page.locator('select[aria-label="拆分"]'))).filter(Boolean)) {
      await page.selectOption('select[aria-label="拆分"]', sp);
      const s = await query(page, '/v1/analytics/attribution');
      expect(s.status, `拆分 ${sp}：${s.body.detail ?? ''}`).toBe(200);
      expect(s.req.split).toBe(sp);
    }
  });
});
