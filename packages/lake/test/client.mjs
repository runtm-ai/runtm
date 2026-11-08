// One independent lake.js client (its own process, so its own catalog cache and
// DuckDB connection). The test drives it over IPC: { op: 'write' | 'query', sql, params }.
import { query, write } from '../lake.js';

process.on('message', async ({ id, op, sql, params }) => {
  try {
    const rows = op === 'write' ? await write(db => db.run(sql, params)) : await query(sql, params);
    process.send({ id, rows: rows ?? null });
  } catch (err) {
    process.send({ id, error: err.message });
  }
});
process.send({ ready: true });
