'use strict';
const CACHE='w1-nexus-desktop-dev50';
const ASSETS=['/assets/styles.css','/assets/app.js','/assets/logo.svg','/assets/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))));
self.addEventListener('fetch',event=>{const url=new URL(event.request.url);if(url.pathname.startsWith('/assets/'))event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request)))});
