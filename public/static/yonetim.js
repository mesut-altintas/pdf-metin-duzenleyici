/* Yönetim sayfası: onay soran formlar ve şifre kopyalama.
 * Onay metni HTML'de data-onay özniteliğinde; satır içi JS yok. Dinleyici
 * yakalama evresinde: vazgeçilince başka hiçbir dinleyici çalışmaz. */
(() => {
  'use strict';

  document.addEventListener('submit', (e) => {
    const form = e.target.closest('form[data-onay]');
    if (form && !window.confirm(form.dataset.onay)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener('click', async (e) => {
    const d = e.target.closest('[data-kopyala]');
    if (!d) return;
    const kaynak = document.getElementById(d.dataset.kopyala);
    try {
      await navigator.clipboard.writeText(kaynak.textContent.trim());
      d.textContent = 'Kopyalandı';
    } catch (_) {
      const secim = window.getSelection();
      const aralik = document.createRange();
      aralik.selectNodeContents(kaynak);
      secim.removeAllRanges();
      secim.addRange(aralik);
      d.textContent = 'Seçildi, kopyalayın';
    }
  });

  // Çift gönderimi önle: gönderilen formun düğmesi kilitlenir.
  document.addEventListener('submit', (e) => {
    const dugme = e.target.querySelector('button[type="submit"]');
    if (dugme) setTimeout(() => { dugme.disabled = true; }, 0);
  });
})();
