/* Yüklenebilir uygulama: servis çalışanı kaydı, "Uygulamayı yükle" düğmesi
 * (Chrome / Edge / Android) ve iPhone-iPad için "Ana Ekrana Ekle" ipucu. */
(() => {
  'use strict';

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .catch((h) => console.warn('[SW] kayıt hatası', h));
    });
  }

  const ayri = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const dugme = document.getElementById('yukle-dugmesi');
  let bekleyen = null;

  function oku(anahtar) {
    try { return window.localStorage.getItem(anahtar); } catch (_) { return null; }
  }
  function yaz(anahtar, deger) {
    try { window.localStorage.setItem(anahtar, deger); } catch (_) { /* özel pencere */ }
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    bekleyen = e;
    if (dugme) dugme.hidden = false;
  });

  if (dugme) {
    dugme.addEventListener('click', async () => {
      if (!bekleyen) return;
      bekleyen.prompt();
      try { await bekleyen.userChoice; } catch (_) { /* geç */ }
      bekleyen = null;
      dugme.hidden = true;
    });
  }

  window.addEventListener('appinstalled', () => {
    if (dugme) dugme.hidden = true;
  });

  // iOS Safari'de yükleme olayı yok: bir kez ipucu gösterilir.
  const ipucu = document.getElementById('ios-ipucu');
  const ua = navigator.userAgent;
  const ios = !/android/i.test(ua) && (/iphone|ipad|ipod/i.test(ua) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1));   // iPadOS kendini Mac gösterir
  if (ipucu && ios && !ayri && !oku('ios-ipucu-kapandi')) {
    ipucu.hidden = false;
    const kapat = ipucu.querySelector('[data-kapat]');
    if (kapat) {
      kapat.addEventListener('click', () => {
        ipucu.hidden = true;
        yaz('ios-ipucu-kapandi', '1');
      });
    }
  }
})();
