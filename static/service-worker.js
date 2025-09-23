const CACHE_NAME = 'mugichat-v4';
const urlsToCache = [
  '/',
  '/static/style.css',
  '/static/icons/icon-72x72.png',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png'
];

// Install event
self.addEventListener('install', event => {
  console.log('Service Worker installing.');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event
self.addEventListener('activate', event => {
  console.log('Service Worker activating.');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            console.log('Deleting old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event
self.addEventListener('fetch', event => {
  // Skip cross-origin requests
  if (!event.request.url.startsWith(self.location.origin)) {
    return;
  }
  
  // For API calls, network first strategy
  if (event.request.url.includes('/api/') || 
      event.request.url.includes('/messages/') ||
      event.request.url.includes('/conversation/') ||
      event.request.url.includes('/users/status')) {
    
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Cache successful API responses
          if (response.status === 200) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME)
              .then(cache => {
                cache.put(event.request, responseToCache);
              });
          }
          return response;
        })
        .catch(() => {
          // Fall back to cache if network fails
          return caches.match(event.request);
        })
    );
  } else {
    // For static assets, cache first strategy
    event.respondWith(
      caches.match(event.request)
        .then(response => {
          if (response) {
            return response;
          }
          
          return fetch(event.request).then(response => {
            // Check if we received a valid response
            if (!response || response.status !== 200 || response.type !== 'basic') {
              return response;
            }
            
            // Clone the response
            const responseToCache = response.clone();
            
            caches.open(CACHE_NAME)
              .then(cache => {
                cache.put(event.request, responseToCache);
              });
            
            return response;
          });
        })
    );
  }
});

// Push notification event
self.addEventListener('push', function(event) {
  if (!event.data) return;
  
  let data = {};
  try {
    data = event.data.json();
  } catch (e) {
    data = {
      title: 'MugiChat',
      body: 'You have a new message',
      icon: '/static/icons/icon-192x192.png'
    };
  }
  
  const options = {
    body: data.body || 'You have a new message',
    icon: data.icon || '/static/icons/icon-192x192.png',
    badge: '/static/icons/icon-72x72.png',
    data: data.data || { url: '/' },
    vibrate: [100, 50, 100],
    tag: 'mugichat-notification'
  };
  
  event.waitUntil(
    self.registration.showNotification(data.title || 'MugiChat', options)
  );
});

// Notification click event
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  
  event.waitUntil(
    clients.matchAll({type: 'window'}).then(windowClients => {
      // Check if there is already a window/tab open with the target URL
      for (let client of windowClients) {
        if (client.url === event.notification.data.url && 'focus' in client) {
          return client.focus();
        }
      }
      
      // If not, then open the target URL in a new window/tab
      if (clients.openWindow) {
        return clients.openWindow(event.notification.data.url || '/');
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