/* Eklenen resim: kutu geometrisi ve piksel işlemleri.
 *
 * Saf işlevler (DOM yok): tarayıcıda window.ResimGeo, Node testinde module.exports.
 * Kutu sayfa görüntüsünde oran: { x, y, w, h } (sol üst köşe, 0–1). `oranWH`: sayfanın
 * ekrandaki eni / boyu (piksel). Resmin görünen en-boy oranı = (w · en) / (h · boy).
 *
 * Kullanıcı kararı (26.11.2026): köşe tutamacı oranı korur, kenar tutamacı yalnız eni ya
 * da boyu değiştirir. Kutu hiçbir zaman sayfanın dışına taşmaz.
 */
(function (kok) {
  'use strict';

  const EN_AZ = 0.01;          // kutunun en küçük eni / boyu (sayfanın %1'i)
  const KOSELER = ['sol-ust', 'sag-ust', 'sol-alt', 'sag-alt'];
  const KENARLAR = ['ust', 'alt', 'sol', 'sag'];

  const kis = (v, a, b) => Math.min(b, Math.max(a, v));

  /* Kutuyu sürükleme: sayfadan taşmaz. */
  function tasi(k, dx, dy) {
    return { x: kis(k.x + dx, 0, 1 - k.w), y: kis(k.y + dy, 0, 1 - k.h), w: k.w, h: k.h };
  }

  /* Tutamaçla boyutlandırma. `t`: KOSELER ya da KENARLAR'dan biri; dx, dy sayfa oranı.
   * Köşede ölçek en ve boy için aynıdır, bu yüzden görünen oran sayfa boyutundan bağımsız korunur. */
  function boyutla(k, t, dx, dy) {
    if (KENARLAR.includes(t)) {
      let { x, y, w, h } = k;
      if (t === 'sag') w = kis(k.w + dx, EN_AZ, 1 - k.x);
      if (t === 'alt') h = kis(k.h + dy, EN_AZ, 1 - k.y);
      if (t === 'sol') { const sag = k.x + k.w; x = kis(k.x + dx, 0, sag - EN_AZ); w = sag - x; }
      if (t === 'ust') { const alt = k.y + k.h; y = kis(k.y + dy, 0, alt - EN_AZ); h = alt - y; }
      return { x, y, w, h };
    }
    if (!KOSELER.includes(t)) return Object.assign({}, k);
    const sx = t.startsWith('sag') ? 1 : -1;
    const sy = t.endsWith('alt') ? 1 : -1;
    // Sabit köşe (çapa) karşı köşe; ölçek, sürüklemenin baskın yönünden.
    const capaX = sx > 0 ? k.x : k.x + k.w;
    const capaY = sy > 0 ? k.y : k.y + k.h;
    const olcekX = (k.w + sx * dx) / k.w;
    const olcekY = (k.h + sy * dy) / k.h;
    let s = Math.abs(olcekX - 1) >= Math.abs(olcekY - 1) ? olcekX : olcekY;
    // Sayfa sınırı ve en küçük boyut: ölçek bu aralığa kısılır (oran bozulmaz).
    const yerX = sx > 0 ? 1 - capaX : capaX;
    const yerY = sy > 0 ? 1 - capaY : capaY;
    const enBuyuk = Math.min(yerX / k.w, yerY / k.h);
    const enKucuk = Math.max(EN_AZ / k.w, EN_AZ / k.h);
    s = kis(s, Math.min(enKucuk, enBuyuk), enBuyuk);
    const w = k.w * s;
    const h = k.h * s;
    return { x: sx > 0 ? capaX : capaX - w, y: sy > 0 ? capaY : capaY - h, w, h };
  }

  /* İlk yerleştirme: tıklanan noktaya ortalı; en sayfanın %30'u (resim daha küçükse kendi
   * boyu), oran korunur, sayfadan taşmaz. genPx / yukPx: resmin, sayfaGenPx / sayfaYukPx:
   * sayfanın ekrandaki boyu. */
  function yerlestir(cx, cy, genPx, yukPx, sayfaGenPx, sayfaYukPx) {
    let w = Math.min(0.3, genPx / sayfaGenPx);
    let h = w * (yukPx / genPx) * (sayfaGenPx / sayfaYukPx);
    if (h > 0.6) { w *= 0.6 / h; h = 0.6; }
    return tasi({ x: cx - w / 2, y: cy - h / 2, w, h }, 0, 0);
  }

  /* 90° döndürme: görünen en ile boy yer değiştirir, merkez korunur; sığmazsa küçülür. */
  function dondur(k, oranWH) {
    let w = k.h / oranWH;
    let h = k.w * oranWH;
    const s = Math.min(1, 1 / w, 1 / h);
    w *= s; h *= s;
    const cx = k.x + k.w / 2;
    const cy = k.y + k.h / 2;
    return tasi({ x: cx - w / 2, y: cy - h / 2, w, h }, 0, 0);
  }

  /* RGBA dizisi (yerinde): beyaza yakın zemin saydam olur. Kenarda yumuşak geçiş:
   * en koyu kanal 245 ve üstü tam saydam, 205–245 arası kademeli. */
  function beyaziKaldir(veri) {
    for (let i = 0; i < veri.length; i += 4) {
      const m = Math.min(veri[i], veri[i + 1], veri[i + 2]);
      if (m >= 245) veri[i + 3] = 0;
      else if (m >= 205) veri[i + 3] = Math.round(veri[i + 3] * (245 - m) / 40);
    }
    return veri;
  }

  /* RGBA dizisi (yerinde): saydamlık (0–1) alfa kanalıyla çarpılır. */
  function saydamlikUygula(veri, oran) {
    if (oran >= 1) return veri;
    for (let i = 3; i < veri.length; i += 4) veri[i] = Math.round(veri[i] * oran);
    return veri;
  }

  function alfaVar(veri) {
    for (let i = 3; i < veri.length; i += 4) if (veri[i] < 255) return true;
    return false;
  }

  const disa = { EN_AZ, KOSELER, KENARLAR, tasi, boyutla, yerlestir, dondur, beyaziKaldir, saydamlikUygula, alfaVar };
  if (typeof module !== 'undefined' && module.exports) module.exports = disa;
  else kok.ResimGeo = disa;
})(typeof window !== 'undefined' ? window : this);
