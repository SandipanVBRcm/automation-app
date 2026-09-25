const assert = require('node:assert/strict');
const { citedSources } = require('../src/lib/sources.ts');
const sources = [{chunk_id:'a'}, {chunk_id:'b'}];
assert.deepEqual(citedSources({content:'Google is open.', sources}), []);
assert.deepEqual(citedSources({content:'Fact [Source 2]', sources}).map(s=>s.chunk_id), ['b']);
assert.deepEqual(citedSources({content:'Fact [Source 2]', sources:[{chunk_id:'b',source_number:2}]}).map(s=>s.chunk_id), ['b']);
assert.deepEqual(citedSources({content:'Invalid [Source 99]', sources}), []);
console.log('PASS: uncited historical sources hidden; cited source numbering preserved');
