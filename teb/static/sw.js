// teb Service Worker — offline support + API caching + offline queue
const CACHE_NAME = 'teb-v4';
const STATIC_ASSETS = [
  './',
  './static/style.css',
  './static/app.js',
  './static/manifest.json',
];

const DB_NAME = 'teb-offline';
const DB_VERSION = 1;
const QUEUE_STORE = 'request-queue';

// ─── IndexedDB helpers ───────────────────────────────────────────────────────

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(QUEUE_STORE)) {
        db.createObjectStore(QUEUE_STORE, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function enqueueRequest(method, url, body, headers) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, 'readwrite');
    tx.objectStore(QUEUE_STORE).add({
      method,
      url,
      body,
      headers: Object.fromEntries(
        [...headers].filter(([k]) => k.toLowerCase() !== 'content-length')
      ),
      timestamp: Date.now(),
    });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function getQueuedRequests() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, 'readonly');
    const req = tx.objectStore(QUEUE_STORE).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function deleteQueuedRequest(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, 'readwrite');
    tx.objectStore(QUEUE_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// ─── Replay queued requests ──────────────────────────────────────────────────

async function replayQueue() {
  const queued = await getQueuedRequests();
  if (queued.length === 0) return;

  // Notify all clients about replay
  const clients = await self.clients.matchAll();
  clients.forEach((c) =>
    c.postMessage({ type: 'offline-queue-replay', count: queued.length })
  );

  let replayed = 0;
  let failed = 0;

  for (const item of queued) {
    try {
      const resp = await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body,
      });

      if (resp.status === 409) {
        // Conflict — notify client for manual resolution
        clients.forEach((c) =>
          c.postMessage({
            type: 'offline-queue-conflict',
            request: { method: item.method, url: item.url, timestamp: item.timestamp },
          })
        );
        failed++;
      } else if (resp.ok || resp.status < 500) {
        replayed++;
      } else {
        // Server error — keep in queue for retry
        failed++;
        continue; // Don't delete
      }

      await deleteQueuedRequest(item.id);
    } catch (err) {
      // Network still down — stop replay
      failed++;
      break;
    }
  }

  clients.forEach((c) =>
    c.postMessage({
      type: 'offline-queue-complete',
      replayed,
      failed,
      remaining: queued.length - replayed,
    })
  );
}

// ─── Install — pre-cache static assets ───────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// ─── Activate — clean old caches ─────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
      )
  );
  self.clients.claim();
});

// ─── Fetch — network-first for API, cache-first for static, offline queue for writes

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API requests
  if (url.pathname.includes('/api/')) {
    // Mutating requests (POST/PATCH/PUT/DELETE): queue when offline
    if (event.request.method !== 'GET') {
      event.respondWith(
        fetch(event.request.clone()).catch(async () => {
          // Offline — queue the request
          const body = await event.request.text();
          const headers = event.request.headers;
          await enqueueRequest(event.request.method, event.request.url, body, headers);

          // Return a synthetic 202 Accepted so the UI can continue
          return new Response(
            JSON.stringify({
              queued: true,
              message: 'Request queued for replay when back online',
            }),
            {
              status: 202,
              headers: { 'Content-Type': 'application/json' },
            }
          );
        })
      );
      return;
    }

    // GET API requests: network-first with cache fallback
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      });
    })
  );
});

// ─── Message handler — replay queue on demand ────────────────────────────────

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'replay-queue') {
    replayQueue();
  }
  if (event.data && event.data.type === 'get-queue-status') {
    getQueuedRequests().then((items) => {
      event.source.postMessage({
        type: 'queue-status',
        count: items.length,
        items: items.map((i) => ({
          method: i.method,
          url: i.url,
          timestamp: i.timestamp,
        })),
      });
    });
  }
});

// ─── Online event — auto-replay when connectivity returns ────────────────────
// Note: 'online' event is not available in service workers, but the
// sync API or periodic sync can be used. For now we rely on the
// client posting a 'replay-queue' message when navigator.onLine becomes true.
