import assert from 'node:assert/strict';
import { api, apiForm, responseError } from '../src/lib/api.ts';

async function run() {
  const cases = [
    [new Response('Invalid host header', { status: 400, headers: { 'content-type': 'text/plain' } }), 'Invalid host header'],
    [new Response(JSON.stringify({ detail: 'Workspace not found' }), { status: 400 }), 'Workspace not found'],
    [new Response(JSON.stringify({ detail: [{ loc: ['body', 'name'], msg: 'Field required' }] }), { status: 422 }), 'body.name: Field required'],
    [new Response('', { status: 400 }), 'Request failed (400) at /api/example'],
  ];
  for (const [response, expected] of cases) {
    assert.equal((await responseError(response, '/api/example')).message, expected);
  }

  const originalFetch = global.fetch;
  try {
    global.fetch = async () => new Response('Bad request from backend', { status: 400, headers: { 'content-type': 'text/plain' } });
    await assert.rejects(api('/api/example'), /Bad request from backend/);
    await assert.rejects(apiForm('/api/example', new FormData()), /Bad request from backend/);
  } finally {
    global.fetch = originalFetch;
  }
  console.log('PASS: API errors show backend messages for JSON and plain text responses');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
