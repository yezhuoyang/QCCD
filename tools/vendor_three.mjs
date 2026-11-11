// Regenerate qccd/viz/vendor/three.qccd.min.js: the part of three.js the 3D stage uses.
//
//   node tools/vendor_three.mjs            # needs network: npm pack three, npx esbuild
//
// WHY A VENDORED FILE.  A page is one self-contained file with no network
// (`render.py::FORBIDDEN`), so the library cannot come from a CDN; and the toolchain is
// standard-library Python, so building a page must not need Node.  The bundle is therefore
// built ONCE, here, and checked in.  Only this script needs Node and npm.
//
// WHAT IS IN IT.  The names in EXPORTS and what they pull in, tree-shaken and minified:
// the WebGL renderer, instanced meshes, a few geometries and materials, lights, the orbit
// controls and ray picking.  No loaders, so no network call exists in the file, and no
// `eval`, `new Function` or `Worker`, so it runs under the workspace's content security
// policy (`script-src 'unsafe-inline'`).  tests/test_stage3d.py checks all of that against
// the file as committed, and its SHA-256 against qccd/viz/vendor/three.qccd.json.
//
// THE LICENCE.  three.js is MIT.  Its notice is kept at the top of the bundle and in
// qccd/viz/vendor/THREE.LICENSE.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const THREE_VERSION = '0.187.0';
const ESBUILD_VERSION = '0.24.0';
const GLOBAL = 'QCCDTHREE';
const EXPORTS = [
  'Scene', 'Group', 'PerspectiveCamera', 'WebGLRenderer',
  'Mesh', 'InstancedMesh', 'InstancedBufferAttribute', 'DynamicDrawUsage',
  'BoxGeometry', 'SphereGeometry', 'CylinderGeometry', 'CircleGeometry', 'PlaneGeometry',
  'MeshStandardMaterial', 'MeshBasicMaterial', 'DoubleSide',
  'HemisphereLight', 'DirectionalLight',
  'Color', 'Matrix4', 'Vector2', 'Vector3', 'Quaternion', 'Raycaster',
];

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'qccd', 'viz', 'vendor');
const work = mkdtempSync(join(tmpdir(), 'qccd-three-'));
const shell = process.platform === 'win32';
try {
  execFileSync('npm', ['pack', `three@${THREE_VERSION}`, '--silent'], { cwd: work, shell, stdio: 'inherit' });
  const tgz = readdirSync(work).find((f) => f.endsWith('.tgz'));
  execFileSync('tar', ['xzf', tgz], { cwd: work, shell, stdio: 'inherit' });
  const pkg = join(work, 'package');
  // OrbitControls imports the bare specifier 'three'; point it at the same build file
  const oc = join(pkg, 'examples', 'jsm', 'controls', 'OrbitControls.js');
  writeFileSync(oc, readFileSync(oc, 'utf8').replace(/from 'three'/g, "from '../../../build/three.module.js'"));
  const entry = join(work, 'entry.js');
  writeFileSync(entry,
    `import { ${EXPORTS.join(', ')} } from './package/build/three.module.js';\n` +
    `import { OrbitControls } from './package/examples/jsm/controls/OrbitControls.js';\n` +
    `globalThis.${GLOBAL} = { ${EXPORTS.join(', ')}, OrbitControls, REVISION: '${THREE_VERSION}' };\n`);
  const built = join(work, 'bundle.js');
  execFileSync('npx', ['--yes', `esbuild@${ESBUILD_VERSION}`, 'entry.js', '--bundle', '--minify',
    '--format=iife', '--legal-comments=none', '--charset=ascii', `--outfile=${built}`,
    '--log-level=warning'], { cwd: work, shell, stdio: 'inherit' });
  const licence = readFileSync(join(pkg, 'LICENSE'), 'utf8').replace(/\r\n/g, '\n');
  const notice = licence.split('\n').filter((l) => /copyright/i.test(l))[0].trim();
  const header =
    `/* three.js ${THREE_VERSION} (https://threejs.org), the part the QCCD 3D stage uses.\n` +
    ` * ${notice.replace(/[^\x20-\x7e]/g, '(c)')}  SPDX-License-Identifier: MIT\n` +
    ` * The full licence text is qccd/viz/vendor/THREE.LICENSE.  Built by tools/vendor_three.mjs;\n` +
    ` * do not edit by hand. */\n`;
  const body = readFileSync(built, 'utf8').replace(/\r\n/g, '\n').replace(/<\/script/gi, '<\\/script');
  const text = header + body;
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, 'three.qccd.min.js'), text);
  writeFileSync(join(out, 'THREE.LICENSE'), licence);
  const manifest = {
    file: 'three.qccd.min.js', three: THREE_VERSION, esbuild: ESBUILD_VERSION, global: GLOBAL,
    exports: [...EXPORTS, 'OrbitControls'], bytes: Buffer.byteLength(text),
    sha256: createHash('sha256').update(text).digest('hex'),
  };
  writeFileSync(join(out, 'three.qccd.json'), JSON.stringify(manifest, null, 1) + '\n');
  console.log(`wrote qccd/viz/vendor/three.qccd.min.js: ${manifest.bytes} bytes, sha256 ${manifest.sha256.slice(0, 16)}...`);
} finally {
  rmSync(work, { recursive: true, force: true });
}
