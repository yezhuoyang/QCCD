
import fs from 'fs';
import { loadPage } from './shim.mjs';
import { PAGE_HOOK, applyScript } from './drive.mjs';
globalThis.__QCCD_SYNC = true;
loadPage('out/studio.html', PAGE_HOOK);
const ED = globalThis.EDITOR;
applyScript(ED, globalThis.__page, JSON.parse(fs.readFileSync(process.argv[2],'utf8')));
fs.writeFileSync(process.argv[3], JSON.stringify(ED.snapshot(), null, 1));
