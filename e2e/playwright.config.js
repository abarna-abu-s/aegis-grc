const { defineConfig } = require('@playwright/test');
const fs = require('fs');
const os = require('os');
const path = require('path');

const root = path.resolve(__dirname, '..');
const venvPython = process.platform === 'win32' ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python');
const python = process.env.PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'aegis_e2e_'));
const PORT = 8123;

module.exports = defineConfig({
  testDir: './tests',
  timeout: 60_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: process.env.PW_CHANNEL || undefined, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: {
    command: `"${python}" -W ignore -m uvicorn app.main:app --port ${PORT}`,
    cwd: root,
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: { DATABASE_URL: `sqlite:///${tmp.replace(/\\/g, '/')}/e2e.db`, UPLOAD_DIR: path.join(tmp, 'uploads'), JWT_SECRET: 'e2e-secret-' + 'y'.repeat(40),
      RATE_LIMIT_PER_MINUTE: '100000', AUTH_RATE_LIMIT_PER_MINUTE: '100000', AI_PROVIDER: 'none' },
  },
});
