const CACHE_NAME = 'mugichat-v2';
const urlsToCache = [
  '/',
  '/offline',
  '/static/style.css',
  '/static/script.js',
  '/static/audio-recorder.js',
  '/static/icons/icon-72x72.png',
  '/static/icons/icon-192x192.png'
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
      event.request.url.includes('/conversation/') ||
      event.request.url.includes('/users/status')) {
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

// Push notification event
self.addEventListener('push', function(event) {
  if (event.data) {
    const payload = event.data.json();
    
    const options = {
      body: payload.body,
      icon: payload.icon || '/static/icons/icon-192x192.png',
      badge: payload.badge || '/static/icons/icon-72x72.png',
      data: payload.data || {},
      vibrate: [200, 100, 200]
    };
    
    event.waitUntil(
      self.registration.showNotification(payload.title, options)
    );
  }
});

// Notification click event
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  
  const conversationId = event.notification.data.conversation_id;
  
  event.waitUntil(
    clients.openWindow(event.notification.data.url || '/')
      .then(windowClient => {
        // Focus on the window and navigate to the conversation
        if (windowClient) {
          windowClient.focus();
          // Send a message to the client to open the specific conversation
          windowClient.postMessage({
            type: 'OPEN_CONVERSATION',
            conversationId: conversationId
          });
        }
      })
  );
});

// Handle messages from the main app
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Example function for background sync (you'll need to implement based on your app)
function sendPendingMessages() {
  // This would check IndexedDB for pending messages and send them
  return Promise.resolve();
}