const CACHE_NAME = 'mugichat-v3';
const OFFLINE_QUEUE = 'offline-message-queue';

// Install event - cache essential resources
self.addEventListener('install', event => {
  console.log('Service Worker installing with offline support');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll([
          '/',
          '/static/style.css',
          '/static/icons/icon-72x72.png',
          '/static/icons/icon-192x192.png',
          '/static/icons/icon-512x512.png',
          '/static/script.js',
          '/static/audio-recorder.js'
        ]);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event
self.addEventListener('activate', event => {
  console.log('Service Worker activating with offline support');
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

// Enhanced fetch event with offline queue support
self.addEventListener('fetch', event => {
  // Skip non-HTTP requests
  if (!event.request.url.startsWith('http')) return;

  // Handle API requests with offline queue
  if (event.request.url.includes('/send_message') || 
      event.request.url.includes('/upload_audio')) {
    
    event.respondWith(
      handleApiRequestWithQueue(event.request)
    );
    return;
  }

  // Handle other API requests with network-first strategy
  if (event.request.url.includes('/api/') || 
      event.request.url.includes('/messages/') ||
      event.request.url.includes('/conversation/') ||
      event.request.url.includes('/users/status')) {
    
    event.respondWith(
      networkFirstStrategy(event.request)
    );
    return;
  }

  // For static assets, use cache-first strategy
  event.respondWith(
    cacheFirstStrategy(event.request)
  );
});

// Network-first strategy for API calls
async function networkFirstStrategy(request) {
  try {
    const networkResponse = await fetch(request);
    
    // Cache successful responses
    if (networkResponse.status === 200) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    // Fall back to cache if network fails
    const cachedResponse = await caches.match(request);
    return cachedResponse || Response.json({ error: 'Offline mode' }, { status: 503 });
  }
}

// Cache-first strategy for static assets
async function cacheFirstStrategy(request) {
  const cachedResponse = await caches.match(request);
  
  if (cachedResponse) {
    return cachedResponse;
  }
  
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.status === 200) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    return new Response('Offline', { 
      status: 503,
      headers: { 'Content-Type': 'text/plain' }
    });
  }
}

// Handle API requests with offline queue
async function handleApiRequestWithQueue(request) {
  // If online, try to send immediately
  if (await isOnline()) {
    try {
      const response = await fetch(request.clone());
      
      if (response.ok) {
        // If successful, process any queued messages
        await processOfflineQueue();
        return response;
      }
      
      throw new Error('Network response was not ok');
    } catch (error) {
      // If network request fails, add to queue
      return await addToOfflineQueue(request);
    }
  } else {
    // If offline, add to queue and return success response
    return await addToOfflineQueue(request);
  }
}

// Check if online
async function isOnline() {
  try {
    const response = await fetch('/api/online-check', {
      method: 'HEAD',
      cache: 'no-cache',
      timeout: 5000
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

// Add request to offline queue
async function addToOfflineQueue(request) {
  const queue = await getOfflineQueue();
  const queueItem = {
    url: request.url,
    method: request.method,
    headers: Object.fromEntries(request.headers.entries()),
    timestamp: Date.now(),
    id: generateId()
  };

  // For POST requests, clone and store the body
  if (request.method === 'POST') {
    const body = await request.clone().text();
    queueItem.body = body;
  }

  queue.push(queueItem);
  await setOfflineQueue(queue);

  // Notify clients about the queued message
  const clients = await self.clients.matchAll();
  clients.forEach(client => {
    client.postMessage({
      type: 'MESSAGE_QUEUED',
      queueLength: queue.length
    });
  });

  // Return success response to the client
  return Response.json({ 
    success: true, 
    queued: true,
    message: 'Message queued for sending when online'
  });
}

// Process offline queue when back online
async function processOfflineQueue() {
  const queue = await getOfflineQueue();
  
  if (queue.length === 0) return;

  const successful = [];
  const failed = [];

  for (const item of queue) {
    try {
      const requestInit = {
        method: item.method,
        headers: item.headers
      };

      if (item.body) {
        requestInit.body = item.body;
      }

      const response = await fetch(item.url, requestInit);
      
      if (response.ok) {
        successful.push(item.id);
      } else {
        failed.push(item.id);
      }
    } catch (error) {
      failed.push(item.id);
    }
  }

  // Remove successful items from queue
  const newQueue = queue.filter(item => !successful.includes(item.id));
  await setOfflineQueue(newQueue);

  // Notify clients about sync results
  const clients = await self.clients.matchAll();
  clients.forEach(client => {
    client.postMessage({
      type: 'QUEUE_SYNC_COMPLETE',
      successful: successful.length,
      failed: failed.length,
      remaining: newQueue.length
    });
  });
}

// Get offline queue from storage
async function getOfflineQueue() {
  try {
    const result = await self.registration.sync.getTags();
    const queueData = localStorage.getItem(OFFLINE_QUEUE);
    return queueData ? JSON.parse(queueData) : [];
  } catch (error) {
    return [];
  }
}

// Set offline queue to storage
async function setOfflineQueue(queue) {
  try {
    localStorage.setItem(OFFLINE_QUEUE, JSON.stringify(queue));
    
    // Register background sync if available
    if ('sync' in self.registration) {
      await self.registration.sync.register('offline-messages');
    }
  } catch (error) {
    console.error('Error saving offline queue:', error);
  }
}

// Generate unique ID for queue items
function generateId() {
  return Date.now().toString(36) + Math.random().toString(36).substr(2);
}

// Background sync event
self.addEventListener('sync', event => {
  if (event.tag === 'offline-messages') {
    console.log('Background sync triggered for offline messages');
    event.waitUntil(processOfflineQueue());
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
    tag: 'mugichat-notification',
    requireInteraction: true
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
      // Check if there is already a window/tab open
      for (let client of windowClients) {
        if (client.url.includes(event.notification.data.url) && 'focus' in client) {
          return client.focus();
        }
      }
      
      // If not, open new window/tab
      if (clients.openWindow) {
        return clients.openWindow(event.notification.data.url || '/');
      }
    })
  );
});

// Handle messages from the main app
self.addEventListener('message', event => {
  switch (event.data.type) {
    case 'SKIP_WAITING':
      self.skipWaiting();
      break;
      
    case 'GET_QUEUE_STATUS':
      getOfflineQueue().then(queue => {
        event.ports[0].postMessage({ queueLength: queue.length });
      });
      break;
      
    case 'SYNC_NOW':
      processOfflineQueue();
      break;
  }
});

// Periodic sync (if supported)
if ('periodicSync' in self.registration) {
  self.addEventListener('periodicsync', event => {
    if (event.tag === 'offline-messages-cleanup') {
      event.waitUntil(cleanupOldQueueItems());
    }
  });
}

// Cleanup old queue items (older than 24 hours)
async function cleanupOldQueueItems() {
  const queue = await getOfflineQueue();
  const now = Date.now();
  const twentyFourHours = 24 * 60 * 60 * 1000;
  
  const filteredQueue = queue.filter(item => 
    (now - item.timestamp) < twentyFourHours
  );
  
  if (filteredQueue.length !== queue.length) {
    await setOfflineQueue(filteredQueue);
  }
}