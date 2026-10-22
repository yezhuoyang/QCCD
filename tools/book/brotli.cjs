// Write <file>.br next to <file> (brotli quality 9, 16 MiB window), as nginx brotli_static expects.
// usage: node brotli.cjs <file> [quality]
const fs = require('fs');
const zlib = require('zlib');
const f = process.argv[2];
const q = Number(process.argv[3] || 9);
const b = fs.readFileSync(f);
fs.writeFileSync(f + '.br', zlib.brotliCompressSync(b, { params: {
  [zlib.constants.BROTLI_PARAM_QUALITY]: q,
  [zlib.constants.BROTLI_PARAM_LGWIN]: 24,
  [zlib.constants.BROTLI_PARAM_SIZE_HINT]: b.length } }));
