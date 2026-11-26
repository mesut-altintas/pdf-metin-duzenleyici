/* Değişiklik listesi: kayıt biçimi, doğrulama ve yeni PDF'teki metinlerle eşleştirme.
 *
 * Saf işlevler (DOM yok, ağ yok): tarayıcıda window.DegisiklikListesi, Node
 * testinde module.exports. Liste yalnız kullanıcının cihazında bir dosya olarak
 * durur; sunucuya gönderilmez.
 *
 * Eşleştirme sırası (her parça en fazla bir kez kullanılır):
 *   1. aynı kimlik ve aynı asıl metin (aynı dosya),
 *   2. aynı sayfada aynı asıl metin; birden çoksa konumu en yakın olan,
 *   3. başka sayfada aynı asıl metin, ama yalnız belgede TEK ise (belirsizse eşleşmez).
 * Değiştirilemez parçayla eşleşen satır uygulanmaz, nedeniyle bildirilir.
 */
(function (kok) {
  'use strict';

  const TUR = 'pdf-metin-duzenleyici/degisiklikler';
  const BICIM = 1;
  const AZAMI_DEGISIKLIK = 500;   // motor.AZAMI_DUZELTME ile aynı
  const AZAMI_METIN = 1000;       // motor.AZAMI_METIN ile aynı
  const HIZALAR = ['sol', 'sag', 'orta'];
  const FONTLAR = ['otomatik', 'benzer'];
  const AZAMI_EKLEME = 200;       // motor.AZAMI_EKLEME ile aynı
  const AZAMI_RESIM = 20;         // motor.AZAMI_RESIM ile aynı
  const AZAMI_KAYNAK = 4_200_000; // resmin base64 metni (~3 MB; motor.AZAMI_RESIM_BAYT)
  const KAYNAK_DESENI = /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+=*$/;

  function olustur({ dosyaAdi, parmakIzi, sayfaSayisi, uygulamaSurumu, parcalar, degisiklik, eklemeler,
    paragraflar, paragrafDegisiklik, resimler, zaman }) {
    const liste = [];
    degisiklik.forEach((d, id) => {
      const p = parcalar.get(id);
      if (!p) return;
      liste.push({
        sayfa: p.sayfa, id, konum: p.k, eski: p.t, yeni: d.yeni,
        hiza: d.hiza, font: d.font, sigdir: !!d.sigdir,
      });
    });
    liste.sort((a, b) => a.sayfa - b.sayfa || a.konum[1] - b.konum[1] || a.konum[0] - b.konum[0]);
    const ekler = [];
    (eklemeler || new Map()).forEach((e) => {
      ekler.push({ sayfa: e.sayfa, x: e.x, y: e.y, metin: e.metin, boyut: e.boyut, renk: e.renk,
        font: { tur: e.font.tur, ad: e.font.ad, bayrak: e.font.bayrak || 0 } });
    });
    const pars = [];
    (paragrafDegisiklik || new Map()).forEach((d, id) => {
      const p = paragraflar && paragraflar.get(id);
      if (p) pars.push({ sayfa: p.sayfa, eski: p.t, yeni: d.yeni, hiza: d.hiza });
    });
    return {
      tur: TUR,
      bicim: BICIM,
      uygulama: uygulamaSurumu || '',
      olusturma: zaman || new Date().toISOString(),
      dosya: { ad: dosyaAdi || '', parmak_izi: parmakIzi || '', sayfa: sayfaSayisi || 0 },
      degisiklikler: liste,
      eklemeler: ekler,
      paragraflar: pars,
      // 1.17: eklenen resimler; asıl (küçültülmüş) resim ve ayarlar, yeniden düzenlenebilsin diye.
      resimler: Array.from((resimler || new Map()).values()).map((r) => ({
        sayfa: r.sayfa, x: r.x, y: r.y, w: r.w, h: r.h, donus: r.donus, saydamlik: r.saydamlik,
        beyaz: !!r.beyaz, kaynak: r.kaynak,
      })),
    };
  }

  function resimGecerli(r) {
    return r && typeof r === 'object' && Number.isInteger(r.sayfa) && r.sayfa >= 1
      && [r.x, r.y, r.w, r.h].every(sayiMi) && r.x >= 0 && r.y >= 0 && r.w >= 0.002 && r.h >= 0.002
      && r.x + r.w <= 1.0001 && r.y + r.h <= 1.0001
      && [0, 90, 180, 270].includes(r.donus) && sayiMi(r.saydamlik) && r.saydamlik >= 0.1 && r.saydamlik <= 1
      && typeof r.beyaz === 'boolean'
      && typeof r.kaynak === 'string' && r.kaynak.length <= AZAMI_KAYNAK && KAYNAK_DESENI.test(r.kaynak);
  }

  function eklemeGecerli(e) {
    return e && typeof e === 'object' && Number.isInteger(e.sayfa) && e.sayfa >= 1
      && sayiMi(e.x) && sayiMi(e.y) && e.x >= 0 && e.x <= 1 && e.y >= 0 && e.y <= 1
      && typeof e.metin === 'string' && e.metin.trim() && e.metin.length <= AZAMI_METIN
      && sayiMi(e.boyut) && e.boyut >= 4 && e.boyut <= 200
      && typeof e.renk === 'string' && /^#[0-9a-fA-F]{6}$/.test(e.renk)
      && e.font && typeof e.font === 'object' && ['belge', 'standart'].includes(e.font.tur)
      && typeof e.font.ad === 'string' && e.font.ad.length > 0 && e.font.ad.length <= 120;
  }

  function sayiMi(x) { return typeof x === 'number' && Number.isFinite(x); }

  /* { liste, dosya } ya da { hata } */
  function dogrula(veri) {
    if (!veri || typeof veri !== 'object' || veri.tur !== TUR) {
      return { hata: 'Bu dosya bir değişiklik listesi değil.' };
    }
    if (veri.bicim !== BICIM) {
      return { hata: 'Bu liste uygulamanın başka bir sürümüyle kaydedilmiş; okunamıyor.' };
    }
    const ham = veri.degisiklikler;
    if (!Array.isArray(ham)) return { hata: 'Liste bozuk: değişiklikler okunamadı.' };
    const hamEk = veri.eklemeler === undefined ? [] : veri.eklemeler;   // 1.6'dan önceki listelerde yok
    if (!Array.isArray(hamEk)) return { hata: 'Liste bozuk: eklenen metinler okunamadı.' };
    if (hamEk.length > AZAMI_EKLEME) {
      return { hata: `Listede ${hamEk.length} eklenen metin var; en fazla ${AZAMI_EKLEME} yüklenebilir.` };
    }
    const hamPar = veri.paragraflar === undefined ? [] : veri.paragraflar;   // 1.7'den önceki listelerde yok
    if (!Array.isArray(hamPar)) return { hata: 'Liste bozuk: paragraflar okunamadı.' };
    const hamRes = veri.resimler === undefined ? [] : veri.resimler;          // 1.17'den önceki listelerde yok
    if (!Array.isArray(hamRes)) return { hata: 'Liste bozuk: eklenen resimler okunamadı.' };
    if (hamRes.length > AZAMI_RESIM) {
      return { hata: `Listede ${hamRes.length} resim var; en fazla ${AZAMI_RESIM} yüklenebilir.` };
    }
    if (ham.length === 0 && hamEk.length === 0 && hamPar.length === 0 && hamRes.length === 0) {
      return { hata: 'Listede değişiklik yok.' };
    }
    if (ham.length > AZAMI_DEGISIKLIK) {
      return { hata: `Listede ${ham.length} değişiklik var; en fazla ${AZAMI_DEGISIKLIK} yüklenebilir.` };
    }
    const liste = [];
    const kimlikler = new Set();
    for (let i = 0; i < ham.length; i += 1) {
      const d = ham[i];
      const bozuk = { hata: `Liste bozuk: ${i + 1}. değişiklik okunamadı.` };
      if (!d || typeof d !== 'object') return bozuk;
      if (!Number.isInteger(d.sayfa) || d.sayfa < 1) return bozuk;
      if (typeof d.eski !== 'string' || !d.eski) return bozuk;
      if (typeof d.yeni !== 'string' || d.yeni.length > AZAMI_METIN) return bozuk;
      if (!HIZALAR.includes(d.hiza) || !FONTLAR.includes(d.font)) return bozuk;
      if (d.id !== undefined && typeof d.id !== 'string') return bozuk;
      if (d.id) {
        // Sunucudaki motor da aynı metnin iki kez değiştirilmesini reddeder.
        if (kimlikler.has(d.id)) return { hata: `Liste bozuk: ${i + 1}. değişiklik aynı metni ikinci kez değiştiriyor.` };
        kimlikler.add(d.id);
      }
      const konum = Array.isArray(d.konum) && d.konum.length === 4 && d.konum.every(sayiMi) ? d.konum : null;
      liste.push({
        sayfa: d.sayfa, id: d.id || null, konum, eski: d.eski, yeni: d.yeni,
        hiza: d.hiza, font: d.font, sigdir: d.sigdir === true,
      });
    }
    const eklemeler = [];
    for (let i = 0; i < hamEk.length; i += 1) {
      const e = hamEk[i];
      if (!eklemeGecerli(e)) return { hata: `Liste bozuk: ${i + 1}. eklenen metin okunamadı.` };
      eklemeler.push({
        sayfa: e.sayfa, x: e.x, y: e.y, metin: e.metin, boyut: e.boyut, renk: e.renk.toLowerCase(),
        font: { tur: e.font.tur, ad: e.font.ad, bayrak: Number.isInteger(e.font.bayrak) ? e.font.bayrak : 0 },
      });
    }
    const paragraflar = [];
    for (let i = 0; i < hamPar.length; i += 1) {
      const d = hamPar[i];
      if (!d || typeof d !== 'object' || !Number.isInteger(d.sayfa) || d.sayfa < 1
          || typeof d.eski !== 'string' || !d.eski || typeof d.yeni !== 'string' || d.yeni.length > 10 * AZAMI_METIN
          || !['sol', 'sag', 'orta', 'iki'].includes(d.hiza)) {
        return { hata: `Liste bozuk: ${i + 1}. paragraf okunamadı.` };
      }
      paragraflar.push({ sayfa: d.sayfa, eski: d.eski, yeni: d.yeni, hiza: d.hiza });
    }
    const resimler = [];
    for (let i = 0; i < hamRes.length; i += 1) {
      const r = hamRes[i];
      if (!resimGecerli(r)) return { hata: `Liste bozuk: ${i + 1}. resim okunamadı.` };
      resimler.push({ sayfa: r.sayfa, x: r.x, y: r.y, w: r.w, h: r.h, donus: r.donus, saydamlik: r.saydamlik,
        beyaz: r.beyaz, kaynak: r.kaynak });
    }
    const dosya = veri.dosya && typeof veri.dosya === 'object' ? veri.dosya : {};
    return {
      liste,
      eklemeler,
      paragraflar,
      resimler,
      dosya: {
        ad: typeof dosya.ad === 'string' ? dosya.ad : '',
        parmak_izi: typeof dosya.parmak_izi === 'string' ? dosya.parmak_izi : '',
      },
    };
  }

  function merkez(k) { return [(k[0] + k[2]) / 2, (k[1] + k[3]) / 2]; }

  function enYakin(adaylar, konum) {
    if (!konum || adaylar.length === 1) return adaylar[0];
    const [x, y] = merkez(konum);
    let en = adaylar[0];
    let enUzaklik = Infinity;
    adaylar.forEach((p) => {
      const [px, py] = merkez(p.k);
      const u = (px - x) ** 2 + (py - y) ** 2;
      if (u < enUzaklik) { en = p; enUzaklik = u; }
    });
    return en;
  }

  /* parcalar: [{ id, t, k, sayfa, duzenlenebilir }]
   * → { eslesen: [{ id, yeni, hiza, font, sigdir, yol, sayfa, listedekiSayfa }],
   *     bulunamayan: [{ ...satır, neden }] } */
  function eslestir(liste, parcalar) {
    const kimlikle = new Map(parcalar.map((p) => [p.id, p]));
    const metinle = new Map();
    parcalar.forEach((p) => {
      if (!metinle.has(p.t)) metinle.set(p.t, []);
      metinle.get(p.t).push(p);
    });
    const kullanilan = new Set();
    const eslesen = [];
    const bulunamayan = [];

    liste.forEach((d) => {
      let p = null;
      let yol = null;
      const ayni = d.id ? kimlikle.get(d.id) : null;
      if (ayni && ayni.t === d.eski && !kullanilan.has(ayni.id)) {
        p = ayni; yol = 'kimlik';
      }
      if (!p) {
        const adaylar = (metinle.get(d.eski) || []).filter((x) => !kullanilan.has(x.id));
        const sayfada = adaylar.filter((x) => x.sayfa === d.sayfa);
        if (sayfada.length) {
          p = enYakin(sayfada, d.konum); yol = 'metin';
        } else if (adaylar.length === 1) {
          p = adaylar[0]; yol = 'baska_sayfa';
        } else if (adaylar.length > 1) {
          bulunamayan.push(Object.assign({}, d, { neden: 'başka sayfalarda birden çok kez geçiyor' }));
          return;
        }
      }
      if (!p) {
        bulunamayan.push(Object.assign({}, d, { neden: 'bu dosyada bulunamadı' }));
        return;
      }
      if (!p.duzenlenebilir) {
        bulunamayan.push(Object.assign({}, d, { neden: 'bu dosyada değiştirilemez' }));
        return;
      }
      kullanilan.add(p.id);
      eslesen.push({
        id: p.id, yeni: d.yeni, hiza: d.hiza, font: d.font, sigdir: d.sigdir,
        yol, sayfa: p.sayfa, listedekiSayfa: d.sayfa,
      });
    });
    return { eslesen, bulunamayan };
  }

  /* Paragraf: aynı sayfada aynı metinli paragraf; yoksa belgede tek olan.
   * paragraflar: [{ id, t, sayfa }] */
  function eslestirParagraf(liste, paragraflar) {
    const kullanilan = new Set();
    const eslesen = [];
    const bulunamayan = [];
    liste.forEach((d) => {
      const adaylar = paragraflar.filter((p) => p.t === d.eski && !kullanilan.has(p.id));
      const sayfada = adaylar.filter((p) => p.sayfa === d.sayfa);
      let p = null;
      if (sayfada.length) p = sayfada[0];
      else if (adaylar.length === 1) p = adaylar[0];
      if (!p) {
        bulunamayan.push(Object.assign({}, d, {
          neden: adaylar.length > 1 ? 'başka sayfalarda birden çok kez geçiyor' : 'bu dosyada bulunamadı',
        }));
        return;
      }
      kullanilan.add(p.id);
      eslesen.push({ id: p.id, yeni: d.yeni, hiza: d.hiza, sayfa: p.sayfa, listedekiSayfa: d.sayfa });
    });
    return { eslesen, bulunamayan };
  }

  const disa = { TUR, BICIM, olustur, dogrula, eslestir, eslestirParagraf };
  if (typeof module !== 'undefined' && module.exports) module.exports = disa;
  else kok.DegisiklikListesi = disa;
})(typeof window !== 'undefined' ? window : this);
