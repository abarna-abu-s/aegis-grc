const { test, expect } = require('@playwright/test');

const PW = 'Str0ng-Password-1';
let n = 0;
const uniq = () => `${Date.now()}${++n}`;

async function register(page, org = 'E2E Org') {
  const email = `e2e${uniq()}@example.com`;
  await page.goto('/');
  await page.getByRole('link', { name: 'Create an account' }).click();
  await page.fill('input[name=full_name]', 'E2E Tester');
  await page.fill('input[name=organization_name]', org);
  await page.fill('input[name=email]', email);
  await page.fill('input[name=password]', PW);
  await page.getByRole('button', { name: /Create account/ }).click();
  await expect(page.locator('.shell')).toBeVisible();
  return email;
}

const goto = (page, hash) => page.evaluate((h) => { location.hash = h; }, hash);

test.describe.configure({ mode: 'serial' });

test('registration, empty-state dashboard and sign-out / sign-in', async ({ page }) => {
  const email = await register(page);
  await expect(page.getByRole('heading', { name: 'Compliance overview' })).toBeVisible();
  await expect(page.getByText('Get started')).toBeVisible();            // onboarding instead of fabricated numbers
  await expect(page.locator('.kpi .value').first()).toHaveText('-');    // readiness has no data yet
  await page.getByLabel('Profile menu').click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.fill('input[name=email]', email);
  await page.fill('input[name=password]', 'wrong-Password-1');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByText('Incorrect email or password')).toBeVisible();
  await page.fill('input[name=password]', PW);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.locator('.shell')).toBeVisible();
});

test('unauthenticated users see the sign-in screen, never data', async ({ page }) => {
  await page.goto('/#/controls');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  const res = await page.request.get('/api/controls');
  expect(res.status()).toBe(401);
});

test('framework -> assessment -> controls -> evidence -> approval -> compliant -> score', async ({ page }) => {
  await register(page);
  await goto(page, '#/frameworks');
  await expect(page.getByText('Template - not legally verified').first()).toBeVisible();
  const iso = page.locator('tr', { hasText: 'ISO/IEC 27001' }).first();
  await iso.getByRole('button', { name: 'Start assessment' }).click();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('heading', { name: /ISO\/IEC 27001/ })).toBeVisible();

  await page.getByRole('button', { name: 'Create / reuse controls' }).click();
  await page.getByRole('button', { name: 'Create / reuse', exact: true }).click();
  await expect(page.getByText(/controls created/)).toBeVisible();

  // cannot mark compliant without approved evidence
  await page.getByPlaceholder('Search...').fill('Access control');
  const row = page.locator('tr', { hasText: '5.15' });
  await row.click();
  await page.locator('select[name=result]').selectOption('compliant');
  await page.getByRole('button', { name: 'Save assessment' }).click();
  await expect(page.getByText(/no approved, unexpired evidence/)).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();

  // open the linked control and upload evidence
  await row.locator('a', { hasText: /ISO27001/ }).click();
  await expect(page.getByRole('heading', { name: /Access control/ })).toBeVisible();
  await page.getByRole('button', { name: '+ Upload evidence' }).click();
  await page.fill('input[name=title]', 'Q3 access review');
  await page.setInputFiles('input[name=file]', { name: 'review.txt', mimeType: 'text/plain', buffer: Buffer.from('Quarterly access review of privileged users completed.') });
  const future = new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10);
  await page.fill('input[name=valid_until]', future);
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Evidence uploaded and awaiting review')).toBeVisible();
  await expect(page.locator('tr', { hasText: 'Q3 access review' }).getByText('Pending').first()).toBeVisible();

  // review & approve
  await page.getByRole('link', { name: 'Q3 access review' }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).last().click();
  await expect(page.getByText('Evidence approved')).toBeVisible();

  // now compliant is allowed
  await goto(page, '#/assessments');
  await page.getByRole('link', { name: /ISO\/IEC 27001/ }).click();
  await page.getByPlaceholder('Search...').fill('Access control');
  await page.locator('tr', { hasText: '5.15' }).click();
  await page.locator('select[name=result]').selectOption('compliant');
  await page.getByRole('button', { name: 'Save assessment' }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();

  await goto(page, '#/dashboard');
  await expect(page.getByRole('heading', { name: 'Compliance overview' })).toBeVisible();
  const score = await page.locator('.kpi .value').first().innerText();
  expect(Number(score)).toBeGreaterThan(0);
});

test('risk creation with validation, scoring and delete confirmation', async ({ page }) => {
  await register(page);
  await goto(page, '#/risks');
  await page.getByRole('button', { name: '+ New risk' }).first().click();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('This field is required').first()).toBeVisible();   // client-side validation
  await page.fill('input[name=title]', 'Ransomware outage');
  await page.fill('input[name=likelihood]', '4');
  await page.fill('input[name=impact]', '5');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Risk saved')).toBeVisible();
  const row = page.locator('tr', { hasText: 'Ransomware outage' });
  await expect(row).toContainText('20');
  await expect(row.getByText('Critical')).toBeVisible();
  // server-side validation message for out-of-range value
  await row.getByRole('button', { name: 'Edit' }).click();
  await page.fill('input[name=likelihood]', '9');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText(/Must be between 1 and 5/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  // destructive action needs confirmation
  await row.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(/cannot be undone/)).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: 'Delete' }).last().click();
  await expect(page.getByText('Deleted')).toBeVisible();
});

test('policy approval workflow feeds the copilot with citations; no AI provider fallback is explained', async ({ page }) => {
  await register(page);
  await goto(page, '#/policies');
  await page.getByRole('button', { name: '+ New policy' }).first().click();
  await page.fill('input[name=title]', 'Password Policy');
  await page.fill('textarea[name=body]', 'Passwords must be rotated every 90 days.\n\nMulti-factor authentication is mandatory for administrators.');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Policy saved')).toBeVisible();

  await goto(page, '#/copilot');
  await expect(page.getByText(/No AI provider is configured|deterministic/).first()).toBeVisible();
  await page.getByLabel('Question').fill('How often are passwords rotated?');
  await page.getByRole('button', { name: 'Ask' }).click();
  await expect(page.getByText(/No supporting passages were found/)).toBeVisible();   // draft policy is not retrievable

  await goto(page, '#/policies');
  await page.locator('tr', { hasText: 'Password Policy' }).getByRole('button', { name: 'Request approval' }).click();
  await page.getByRole('button', { name: 'Send for approval' }).click();
  await goto(page, '#/workflow');
  await page.locator('tr', { hasText: 'Password Policy' }).getByRole('button', { name: 'Approve' }).click();
  await page.getByRole('button', { name: 'Approve' }).last().click();
  await expect(page.getByText('Request approved')).toBeVisible();

  await goto(page, '#/copilot');
  await page.getByLabel('Question').fill('How often are passwords rotated?');
  await page.getByRole('button', { name: 'Ask' }).click();
  await expect(page.locator('.cite', { hasText: 'Password Policy' })).toBeVisible();
  await expect(page.getByText('AI suggestion').first()).toBeVisible();
});

test('command palette searches live data and navigates', async ({ page }) => {
  await register(page);
  await goto(page, '#/controls');
  await page.getByRole('button', { name: '+ New control' }).first().click();
  await page.fill('input[name=control_key]', 'ZZ-77');
  await page.fill('input[name=title]', 'Quokka handling');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Control saved')).toBeVisible();
  await page.keyboard.press('Control+k');
  await page.getByLabel('Command palette').first().fill('quokka');
  await page.locator('.palette-item', { hasText: 'Quokka handling' }).click();
  await expect(page.getByRole('heading', { name: /ZZ-77/ })).toBeVisible();
});

test('read-only viewer cannot create records and sees no write controls; tenants are isolated', async ({ page, request }) => {
  const email = await register(page, 'Tenant A');
  await goto(page, '#/controls');
  await page.getByRole('button', { name: '+ New control' }).first().click();
  await page.fill('input[name=control_key]', 'A-ONLY');
  await page.fill('input[name=title]', 'Tenant A secret control');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Control saved')).toBeVisible();

  const token = await page.evaluate(() => sessionStorage.getItem('aegis_token'));
  const viewerEmail = `viewer${uniq()}@example.com`;
  const add = await request.post('/api/members', { headers: { Authorization: `Bearer ${token}` }, data: { email: viewerEmail, role: 'viewer', full_name: 'Vee Viewer', temporary_password: PW } });
  expect(add.status()).toBe(201);

  await page.getByLabel('Profile menu').click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.fill('input[name=email]', viewerEmail);
  await page.fill('input[name=password]', PW);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.locator('.shell')).toBeVisible();
  await goto(page, '#/controls');
  await expect(page.getByText('Tenant A secret control')).toBeVisible();   // same org, read access
  await expect(page.getByRole('button', { name: '+ New control' })).toHaveCount(0);
  await goto(page, '#/audit-trail');
  await expect(page.getByText('Access restricted')).toBeVisible();
  const vt = await page.evaluate(() => sessionStorage.getItem('aegis_token'));
  const denied = await request.post('/api/controls', { headers: { Authorization: `Bearer ${vt}` }, data: { control_key: 'X', title: 'nope' } });
  expect(denied.status()).toBe(403);

  // a different tenant sees nothing of Tenant A
  await page.getByLabel('Profile menu').click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await register(page, 'Tenant B');
  await goto(page, '#/controls');
  await expect(page.getByText('Tenant A secret control')).toHaveCount(0);
  await expect(page.getByText('No controls yet')).toBeVisible();
  void email;
});

test('evidence upload rejects disallowed file types with a clear message', async ({ page }) => {
  await register(page);
  await goto(page, '#/evidence');
  await page.getByRole('button', { name: '+ Upload evidence' }).first().click();
  await page.fill('input[name=title]', 'Bad file');
  await page.setInputFiles('input[name=file]', { name: 'tool.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ binary') });
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText(/not allowed/)).toBeVisible();
});

test('responsive layout: sidebar collapses on a phone-width screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await register(page);
  await expect(page.locator('.sidebar')).not.toBeInViewport();
  await page.getByLabel('Toggle sidebar').click();
  await expect(page.locator('.sidebar')).toBeInViewport();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBe(false);
});
