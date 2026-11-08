# lake.js

A small SQL database shared by every runtm session of one template. Copy
[`lake.js`](./lake.js) into your app; there is nothing to install from this repo.

```sh
curl -fsSLo lake.js https://raw.githubusercontent.com/runtm-ai/runtm/main/packages/lake/lake.js
npm install @duckdb/node-api@2.0.0-r.1
```

```js
import { query, write } from './lake.js';

await write(db => db.run('create table if not exists notes (id varchar, body varchar, created_at varchar)'));
await write(db => db.run('insert into notes values (?, ?, ?)', [crypto.randomUUID(), 'hi', new Date().toISOString()]));
console.log(await query('select * from notes order by created_at'));
```

It needs a runtm session whose template has **Shared database** turned on.
Usage, table rules and limits: [Share data across sessions](https://docs.runtm.com/guides/recipes/share-data-across-sessions).

Tests (not needed to use lake.js): `cd packages/lake && npm ci && npm test` runs two clients against a local fake of runtm and Cloud Storage.
