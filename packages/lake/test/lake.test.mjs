// lake.js end to end with the pinned DuckDB and no credentials: one local server
// plays both runtm (the token endpoint) and Cloud Storage (catalog objects with
// generations and ifGenerationMatch), storing the real catalog bytes. Each client
// is its own process, like two sessions of one template.
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { createServer } from 'node:http';
import { after, before, test } from 'node:test';

const BUCKET = 'runtm-db-test';
const PREFIX = 'orgs/org-1/templates/tpl-1/';

const objects = new Map(); // name -> { generation, body }
let nextGeneration = 1;
let server;
let baseUrl;
const clients = [];

function fakeRuntmAndStorage(req, res) {
  const url = new URL(req.url, 'http://fake');
  const send = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(body === undefined ? '' : JSON.stringify(body));
  };

  if (req.method === 'POST' && url.pathname === '/api/v0/sandbox/shared-database/tokens') {
    if (req.headers.authorization !== 'Bearer session-key') return send(401, { detail: 'bad key' });
    return send(201, {
      url: `gs://${BUCKET}/${PREFIX}`,
      access_token: 'fake-gcs-token',
      expires_at: new Date(Date.now() + 3600_000).toISOString(),
    });
  }
  if (req.headers.authorization !== 'Bearer fake-gcs-token') return send(401, { error: 'no token' });

  const objectPath = `/storage/v1/b/${BUCKET}/o/`;
  if (req.method === 'GET' && url.pathname.startsWith(objectPath)) {
    const object = objects.get(decodeURIComponent(url.pathname.slice(objectPath.length)));
    if (!object) return send(404, { error: 'not found' });
    if (url.searchParams.get('alt') !== 'media') return send(200, { generation: String(object.generation) });
    if (url.searchParams.get('generation') !== String(object.generation)) return send(404, { error: 'gone' });
    res.writeHead(200, { 'Content-Type': 'application/gzip' });
    return res.end(object.body);
  }
  if (req.method === 'POST' && url.pathname === `/upload/storage/v1/b/${BUCKET}/o`) {
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => {
      const name = url.searchParams.get('name');
      const current = objects.get(name)?.generation ?? 0;
      if (Number(url.searchParams.get('ifGenerationMatch')) !== current) {
        return send(412, { error: 'precondition failed' });
      }
      const generation = nextGeneration++;
      objects.set(name, { generation, body: Buffer.concat(chunks) });
      send(200, { generation: String(generation) });
    });
    return;
  }
  send(400, { error: `unexpected ${req.method} ${url.pathname}` });
}

/** An independent lake.js client in its own process. */
async function startClient() {
  const child = fork(new URL('./client.mjs', import.meta.url), {
    execArgv: ['--import', new URL('./redirect-storage.mjs', import.meta.url).href],
    env: {
      ...process.env,
      RUNTM_API_URL: `${baseUrl}/api`,
      RUNTM_TOKEN: 'session-key',
      FAKE_STORAGE_URL: baseUrl,
    },
  });
  clients.push(child);
  let id = 0;
  const pending = new Map();
  const ready = new Promise(resolve => {
    child.on('message', msg => {
      if (msg.ready) return resolve();
      const { resolve: done, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error)) : done(msg.rows);
    });
  });
  await ready;
  const call = (op, sql, params) =>
    new Promise((resolve, reject) => {
      pending.set(++id, { resolve, reject });
      child.send({ id, op, sql, params });
    });
  return {
    write: (sql, params) => call('write', sql, params),
    query: (sql, params) => call('query', sql, params),
  };
}

before(async () => {
  server = createServer(fakeRuntmAndStorage);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(() => {
  for (const child of clients) child.kill();
  server.close();
});

test('two independent clients see each other’s writes', { timeout: 120_000 }, async () => {
  const a = await startClient();
  const b = await startClient();

  await a.write('create table notes (id varchar, body varchar, created_at varchar)');
  await a.write('insert into notes values (?, ?, ?)', ['1', 'from a', '2026-01-01T00:00:00Z']);

  assert.deepEqual(await b.query('select body from notes order by created_at'), [{ body: 'from a' }]);
  await b.write('insert into notes values (?, ?, ?)', ['2', 'from b', '2026-01-02T00:00:00Z']);

  assert.deepEqual(await a.query('select body from notes order by created_at'), [
    { body: 'from a' },
    { body: 'from b' },
  ]);
  assert.ok(objects.has(`${PREFIX}catalog.ducklake.gz`), 'the catalog lives under the template prefix');
});

test('concurrent writes from two clients both survive', { timeout: 120_000 }, async () => {
  const a = await startClient();
  const b = await startClient();
  await a.write('create table hits (who varchar)');

  await Promise.all([
    ...Array.from({ length: 3 }, () => a.write("insert into hits values ('a')")),
    ...Array.from({ length: 3 }, () => b.write("insert into hits values ('b')")),
  ]);

  assert.deepEqual(await a.query('select who, count(*)::int as n from hits group by who order by who'), [
    { who: 'a', n: 3 },
    { who: 'b', n: 3 },
  ]);
});

test('a failed write publishes nothing, and unsupported SQL says what to write instead', { timeout: 120_000 }, async () => {
  const a = await startClient();
  await a.write('create table things (id varchar)');
  const before = objects.get(`${PREFIX}catalog.ducklake.gz`).generation;

  await assert.rejects(a.write('insert into missing_table values (1)'), /missing_table/);
  await assert.rejects(a.write('create table bad (id integer primary key)'), /crypto\.randomUUID/);

  assert.equal(objects.get(`${PREFIX}catalog.ducklake.gz`).generation, before);
});
