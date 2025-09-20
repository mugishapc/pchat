const CACHE_NAME = 'mugichat-v1';
const urlsToCache = [
  '/',
  '/offline',
  '/static/style.css',
  '/static/script.js',
  '/static/audio-recorder.js'
];

// Install event
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache);
      })
  );
});

// Fetch event
self.addEventListener('fetch', event => {
  // Handle API requests differently
  if (event.request.url.includes('/api/') || 
      event.request.url.includes('/messages/') ||
      event.request.url.includes('/conversation/')) {
    // For API calls, try network first, then fail
    event.respondWith(
      fetch(event.request)
        .catch(error => {
          return new Response(JSON.stringify({ 
            error: 'Network error', 
            offline: true 
          }), {
            headers: { 'Content-Type': 'application/json' }
          });
        })
    );
  } else {
    // For other resources, try cache first, then network
    event.respondWith(
      caches.match(event.request)
        .then(response => {
          return response || fetch(event.request)
            .catch(error => {
              // If both fail, show offline page for navigation requests
              if (event.request.mode === 'navigate') {
                return caches.match('/offline');
              }
            });
        })
    );
  }
});

// Activate event
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            return caches.delete(cache);
          }
        })
      );
    })
  );
});

// Background sync for messages when coming back online
self.addEventListener('sync', event => {
  if (event.tag === 'send-message') {
    event.waitUntil(sendPendingMessages());
  }
});

// Example function for background sync (you'll need to implement based on your app)
function sendPendingMessages() {
  // This would check IndexedDB for pending messages and send them
  return Promise.resolve();
}