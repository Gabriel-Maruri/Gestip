const CACHE_NAME = 'gestip-v2';
const ASSETS_TO_CACHE = [
  '/static/style.css',
  '/static/chart.min.js',
  '/static/manifest.json',
  '/static/icon.svg',
  '/static/icon-192.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  // Solo interceptar peticiones GET
  if (event.request.method !== 'GET') return;

  // Para recursos estáticos: Cache primero con respaldo de red
  if (event.request.url.includes('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return networkResponse;
        });
      })
    );
  } else {
    // Para navegación HTML y datos dinámicos: Red primero con respaldo de caché
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  }
});
