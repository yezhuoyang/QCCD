// Where Chrome is, for the drivers that run a real one: CHROME if set, else the usual install
// places on Windows, macOS and Linux (the same list as tests/chrome_path.py).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PLACES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  path.join(os.homedir(), 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
  '/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium-browser', '/usr/bin/chromium',
];

export function findChrome() {
  return process.env.CHROME || PLACES.find((p) => fs.existsSync(p)) || null;
}
