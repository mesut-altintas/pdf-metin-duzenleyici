/* PDF Metin Düzenleyici — servis çalışanı.
 *
 * Yalnız uygulamanın kendi stil / betik / simge dosyalarını tutar. PDF'ler,
 * sayfa görüntüleri, sayfalar ve API yanıtları ASLA önbelleğe alınmaz
 * (kullanıcı verisi; sunucu da no-store gönderir). Bağlantı yokken sayfa
 * isteğine "bağlantı yok" sayfası döner.
 */
const SURUM = 'pdf-duzenle-v5';
const ON_YUKLEME = [
  '/cevrimdisi.html',
  '/static/editor.css',
  '/static/editor.js',
  '/static/liste.js',
  '/static/bul.js',
  '/static/yonetim.js',
  '/static/pwa.js',
  '/static/simge/simge.svg',
  '/static/simge/simge-192.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SURUM)
      .then((c) => c.addAll(ON_YUKLEME))
      .catch((h) => console.warn('[SW] ön yükleme atlandı', h))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((adlar) => Promise.all(adlar.filter((a) => a !== SURUM).map((a) => caches.delete(a))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const istek = e.request;
  if (istek.method !== 'GET') return;
  const url = new URL(istek.url);
  if (url.origin !== self.location.origin) return;

  // Sayfa geçişi: her zaman ağdan; bağlantı yoksa "bağlantı yok" sayfası.
  if (istek.mode === 'navigate') {
    e.respondWith(fetch(istek).catch(() => caches.match('/cevrimdisi.html')));
    return;
  }

  // Kendi statik dosyalarımız: önce ağ (güncel kalsın), olmazsa önbellek.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      fetch(istek)
        .then((yanit) => {
          if (yanit.ok) {
            const kopya = yanit.clone();
            caches.open(SURUM).then((c) => c.put(istek, kopya));
          }
          return yanit;
        })
        .catch(() => caches.match(istek))
    );
  }
  // Geri kalan her şey (API, görüntü verisi): tarayıcının normal yolu, önbellek yok.
});
