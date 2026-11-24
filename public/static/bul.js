/* Bul ve değiştir: Türkçeye uygun eşleştirme ve değiştirme.
 *
 * Saf işlevler (DOM yok): tarayıcıda window.Bul, Node testinde module.exports.
 *
 * Büyük/küçük harf duyarsız aramada karşılaştırma Türkçe kurallarıyla yapılır
 * ("İSTANBUL" ~ "istanbul", "IŞIK" ~ "ışık"; "I" ile "i" eşleşmez). Tarayıcının
 * düzenli ifade "i" bayrağı bunu yapmadığı için harf harf karşılaştırılır.
 * Konumlar harf (kod noktası) dizisinin elemanları üzerinde tutulur: her harf
 * ayrı küçültülüp ayrı karşılaştırıldığı için, dil verisi eksik bir ortamda bir
 * harf iki karaktere dönse bile (İ → i̇) eşleşen yer asıl metinde aynı yerdir.
 */
(function (kok) {
  'use strict';

  const HARF_RAKAM = /[\p{L}\p{N}]/u;

  function kucuk(h) {
    // Tek karaktere inmeyen küçültmede harfin kendisi kalır (konum bundan etkilenmez).
    const k = h.toLocaleLowerCase('tr');
    return Array.from(k).length === 1 ? k : h;
  }

  function harfler(metin, buyukKucuk) {
    const dizi = Array.from(metin);
    return buyukKucuk ? dizi : dizi.map(kucuk);
  }

  /* metin içinde sorgunun çakışmayan yerleri: [[baş, son), ...] (harf indeksi) */
  function yerler(metin, sorgu, { buyukKucuk = false, tamSozcuk = false } = {}) {
    if (!sorgu) return [];
    const m = harfler(metin, buyukKucuk);
    const s = harfler(sorgu, buyukKucuk);
    const asil = Array.from(metin);
    const sonuc = [];
    let i = 0;
    while (i + s.length <= m.length) {
      let tutar = true;
      for (let j = 0; j < s.length; j += 1) {
        if (m[i + j] !== s[j]) { tutar = false; break; }
      }
      if (tutar && tamSozcuk) {
        const once = i > 0 ? asil[i - 1] : '';
        const sonra = i + s.length < asil.length ? asil[i + s.length] : '';
        if ((once && HARF_RAKAM.test(once)) || (sonra && HARF_RAKAM.test(sonra))) tutar = false;
      }
      if (tutar) {
        sonuc.push([i, i + s.length]);
        i += s.length;
      } else {
        i += 1;
      }
    }
    return sonuc;
  }

  function degistir(metin, sorgu, yerine, secenek) {
    const y = yerler(metin, sorgu, secenek);
    if (!y.length) return metin;
    const asil = Array.from(metin);
    let sonuc = '';
    let onceki = 0;
    y.forEach(([bas, son]) => {
      sonuc += asil.slice(onceki, bas).join('') + yerine;
      onceki = son;
    });
    return sonuc + asil.slice(onceki).join('');
  }

  /* parcalar: [{ id, metin, duzenlenebilir }] (metin: parçanın şu anki hâli)
   * → [{ id, metin, yeni, adet, duzenlenebilir }] yalnız eşleşenler */
  function eslesmeler(parcalar, sorgu, yerine, secenek) {
    const sonuc = [];
    parcalar.forEach((p) => {
      const adet = yerler(p.metin, sorgu, secenek).length;
      if (!adet) return;
      sonuc.push({
        id: p.id, metin: p.metin, adet, duzenlenebilir: p.duzenlenebilir,
        yeni: yerine === null ? null : degistir(p.metin, sorgu, yerine, secenek),
      });
    });
    return sonuc;
  }

  const disa = { yerler, degistir, eslesmeler };
  if (typeof module !== 'undefined' && module.exports) module.exports = disa;
  else kok.Bul = disa;
})(typeof window !== 'undefined' ? window : this);
