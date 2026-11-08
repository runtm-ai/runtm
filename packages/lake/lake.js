// lake.js: a small SQL database shared by every runtm session of one template.
//
// Copy this file into your app as-is, then:
//   npm install @duckdb/node-api@2.0.0-r.1
//
//   import { query, write } from './lake.js';
//   await write(db => db.run(`create table if not exists notes (id varchar, body varchar)`));
//   await write(db => db.run(`insert into notes values (?, ?)`, [crypto.randomUUID(), 'hi']));
//   const notes = await query(`select * from notes`);
//
// Requires a runtm session whose template has Shared database turned on. Guide:
// https://docs.runtm.com/guides/recipes/share-data-across-sessions
//
// How it works: DuckLake on Cloud Storage. The catalog is one object next to the
// Parquet data. Readers cache the catalog and re-download it when its generation
// changes. Writers download it, apply changes and upload with ifGenerationMatch, so
// concurrent writers can't overwrite each other; the loser retries on the new catalog.
// The location and a short-lived token come from runtm and are renewed before expiry.
import { execFileSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { gzip, gunzip } from 'node:zlib';
import { DuckDBInstance } from '@duckdb/node-api';

// Gzipped: the catalog is mostly empty DuckDB blocks (~6.5 MB raw, ~20 KB compressed).
const CATALOG_NAME = 'catalog.ducklake.gz';
// Per-process, so processes sharing a machine never touch each other's catalog copies.
const CACHE_DIR = path.join(os.tmpdir(), 'lake-cache', `${process.pid}-${randomUUID()}`);
const INLINE_ROW_LIMIT = 100; // inserts up to this many rows live in the catalog, not Parquet
const MAX_WRITE_ATTEMPTS = 10;
const TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000;
// Every app sharing a lake must run the same DuckDB: a catalog written by a newer
// DuckDB can't be opened by an older one, so one mismatched app would lock out the rest.
const DUCKDB_VERSION = 'v2.0';
const DUCKDB_PACKAGE = '@duckdb/node-api@2.0.0-r.1';

let lake = null; // { bucket, catalogObject, dataPath, token, expiresAt }
let connectionPromise;
let secretToken; // token currently loaded into the DuckDB secret
let current = null; // read-only catalog attached as "lake": { generation, file }
let queue = Promise.resolve();

// Rows as JSON-safe objects, always reflecting the latest committed data.
export function query(sql, params) {
  return exclusive(async () => {
    const connection = await getConnection();
    await refreshReader(connection);
    await connection.run('USE lake');
    try {
      return (await connection.runAndReadAll(sql, params)).getRowObjectsJson();
    } catch (err) {
      throw explain(err);
    }
  });
}

// Runs fn(db) against a writable copy and publishes it. db.run(sql, params) executes,
// db.query(sql, params) returns rows. fn may run more than once if another session
// commits first, so it should only touch the database (no emails, no API calls).
export function write(fn) {
  return exclusive(() => writeLocked(fn));
}

async function writeLocked(fn) {
  const connection = await getConnection();

  for (let attempt = 1; attempt <= MAX_WRITE_ATTEMPTS; attempt++) {
    const generation = await remoteGeneration();
    const file = path.join(CACHE_DIR, `write-${randomUUID()}.ducklake`);

    if (generation !== null && !(await downloadCatalog(generation, file))) continue;

    let result;
    let attached = false;
    try {
      const options = generation === null
        ? `DATA_PATH ${sqlString(lake.dataPath)}, DATA_INLINING_ROW_LIMIT ${INLINE_ROW_LIMIT}`
        : `DATA_INLINING_ROW_LIMIT ${INLINE_ROW_LIMIT}`;
      await connection.run(`ATTACH ${sqlString(`ducklake:${file}`)} AS lake_write (${options})`);
      attached = true;
      await connection.run('USE lake_write');
      result = await fn(dbHandle(connection));
    } catch (err) {
      await fs.rm(file, { force: true });
      throw explain(err);
    } finally {
      await connection.run('USE memory');
      if (attached) await connection.run('DETACH lake_write');
    }

    let newGeneration;
    try {
      newGeneration = await uploadCatalog(file, generation ?? 0);
    } catch (err) {
      await fs.rm(file, { force: true });
      throw err;
    }

    if (newGeneration !== null) {
      // The catalog we just uploaded is the newest one; use it for reads too.
      await swapReader(connection, { generation: newGeneration, file });
      return result;
    }

    // Another writer committed after our download: retry on top of their catalog.
    await fs.rm(file, { force: true });
    await sleep(100 * attempt + Math.random() * 200);
  }

  throw new Error(`lake write gave up after ${MAX_WRITE_ATTEMPTS} attempts`);
}

async function refreshReader(connection) {
  for (let attempt = 1; attempt <= MAX_WRITE_ATTEMPTS; attempt++) {
    const generation = await remoteGeneration();
    // First use of an empty lake: an empty write creates the catalog.
    if (generation === null) return writeLocked(async () => {});
    if (current?.generation === generation) return;

    const file = path.join(CACHE_DIR, `read-${randomUUID()}.ducklake`);
    if (await downloadCatalog(generation, file)) {
      return swapReader(connection, { generation, file });
    }
  }
  throw new Error('lake catalog kept changing while downloading it');
}

async function swapReader(connection, next) {
  if (current) {
    await connection.run('USE memory');
    await connection.run('DETACH lake');
    await fs.rm(current.file, { force: true });
  }
  await connection.run(`ATTACH ${sqlString(`ducklake:${next.file}`)} AS lake (READ_ONLY)`);
  current = next;
}

// Location and token, from the runtm session.

async function ensureLake() {
  if (lake && lake.expiresAt - Date.now() > TOKEN_REFRESH_MARGIN_MS) return lake;

  const apiUrl = process.env.RUNTM_API_URL;
  if (!apiUrl || !process.env.RUNTM_TOKEN) {
    throw new Error('lake.js runs inside a runtm session (RUNTM_API_URL and RUNTM_TOKEN are not set)');
  }
  const requestToken = sessionToken => fetch(`${apiUrl.replace(/\/$/, '')}/v0/sandbox/shared-database/tokens`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  let res = await requestToken(process.env.RUNTM_TOKEN);
  if (res.status === 401) {
    // The session key rotates when a session resumes, and this process still holds the
    // old one. A login shell sources the session's current environment.
    const fresh = loginShellToken();
    if (fresh && fresh !== process.env.RUNTM_TOKEN) {
      process.env.RUNTM_TOKEN = fresh;
      res = await requestToken(fresh);
    }
  }
  if (res.status === 404) {
    throw new Error("Shared database is off for this session's template. Turn it on in the template's settings, then retry.");
  }
  if (!res.ok) throw new Error(`runtm shared database token failed: ${res.status} ${await res.text()}`);

  const { url, access_token: token, expires_at: expiresAt } = await res.json();
  const [, bucket, prefix] = url.match(/^gs:\/\/([^/]+)\/(.*)$/);
  lake = {
    bucket,
    catalogObject: `${prefix}${CATALOG_NAME}`,
    dataPath: `gs://${bucket}/${prefix}data/`,
    token,
    expiresAt: Date.parse(expiresAt),
  };
  return lake;
}

function loginShellToken() {
  try {
    return execFileSync('bash', ['-lc', 'printf %s "$RUNTM_TOKEN"'], { encoding: 'utf8', timeout: 10_000 });
  } catch {
    return null;
  }
}

// Catalog object via the GCS JSON API.

function objectUrl({ bucket, catalogObject }) {
  return `https://storage.googleapis.com/storage/v1/b/${bucket}/o/${encodeURIComponent(catalogObject)}`;
}

async function remoteGeneration() {
  const res = await gcsFetch(l => `${objectUrl(l)}?fields=generation`, {}, [404]);
  if (res.status === 404) return null;
  return (await res.json()).generation;
}

// Downloads exactly this generation; false if it was replaced in the meantime.
async function downloadCatalog(generation, destination) {
  const res = await gcsFetch(l => `${objectUrl(l)}?alt=media&generation=${generation}`, {}, [404]);
  if (res.status === 404) return false;
  await fs.writeFile(destination, await promisify(gunzip)(Buffer.from(await res.arrayBuffer())));
  return true;
}

// Returns the new generation, or null if the catalog changed since ifGenerationMatch.
async function uploadCatalog(file, ifGenerationMatch) {
  const body = await promisify(gzip)(await fs.readFile(file));
  const res = await gcsFetch(
    ({ bucket, catalogObject }) => `https://storage.googleapis.com/upload/storage/v1/b/${bucket}/o`
      + `?uploadType=media&name=${encodeURIComponent(catalogObject)}&ifGenerationMatch=${ifGenerationMatch}`,
    { method: 'POST', headers: { 'Content-Type': 'application/gzip' }, body },
    [412],
  );
  if (res.status === 412) return null;
  return (await res.json()).generation;
}

async function gcsFetch(urlFor, init, allowedStatuses) {
  const l = await ensureLake();
  const url = urlFor(l);
  const res = await fetch(url, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${l.token}` },
  });
  if (res.ok || allowedStatuses.includes(res.status)) return res;
  throw new Error(`GCS ${init.method ?? 'GET'} ${url.split('?')[0]} failed: ${res.status} ${await res.text()}`);
}

// DuckDB connection, with the GCS secret kept in sync with the current token.

async function getConnection() {
  connectionPromise ??= (async () => {
    await fs.mkdir(CACHE_DIR, { recursive: true });
    process.once('exit', () => rmSync(CACHE_DIR, { recursive: true, force: true }));
    const instance = await DuckDBInstance.create(':memory:');
    const connection = await instance.connect();
    const [{ version }] = (await connection.runAndReadAll('select version() as version')).getRowObjectsJson();
    if (!version.startsWith(`${DUCKDB_VERSION}.`)) {
      throw new Error(`lake.js needs DuckDB ${DUCKDB_VERSION}.x but this app has ${version}. Run: npm install ${DUCKDB_PACKAGE}`);
    }
    for (const sql of ['INSTALL ducklake', 'LOAD ducklake', 'INSTALL httpfs', 'LOAD httpfs']) {
      await connection.run(sql);
    }
    return connection;
  })().catch(err => {
    connectionPromise = undefined;
    throw err;
  });

  const connection = await connectionPromise;
  const { token } = await ensureLake();
  if (token !== secretToken) {
    await connection.run(`CREATE OR REPLACE SECRET gcs_secret (TYPE gcs, BEARER_TOKEN ${sqlString(token)})`);
    secretToken = token;
  }
  return connection;
}

function dbHandle(connection) {
  return {
    run: (sql, params) => connection.run(sql, params),
    query: async (sql, params) => (await connection.runAndReadAll(sql, params)).getRowObjectsJson(),
  };
}

// SQL this database doesn't support, with what to write instead.
function explain(err) {
  const hints = [
    [/PRIMARY KEY\/UNIQUE/, 'Drop PRIMARY KEY/UNIQUE and use varchar ids from crypto.randomUUID().'],
    [/does not support sequences/, 'There are no sequences or auto-increment ids: insert crypto.randomUUID() as the id.'],
    [/Only literals .* are supported as default values/, 'Defaults must be literals: set timestamps like created_at in your app (new Date().toISOString()).'],
  ];
  const hint = hints.find(([pattern]) => pattern.test(err.message));
  if (hint) err.message = `${err.message}\n${hint[1]}`;
  return err;
}

// One DuckDB connection per process, so lake operations run one at a time.
function exclusive(task) {
  const result = queue.then(task);
  queue = result.catch(() => {});
  return result;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
