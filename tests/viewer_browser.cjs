// Optional browser verification. The shipped viewer has no Node dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'director-viewer-browser-'));
  const profile = path.join(root, 'profile');
  await fs.mkdir(profile);
  const teaching = '# Teaching\n\n' + Array.from({length: 25}, (_, i) =>
    `## ${i + 1}. Lesson ${i + 1}\n\nOperator: Preserve wording ${i + 1}.\n\nSource: thread example, 05-09-2026.\n`).join('\n');
  await fs.writeFile(path.join(profile, 'qa.md'), teaching);
  await fs.writeFile(path.join(profile, 'how.md'), '## Interpretation (Q1)\n\nKeep <img src=x onerror="window.injected=true"> literal.\n');
  const child = spawn(process.env.PYTHON || 'python3', ['-B', 'director/cli.py', '--home', root, 'memory', '--no-open'],
    {cwd: path.resolve(__dirname, '..'), stdio: ['ignore', 'pipe', 'pipe']});
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Viewer startup timed out')), 10000);
      let stdout = '', stderr = '';
      child.stdout.on('data', data => {
        stdout += data;
        const match = stdout.match(/http:\/\/127\.0\.0\.1:\d+\/#[^\s]+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
      child.stderr.on('data', data => { stderr += data; });
      child.on('error', error => { clearTimeout(timer); reject(error); });
      child.on('exit', code => { clearTimeout(timer); reject(new Error(`Viewer exited ${code}: ${stderr}`)); });
    });
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_EXECUTABLE} : {})});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme});
      for (const width of [1024, 736, 360]) {
        await page.setViewportSize({width, height: 900});
        await page.goto(url);
        await page.getByText('25 saved entries', {exact: true}).waitFor();
        await page.locator('#lp-search').fill('');
        await page.locator('#lp-next').click();
        assert.equal(await page.locator('#lp-range').innerText(), '13–24 of 25 entries');
        await page.locator('#lp-prev').click();
        await page.locator('.lp-item').first().click();
        await page.locator('#lp-detail-page').waitFor();
        assert(await page.locator('#lp-list-page').isHidden());
        assert.match(await page.locator('#interpretations').innerText(), /<img/);
        assert.equal(await page.locator('#interpretations img').count(), 0);
        assert.equal(await page.evaluate(() => window.injected), undefined);
        assert.equal(await page.locator('#stored-at').innerText(), 'Not recorded per entry');
        await page.locator('#locations summary').first().click();
        assert.match(await page.locator('#locations pre').first().innerText(), /Preserve wording 1/);
        await page.reload();
        await page.locator('#lp-detail-page').waitFor();
        assert.match(await page.locator('#lp-detail-title').innerText(), /Lesson 1/);
        await page.goBack();
        await page.locator('#lp-list-page').waitFor();
        await page.goForward();
        await page.locator('#lp-detail-page').waitFor();
        await page.locator('#lp-back').click();
        await page.locator('#lp-list-page').waitFor();
        await page.locator('#lp-search').fill('Lesson 25');
        assert.equal(await page.locator('.lp-item').count(), 1);
        await page.locator('.lp-item').click();
        await page.locator('#lp-detail-page').waitFor();
        assert.equal(await page.locator('#interpretations').innerText(), 'No separate saved interpretation identified.');
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        assert.equal(overflow, false, `Overflow at ${width}, ${colorScheme}`);
        if (process.env.VIEWER_SCREENSHOTS && width === 736 && colorScheme === 'light') {
          await fs.mkdir(process.env.VIEWER_SCREENSHOTS, {recursive: true});
          await page.screenshot({path: path.join(process.env.VIEWER_SCREENSHOTS, 'detail.png'), fullPage: true});
        }
      }
    }
    await page.goto(url);
    await page.getByText('25 saved entries', {exact: true}).waitFor();
    await fs.appendFile(path.join(profile, 'qa.md'), '\n## 26. Added later\nNew content.\n');
    await page.locator('#refresh').click();
    await page.getByText('26 saved entries', {exact: true}).waitFor();
    await fs.writeFile(path.join(profile, 'qa.md'), '');
    await fs.writeFile(path.join(profile, 'how.md'), '');
    await page.locator('#refresh').click();
    await page.getByText('0 saved entries', {exact: true}).waitFor();
    assert.match(await page.locator('#lp-list').innerText(), /No saved teachings/);
    await page.goto(url.split('#')[0]);
    await page.locator('#error').waitFor();
    assert.match(await page.locator('#error').innerText(), /full URL/);
    assert.deepEqual(errors, []);
    assert.deepEqual((await fs.readdir(root)).sort(), ['profile']);
    console.log('Passed: list/detail, history, reload, search, pagination, refresh, empty state, literal markup, missing token, 3 widths and 2 themes.');
  } finally {
    if (browser) await browser.close();
    child.kill('SIGTERM');
    await fs.rm(root, {recursive: true, force: true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
