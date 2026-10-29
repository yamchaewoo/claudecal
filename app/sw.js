// 앱 화면 파일만 저장해 두는 서비스 워커 — 인터넷이 잠깐 끊겨도 화면은 열리게.
// 항상 새 파일을 먼저 받고(network-first), 안 되면 저장해 둔 걸 쓴다. 구글·Claude 요청은 건드리지 않는다.
const CACHE = "claudecal-v1";
const SHELL = ["./", "app.js", "cal.js", "prompt.js", "manifest.webmanifest", "icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request, {ignoreSearch: true}))   // 공유로 열린 ?text=… 주소도 화면은 같다
  );
});
