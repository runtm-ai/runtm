// Preloaded into each test client (node --import): sends lake.js's Cloud Storage
// requests to the fake in lake.test.mjs, so lake.js itself runs unchanged.
const fake = process.env.FAKE_STORAGE_URL;
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) =>
  realFetch(String(url).replace('https://storage.googleapis.com', fake), init);
