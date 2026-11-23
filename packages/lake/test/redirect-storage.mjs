// Preloaded into each test client (node --import): sends lake.js's Cloud Storage and
// S3 requests to the fake in lake.test.mjs, so lake.js itself runs unchanged.
const fake = process.env.FAKE_STORAGE_URL;
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) =>
  realFetch(
    String(url)
      .replace('https://storage.googleapis.com', fake)
      .replace(/^https:\/\/[^/]+\.s3\.[a-z0-9-]+\.amazonaws\.com\//, `${fake}/s3-storage/`),
    init,
  );
