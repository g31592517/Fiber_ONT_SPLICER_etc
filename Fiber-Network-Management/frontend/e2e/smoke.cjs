const { chromium } = require('playwright');
(async () => {
  const S = process.argv[2] || 'e2e-output'; require('fs').mkdirSync(S + '/shots', { recursive: true });
  const B = process.env.FNMS_UI_URL || 'http://127.0.0.1:4173';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(`[pageerror] ${page.url()} ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`[console] ${page.url()} ${m.text()}`); });
  await page.goto(B + '/');
  await page.fill('input[autocomplete=username]', 'admin');
  await page.fill('input[type=password]', 'ChangeMe!2026');
  await page.click('button.primary');
  await page.waitForSelector('text=Network dashboard');
  const routes = ['/', '/incidents', '/alarms', '/sites', '/olts', '/olts/1', '/pon-ports', '/pon-ports/1', '/onts', '/onts/1', '/discovered', '/optical',
    '/fats', '/fats/2', '/splitters', '/splitters/1', '/cables', '/cables/1', '/distribution-points', '/distribution-points/1', '/links', '/topology', '/map',
    '/subscribers', '/subscribers/1', '/provisioning/jobs', '/provisioning/templates', '/provisioning/bandwidth', '/provisioning/vlans',
    '/provisioning/vendor-profiles', '/monitoring/thresholds', '/monitoring/jobs', '/integrations', '/integrations/health', '/simulator',
    '/admin/users', '/admin/roles', '/admin/audit', '/admin/settings', '/incidents/2'];
  for (const r of routes) {
    await page.goto(B + r);
    await page.waitForTimeout(1200);
    const txt = await page.locator('main').innerText().catch(() => '');
    if (/Page not found|Internal server error/.test(txt)) errors.push(`[content] ${r} shows error text`);
    const errBox = await page.locator('.error-box').count();
    if (errBox) errors.push(`[errorbox] ${r}: ${(await page.locator('.error-box').first().innerText()).slice(0,150)}`);
  }
  // OLT tabs
  await page.goto(B + '/olts/1');
  for (const t of ['Hardware', 'PON ports', 'ONTs', 'Profiles', 'History', 'Capabilities', 'Operations']) { await page.click(`.tabs button:has-text("${t}")`); await page.waitForTimeout(500); }
  await page.goto(B + '/onts/1');
  for (const t of ['Fiber path', 'Optical history', 'Status history', 'Alarms', 'Provisioning']) { await page.click(`.tabs button:has-text("${t}")`); await page.waitForTimeout(500); }
  console.log(errors.length ? errors.join('\n') : 'NO ERRORS');
  await browser.close();
})();
