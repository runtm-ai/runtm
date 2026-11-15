// lake.js end to end with the pinned DuckDB and no credentials: one local server
// plays runtm (the token endpoint), Cloud Storage (catalog objects with generations
// and ifGenerationMatch) and S3 (ETags, If-Match / If-None-Match, a prefix listing),
// storing the real catalog bytes. Each client is its own process, like two sessions
// of one template. Every test runs once per store.
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { after, before, test } from 'node:test';

const BUCKET = 'runtm-db-test';
const PREFIX = 'orgs/org-1/templates/tpl-1/';
const S3_BUCKET = 'runtm-sandbox-test-us-west-2';
const S3_PREFIX = 'databases/tpl-1/';

const stores = {
  gcs: { prefix: PREFIX, objects: new Map() }, // name -> { generation, body }
  s3: { prefix: S3_PREFIX, objects: new Map() }, // key -> { etag, body }
};
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

  if (req.method === 'POST' && url.pathname.endsWith('/v0/sandbox/shared-database/tokens')) {
    if (req.headers.authorization !== 'Bearer session-key') return send(401, { detail: 'bad key' });
    const expires_at = new Date(Date.now() + 3600_000).toISOString();
    if (url.pathname.startsWith('/s3/')) {
      return send(201, {
        url: `s3://${S3_BUCKET}/${S3_PREFIX}`,
        access_token: null,
        aws: { region: 'us-west-2', access_key_id: 'ASIAFAKE', secret_access_key: 'fake-secret', session_token: 'fake-session' },
        expires_at,
      });
    }
    return send(201, { url: `gs://${BUCKET}/${PREFIX}`, access_token: 'fake-gcs-token', expires_at });
  }
  if (url.pathname.startsWith('/s3-storage/')) return fakeS3(req, res, url);
  if (req.headers.authorization !== 'Bearer fake-gcs-token') return send(401, { error: 'no token' });
  const objects = stores.gcs.objects;

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

// S3 as lake.js uses it. Checks the request is signed with the session credentials
// (the signature itself is checked against botocore once, not here).
function fakeS3(req, res, url) {
  const objects = stores.s3.objects;
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    const reply = (status, payload = '', headers = {}) => {
      res.writeHead(status, headers);
      res.end(payload);
    };
    const auth = req.headers.authorization ?? '';
    if (!auth.startsWith('AWS4-HMAC-SHA256 Credential=ASIAFAKE/') || !auth.includes('/us-west-2/s3/aws4_request')
      || req.headers['x-amz-security-token'] !== 'fake-session'
      || req.headers['x-amz-content-sha256'] !== createHash('sha256').update(body).digest('hex')) {
      return reply(403, '<Error><Code>AccessDenied</Code></Error>');
    }
    const key = decodeURIComponent(url.pathname.slice('/s3-storage/'.length));
    const object = objects.get(key);

    if (req.method === 'GET' && key === '' && url.searchParams.get('list-type') === '2') {
      const prefix = url.searchParams.get('prefix') ?? '';
      // The credentials may only list the template's prefix (s3:prefix condition).
      if (!prefix.startsWith(S3_PREFIX)) return reply(403, '<Error><Code>AccessDenied</Code></Error>');
      const found = [...objects.keys()].filter(k => k.startsWith(prefix)).sort().slice(0, 1);
      const contents = found
        .map(k => `<Contents><Key>${k}</Key><ETag>${objects.get(k).etag.replaceAll('"', '&quot;')}</ETag></Contents>`)
        .join('');
      return reply(200, `<?xml version="1.0" encoding="UTF-8"?><ListBucketResult>${contents}</ListBucketResult>`);
    }
    if (req.method === 'GET') {
      // Missing objects are 403 without bucket-wide s3:ListBucket, which lake.js never has.
      if (!object) return reply(403, '<Error><Code>AccessDenied</Code></Error>');
      if (req.headers['if-match'] && req.headers['if-match'] !== object.etag) return reply(412);
      return reply(200, object.body, { 'Content-Type': 'application/gzip', ETag: object.etag });
    }
    if (req.method === 'PUT') {
      if (req.headers['if-none-match'] === '*' && object) return reply(412);
      if (req.headers['if-match'] && req.headers['if-match'] !== object?.etag) return reply(object ? 412 : 404);
      const etag = `"${createHash('md5').update(body).update(String(nextGeneration++)).digest('hex')}"`;
      objects.set(key, { etag, body });
      return reply(200, '', { ETag: etag });
    }
    reply(400, `unexpected ${req.method} ${url.pathname}`);
  });
}

/** An independent lake.js client in its own process, on the given store's lake. */
async function startClient(store) {
  const child = fork(new URL('./client.mjs', import.meta.url), {
    execArgv: ['--import', new URL('./redirect-storage.mjs', import.meta.url).href],
    env: {
      ...process.env,
      RUNTM_API_URL: `${baseUrl}/${store}/api`,
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

for (const store of ['gcs', 's3']) {
const { prefix: PREFIX, objects } = stores[store];
const startStoreClient = () => startClient(store);
const version = name => objects.get(name)?.generation ?? objects.get(name)?.etag;

test(`${store}: two independent clients see each other’s writes`, { timeout: 120_000 }, async () => {
  const a = await startStoreClient();
  const b = await startStoreClient();

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

test(`${store}: concurrent writes from two clients both survive`, { timeout: 120_000 }, async () => {
  const a = await startStoreClient();
  const b = await startStoreClient();
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

test(`${store}: a failed write publishes nothing, and unsupported SQL says what to write instead`, { timeout: 120_000 }, async () => {
  const a = await startStoreClient();
  await a.write('create table things (id varchar)');
  const before = version(`${PREFIX}catalog.ducklake.gz`);

  await assert.rejects(a.write('insert into missing_table values (1)'), /missing_table/);
  await assert.rejects(a.write('create table bad (id integer primary key)'), /crypto\.randomUUID/);

  assert.equal(version(`${PREFIX}catalog.ducklake.gz`), before);
});
}
