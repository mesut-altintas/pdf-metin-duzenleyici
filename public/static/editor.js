/* PDF Metin Düzenleyici — tarayıcı tarafı.
 *
 * PDF yalnız bu sekmenin belleğinde durur (File nesnesi). Sunucuya iki kez
 * gider: açılırken (sayfa görüntüleri + metin parçaları) ve oluştururken
 * (dosya + değişiklik listesi). Sunucu hiçbir şey saklamaz.
 */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const yukle = $('#yukle');
  const AZAMI = Number(yukle.dataset.azami);
  const AZAMI_MB = yukle.dataset.azamiMb;
  const ARA_SINIR = 50;

  let dosya = null;              // File
  let dosyaIzi = '';             // SHA-256'nın ilk 16 hanesi (sunucudaki kullanim.parmak_izi ile aynı)
  let sayfaSayisi = 0;
  let parcalar = new Map();      // id -> {id, t, k, font, boyut, sayfa, duzenlenebilir, neden}
  let degisiklik = new Map();    // id -> {yeni, hiza, font, sigdir}
  let eklemeler = new Map();     // e1.. -> {sayfa, x, y, metin, boyut, renk, font:{tur, ad, bayrak}}
  let eklemeSayac = 0;
  let resimler = new Map();      // r1.. -> {sayfa, x, y, w, h, donus, saydamlik, beyaz, kaynak, surum, pisirilmis}
  let resimSayac = 0;
  let seciliResim = null;
  const sayfaBilgi = new Map();  // sayfa no -> {pt:[gen, yuk], yazi:[...]}
  let belgeFontlari = [];        // [{ad, bayrak}] bütün sayfalardan, en çok kullanılan önce
  const paragraflar = new Map();        // P.. -> {id, t, satir, hiza, k, parcalar, sayfa}
  const parcaPar = new Map();           // parça id -> paragraf id
  let paragrafDegisiklik = new Map();   // P.. -> {yeni, hiza}
  const toplamDegisiklik = () => degisiklik.size + eklemeler.size + paragrafDegisiklik.size + resimler.size;
  const parDuzenleniyor = (id) => parcaPar.has(id) && paragrafDegisiklik.has(parcaPar.get(id));
  const HIZA_ADI = { sol: 'sola dayalı', sag: 'sağa dayalı', orta: 'ortalı', iki: 'iki yana yaslı' };
  let son = null;                // {anahtar, blob, ad, uyarilar}
  let acikId = null;
  let indirildi = true;

  // Dokunmatik cihaz (kaba işaretçi): metin çerçeveleri baştan açık, arama
  // sonucu tek dokunuşla düzenlenir. Dar ekran: yan panel alttan açılır.
  const dokunmatik = window.matchMedia('(pointer: coarse)').matches;
  const darEkran = () => window.matchMedia('(max-width: 900px)').matches;
  let paylasilabilir = false;
  try {
    paylasilabilir = !!(navigator.canShare &&
      navigator.canShare({ files: [new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })] }));
  } catch (_) { paylasilabilir = false; }

  const trKucuk = (s) => s.toLocaleLowerCase('tr');

  function durum(el, metin, hata = false) {
    el.hidden = !metin;
    el.textContent = metin || '';
    el.classList.toggle('hata', !!hata);
  }

  async function istek(url, govde) {
    let yanit;
    try {
      yanit = await fetch(url, {
        method: 'POST',
        body: govde,
        headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
    } catch (e) {
      throw new Error('Sunucuya ulaşılamadı. Bağlantınızı denetleyip yeniden deneyin.');
    }
    if (!yanit.ok) {
      let mesaj = `Sunucu hatası (${yanit.status}).`;
      let j = null;
      try {
        j = await yanit.json();
        if (j && j.hata) mesaj = j.hata;
      } catch (_) {
        if (yanit.status === 413) mesaj = `Dosya çok büyük. Üst sınır ${AZAMI_MB} MB.`;
      }
      if (yanit.status === 401) {
        indirildi = true;
        setTimeout(() => { window.location.href = '/giris'; }, 1500);
      }
      const hata = new Error(mesaj);
      if (j && j.kod === 'kota') hata.kota = j.kota;
      throw hata;
    }
    return yanit;
  }

  // Üretim hatası. Kota dolduysa açıklama düğmelerin üstündeki kutuda; burada kısa not
  // (aynı uzun metin iki kez görünmesin). Değişiklikler yerinde kalır.
  function uretimHatasi(d, e, olmayan = 'PDF oluşturulmadı') {
    if (e.kota) {
      kotaGoster(e.kota);
      durum(d, `${olmayan}: bu ayki belge hakkınız doldu.`, true);
    } else {
      durum(d, e.message, true);
    }
  }

  // Aylık belge hakkı (kota.py). k yoksa kotasız: hiçbir şey gösterilmez.
  // Sunucu her üretimde ayrıca denetler; buradaki yalnız bilgi.
  function kotaGoster(k) {
    const not = $('#kota-calisma');
    const uyari = $('#kota-uyari');
    if (!k) { durum(not, ''); durum(uyari, ''); return; }
    if (k.okunamadi) { durum(not, 'Belge hakkınız şu an okunamadı.'); durum(uyari, ''); return; }
    durum(not, `Bu ay ${k.kullanilan} / ${k.sinir} belge`);
    durum(uyari, k.mesaj || '', true);
    // Yükleme sayfasındaki sayı yalnız kotalı kullanıcıda çizilir (isteğe bağlı öğe).
    const ust = document.getElementById('kota-kullanilan');
    if (ust) ust.textContent = k.kullanilan;
  }

  function kotaBaslik(deger) {
    const m = /^(\d+)\/(\d+)$/.exec(deger || '');
    if (m) kotaGoster({ kullanilan: Number(m[1]), sinir: Number(m[2]) });
  }

  // Kullanım ölçümü: indirme ve paylaşma sunucuya uğramaz, buradan bildirilir.
  // Yalnız sayı gider (dosya adı, içerik yok); hata kullanıcıya gösterilmez.
  function olayBildir(tur, ek) {
    try {
      fetch('/api/olay', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ tur }, ek || {})),
        credentials: 'same-origin',
        keepalive: true,
      }).catch(() => {});
    } catch (_) { /* geç */ }
  }

  // ---------- Dosya seçimi ----------

  const birak = $('#birak');
  const girdi = $('#dosya');
  const yukleDurum = $('#yukle-durum');

  girdi.addEventListener('change', () => { if (girdi.files[0]) dosyaAc(girdi.files[0]); });
  ['dragenter', 'dragover'].forEach((t) => birak.addEventListener(t, (e) => {
    e.preventDefault(); birak.classList.add('uzerinde');
  }));
  ['dragleave', 'drop'].forEach((t) => birak.addEventListener(t, (e) => {
    e.preventDefault(); birak.classList.remove('uzerinde');
  }));
  birak.addEventListener('drop', (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) dosyaAc(f);
  });

  async function dosyaAc(f) {
    if (!/\.pdf$/i.test(f.name) && f.type !== 'application/pdf') {
      durum(yukleDurum, 'Yalnız PDF dosyası seçilebilir.', true);
      return;
    }
    if (f.size > AZAMI) {
      durum(yukleDurum, `Dosya çok büyük. Üst sınır ${AZAMI_MB} MB.`, true);
      olayBildir('buyuk_dosya', { boyut: f.size });
      return;
    }
    durum(yukleDurum, 'PDF açılıyor…');
    birak.style.pointerEvents = 'none';
    try {
      const fd = new FormData();
      fd.append('pdf', f, f.name);
      const j = await (await istek('/api/analiz', fd)).json();
      dosya = f;
      eklemeler = new Map();
      resimler = new Map();
      seciliResim = null;
      resimModuAyarla(false);
      paragrafDegisiklik = new Map();
      eklemeModuAyarla(false);
      dosyaIzi = await parmakIzi(f);
      geriAl = null;
      $('#bul-geri').hidden = true;
      durum($('#bul-durum'), '');
      sayfaSayisi = j.sayfalar.length;
      degisiklik = new Map();
      son = null;
      durum($('#liste-durum'), '');
      indirildi = true;
      yakinlik = 0;
      yakinlikUygula();
      yanPanel(false);
      ciz(j);
      kotaGoster(j.kota);
      durum(yukleDurum, '');
    } catch (e) {
      durum(yukleDurum, e.message, true);
    } finally {
      birak.style.pointerEvents = '';
      girdi.value = '';
    }
  }

  $('#baska-dosya').addEventListener('click', () => {
    if (toplamDegisiklik() && !indirildi &&
        !window.confirm('Yaptığınız değişiklikler kaybolacak. Başka PDF açılsın mı?')) return;
    $('#calisma').hidden = true;
    yukle.hidden = false;
    dosya = null;
    degisiklik = new Map();
    eklemeler = new Map();
    paragrafDegisiklik = new Map();
    parcalar = new Map();
    indirildi = true;
    girdi.click();
  });

  // ---------- Sayfaları çizme ----------

  function ciz(j) {
    const kap = $('#sayfalar');
    kap.textContent = '';
    parcalar = new Map();
    paragraflar.clear();
    parcaPar.clear();
    sayfaBilgi.clear();
    let duzenlenebilir = 0;
    const fontlar = new Map();

    j.sayfalar.forEach((s) => {
      sayfaBilgi.set(s.no, { pt: s.pt, yazi: s.yazi || [] });
      (s.yazi || []).forEach((y) => {
        const v = fontlar.get(y.ad);
        if (v) v.harf += y.harf; else fontlar.set(y.ad, { ad: y.ad, bayrak: y.bayrak, harf: y.harf });
      });
      const sayfa = document.createElement('div');
      sayfa.className = 'sayfa';
      const baslik = document.createElement('div');
      baslik.className = 'sayfa-baslik';
      baslik.textContent = `Sayfa ${s.no}`;
      const ic = document.createElement('div');
      ic.className = 'sayfa-ic';
      ic.dataset.no = String(s.no);
      const img = document.createElement('img');
      img.src = s.resim;
      img.alt = `Sayfa ${s.no}`;
      img.width = s.gen; img.height = s.yuk;
      img.addEventListener('load', olcekGuncelle);
      ic.appendChild(img);

      s.parcalar.forEach((p) => {
        p.sayfa = s.no;
        parcalar.set(p.id, p);
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'parca' + (p.duzenlenebilir ? '' : ' kapali');
        b.dataset.id = p.id;
        b.style.left = `${p.k[0] * 100}%`;
        b.style.top = `${p.k[1] * 100}%`;
        b.style.width = `${(p.k[2] - p.k[0]) * 100}%`;
        b.style.height = `${(p.k[3] - p.k[1]) * 100}%`;
        b.title = p.duzenlenebilir ? p.t : `${p.t}\n(${p.neden})`;
        if (p.duzenlenebilir) duzenlenebilir += 1;
        ic.appendChild(b);
      });

      (s.paragraflar || []).forEach((pp) => {
        pp.sayfa = s.no;
        paragraflar.set(pp.id, pp);
        pp.parcalar.forEach((id) => parcaPar.set(id, pp.id));
        const m = document.createElement('button');
        m.type = 'button';
        m.className = 'paragraf-isaret';
        m.dataset.par = pp.id;
        m.style.left = `${pp.k[0] * 100}%`;
        m.style.top = `${pp.k[1] * 100}%`;
        m.style.width = `${(pp.k[2] - pp.k[0]) * 100}%`;
        m.style.height = `${(pp.k[3] - pp.k[1]) * 100}%`;
        m.title = 'Paragraf düzenleniyor; tıklayınca açılır.';
        ic.appendChild(m);
      });

      sayfa.append(baslik, ic);
      kap.appendChild(sayfa);
    });
    belgeFontlari = Array.from(fontlar.values()).sort((a, b) => b.harf - a.harf);

    $('#dosya-adi').textContent = dosya.name;
    yukle.hidden = true;
    $('#calisma').hidden = false;
    $('#ara').value = '';
    araGuncelle();
    listeGuncelle();
    const uy = $('#uyarilar');
    if (!duzenlenebilir) {
      uy.hidden = false;
      uy.className = 'uyari';
      uy.textContent = 'Bu PDF\'te değiştirilebilir metin bulunamadı. Belge taranmış (resim) olabilir; resimdeki yazı bu araçla değiştirilemez.';
    } else {
      uy.hidden = true;
    }
    window.scrollTo(0, 0);
  }

  $('#sayfalar').addEventListener('click', (e) => {
    if (e.target.closest('.ekleme')) return;          // sürükleme / düzenleme kendi dinleyicisinde
    if (e.target.closest('.resim-ek')) return;        // resmin kendi dinleyicisi var
    if (resimModu) { resmiYerlestir(e); return; }
    const isaret = e.target.closest('.paragraf-isaret');
    if (isaret && !eklemeModu) { paragrafPenceresi(isaret.dataset.par); return; }
    if (eklemeModu) {
      const ic = e.target.closest('.sayfa-ic');
      if (!ic) return;
      const r = ic.getBoundingClientRect();
      const kis = (v) => Math.min(1, Math.max(0, v));
      eklemePenceresi(null, { sayfa: Number(ic.dataset.no), x: kis((e.clientX - r.left) / r.width), y: kis((e.clientY - r.top) / r.height) });
      return;
    }
    const b = e.target.closest('.parca');
    if (!b) return;
    const p = parcalar.get(b.dataset.id);
    if (!p) return;
    if (!p.duzenlenebilir) {
      durum($('#islem-durum'), `Bu metin değiştirilemez: ${p.neden}.`, true);
      return;
    }
    pencereAc(p.id);
  });

  // ---------- Düzenleme penceresi ----------

  const pencere = $('#duzenle');
  const yeniGirdi = $('#duzenle-yeni');

  function boyutYaz(n) {
    return (Math.round(n * 10) / 10).toString().replace('.', ',');
  }

  function pencereAc(id) {
    if (parDuzenleniyor(id)) { paragrafPenceresi(parcaPar.get(id)); return; }
    const p = parcalar.get(id);
    const d = degisiklik.get(id);
    acikId = id;
    const pp = parcaPar.has(id) ? paragraflar.get(parcaPar.get(id)) : null;
    const gec = $('#duzenle-paragraf');
    gec.hidden = !pp;
    if (pp) gec.textContent = `Paragrafın tamamını düzenle (${pp.satir} satır)`;
    $('#duzenle-bilgi').textContent = `Sayfa ${p.sayfa} · ${p.font} · ${boyutYaz(p.boyut)} pt`;
    $('#duzenle-asil').textContent = p.t;
    yeniGirdi.value = d ? d.yeni : p.t;
    $('#duzenle-hiza').value = d ? d.hiza : (p.hiza || 'sol');
    $('#duzenle-font').value = d ? d.font : 'otomatik';
    $('#duzenle-sigdir').checked = d ? d.sigdir : false;
    $('#duzenle-kaldir').hidden = !d;
    pencere.showModal();
    yeniGirdi.focus();
    yeniGirdi.select();
  }

  $('#duzenle-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const p = parcalar.get(acikId);
    const yeni = yeniGirdi.value;
    if (yeni === p.t) {
      degisiklik.delete(acikId);
    } else {
      degisiklik.set(acikId, {
        yeni,
        hiza: $('#duzenle-hiza').value,
        font: $('#duzenle-font').value,
        sigdir: $('#duzenle-sigdir').checked,
      });
    }
    pencere.close();
    degisti();
  });
  $('#duzenle-vazgec').addEventListener('click', () => pencere.close());
  $('#duzenle-paragraf').addEventListener('click', () => {
    const pid = parcaPar.get(acikId);
    pencere.close();
    if (pid) paragrafPenceresi(pid);
  });
  $('#duzenle-kaldir').addEventListener('click', () => {
    degisiklik.delete(acikId);
    pencere.close();
    degisti();
  });

  function degisti() {
    indirildi = toplamDegisiklik() === 0;
    durum($('#islem-durum'), '');
    listeGuncelle();
  }

  // ---------- Değişiklik listesi ----------

  function ozetSatiri(p, ekler) {
    const li = document.createElement('li');
    const oz = document.createElement('span');
    oz.className = 'ozet';
    oz.dataset.git = p.id;
    const sno = document.createElement('span');
    sno.className = 'sno';
    sno.textContent = `S${p.sayfa}`;
    oz.appendChild(sno);
    ekler(oz);
    li.appendChild(oz);
    return li;
  }

  function listeGuncelle() {
    const ul = $('#degisiklikler');
    ul.textContent = '';
    document.querySelectorAll('.parca.degisti').forEach((b) => b.classList.remove('degisti'));

    degisiklik.forEach((d, id) => {
      const p = parcalar.get(id);
      const b = document.querySelector(`.parca[data-id="${CSS.escape(id)}"]`);
      if (b) {
        b.classList.add('degisti');
        b.title = `Asıl: ${p.t}\nYeni: ${d.yeni}`;
      }
      const li = ozetSatiri(p, (oz) => {
        const a = document.createElement('span'); a.className = 'asil-m'; a.textContent = p.t;
        const ok = document.createElement('span'); ok.className = 'ok'; ok.textContent = '→';
        const y = document.createElement('span'); y.className = 'yeni-m';
        y.textContent = d.yeni === '' ? '(boş)' : d.yeni;
        oz.append(a, ok, y);
      });
      const kaldir = document.createElement('button');
      kaldir.type = 'button';
      kaldir.className = 'dugme bag';
      kaldir.textContent = 'Kaldır';
      kaldir.dataset.kaldir = id;
      li.appendChild(kaldir);
      ul.appendChild(li);
    });
    // Değişikliği kaldırılan parçanın başlığı asıl metne döner.
    parcalar.forEach((p, id) => {
      if (degisiklik.has(id) || !p.duzenlenebilir) return;
      const b = document.querySelector(`.parca[data-id="${CSS.escape(id)}"]`);
      if (b) b.title = p.t;
    });

    paragrafDegisiklik.forEach((d, pid) => {
      const pp = paragraflar.get(pid);
      const li = document.createElement('li');
      const oz = document.createElement('span');
      oz.className = 'ozet';
      oz.dataset.parGit = pid;
      const sno = document.createElement('span'); sno.className = 'sno'; sno.textContent = `S${pp.sayfa} ¶`;
      const a = document.createElement('span'); a.className = 'asil-m'; a.textContent = kisa(pp.t);
      const ok = document.createElement('span'); ok.className = 'ok'; ok.textContent = '→';
      const y = document.createElement('span'); y.className = 'yeni-m';
      y.textContent = d.yeni === '' ? '(boş)' : kisa(d.yeni);
      oz.append(sno, a, ok, y);
      const k = document.createElement('button');
      k.type = 'button'; k.className = 'dugme bag'; k.textContent = 'Kaldır'; k.dataset.parKaldir = pid;
      li.append(oz, k);
      ul.appendChild(li);
    });
    document.querySelectorAll('.paragraf-isaret').forEach((m) => {
      m.classList.toggle('gorunur', paragrafDegisiklik.has(m.dataset.par));
    });

    eklemeListesiCiz();
    eklemeleriCiz();
    resimListesiCiz();
    resimleriCiz();
    const toplam = toplamDegisiklik();
    $('#sayac').textContent = String(toplam);
    $('#sayac-mobil').textContent = String(toplam);
    $('#bos-not').hidden = toplam > 0;
    $('#indir').disabled = toplam === 0;
    $('#onizle').disabled = toplam === 0;
    $('#paylas').disabled = toplam === 0;
    $('#liste-kaydet').disabled = toplam === 0;
  }

  function parcayaGit(id) {
    const b = document.querySelector(`.parca[data-id="${CSS.escape(id)}"]`);
    if (!b) return;
    b.scrollIntoView({ behavior: 'smooth', block: 'center' });
    b.classList.remove('parla');
    void b.offsetWidth;
    b.classList.add('parla');
  }

  document.querySelector('.yan').addEventListener('click', (e) => {
    const k = e.target.closest('[data-kaldir]');
    if (k) {
      degisiklik.delete(k.dataset.kaldir);
      degisti();
      return;
    }
    const g = e.target.closest('[data-git]');
    if (g) {
      parcayaGit(g.dataset.git);
      if (dokunmatik) {
        yanPanel(false);
        pencereAc(g.dataset.git);
      } else if (e.detail >= 2) {
        pencereAc(g.dataset.git);
      }
    }
  });

  // ---------- Arama, bul ve değiştir ----------
  // Eşleştirme Türkçe kurallarıyla bul.js'de. Arama parçanın şu anki hâlinde
  // yapılır (elle değiştirilmişse yeni metninde). PDF'te parçalara bölünmüş metin
  // (ör. yarısı kalın) tek seferde bulunamaz.

  const ara = $('#ara');
  const yerineGirdi = $('#bul-yerine');
  let degistirModu = false;
  let secilmeyen = new Set();    // değiştirme listesinde işareti kaldırılan parçalar
  let geriAl = null;             // son toplu değiştirmeden önceki hâl: id -> düzeltme | null

  function etkinMetin(p) {
    const d = degisiklik.get(p.id);
    return d ? d.yeni : p.t;
  }

  function aramaDurumu() {
    const sorgu = degistirModu ? ara.value : ara.value.trim();
    const secenek = {
      buyukKucuk: degistirModu && $('#bul-buyuk').checked,
      tamSozcuk: degistirModu && $('#bul-tam').checked,
    };
    const liste = Array.from(parcalar.values()).map((p) => ({
      id: p.id, metin: etkinMetin(p), duzenlenebilir: p.duzenlenebilir && !parDuzenleniyor(p.id),
    }));
    const bulunan = sorgu.trim() ? Bul.eslesmeler(liste, sorgu, degistirModu ? yerineGirdi.value : null, secenek) : [];
    return { sorgu, bulunan };
  }

  function uygulanacak(bulunan) {
    return bulunan.filter((x) => x.duzenlenebilir && !secilmeyen.has(x.id) && x.yeni !== x.metin);
  }

  function araGuncelle() {
    const ul = $('#ara-sonuc');
    const not = $('#ara-not');
    ul.textContent = '';
    document.querySelectorAll('.parca.bulundu').forEach((b) => b.classList.remove('bulundu'));
    const { sorgu, bulunan } = aramaDurumu();
    const uygula = $('#bul-uygula');
    if (!sorgu.trim()) {
      ul.hidden = true; not.hidden = true;
      uygula.disabled = true; uygula.textContent = 'Seçilenleri değiştir';
      return;
    }

    bulunan.forEach((x) => {
      const b = document.querySelector(`.parca[data-id="${CSS.escape(x.id)}"]`);
      if (b) b.classList.add('bulundu');
    });
    bulunan.slice(0, ARA_SINIR).forEach((x) => {
      const p = parcalar.get(x.id);
      const li = ozetSatiri(p, (oz) => {
        const t = document.createElement('span');
        t.textContent = x.metin;
        if (degistirModu) {
          t.className = 'asil-m';
          const ok = document.createElement('span'); ok.className = 'ok'; ok.textContent = '→';
          const y = document.createElement('span');
          y.className = 'yeni-m';
          y.textContent = x.duzenlenebilir ? (x.yeni === '' ? '(boş)' : x.yeni)
            : (p.neden ? `değiştirilemez: ${p.neden}` : 'paragraf olarak düzenleniyor');
          oz.append(t, ok, y);
        } else {
          oz.appendChild(t);
        }
      });
      if (degistirModu) {
        const k = document.createElement('input');
        k.type = 'checkbox';
        k.className = 'bul-sec';
        k.dataset.sec = x.id;
        k.disabled = !x.duzenlenebilir;
        k.checked = x.duzenlenebilir && !secilmeyen.has(x.id);
        k.setAttribute('aria-label', 'Bu eşleşmeyi değiştir');
        li.insertBefore(k, li.firstChild);
      }
      ul.appendChild(li);
    });
    ul.hidden = bulunan.length === 0;
    not.hidden = false;

    const yer = bulunan.reduce((t, x) => t + x.adet, 0);
    const nasil = dokunmatik ? 'Dokununca düzenlenir.' : 'Tıklayınca sayfada gösterilir, çift tıklayınca düzenlenir.';
    if (!bulunan.length) {
      not.textContent = 'Eşleşen metin yok. PDF\'te parçalara bölünmüş metin (ör. yarısı kalın) tek seferde bulunamaz.';
    } else {
      const ozet = `${bulunan.length} metinde ${yer} eşleşme.`;
      const kirp = bulunan.length > ARA_SINIR ? ` İlk ${ARA_SINIR} metin listeleniyor` + (degistirModu ? '; listelenmeyenler de değiştirilir.' : '.') : '';
      not.textContent = `${ozet}${kirp} ${degistirModu ? 'İstemediğiniz eşleşmenin işaretini kaldırın.' : nasil}`;
    }
    const sayi = uygulanacak(bulunan).length;
    uygula.disabled = !degistirModu || sayi === 0;
    uygula.textContent = sayi ? `Seçilenleri değiştir (${sayi})` : 'Seçilenleri değiştir';
  }

  ara.addEventListener('input', () => { secilmeyen = new Set(); araGuncelle(); });
  yerineGirdi.addEventListener('input', araGuncelle);
  $('#bul-buyuk').addEventListener('change', araGuncelle);
  $('#bul-tam').addEventListener('change', araGuncelle);

  $('#degistir-ac').addEventListener('click', () => {
    degistirModu = !degistirModu;
    $('#degistir-alani').hidden = !degistirModu;
    $('#degistir-ac').setAttribute('aria-expanded', degistirModu ? 'true' : 'false');
    $('#degistir-ac').textContent = degistirModu ? 'Yalnız ara' : 'Değiştir…';
    durum($('#bul-durum'), '');
    araGuncelle();
    if (degistirModu) (ara.value ? yerineGirdi : ara).focus();
  });

  $('#ara-sonuc').addEventListener('change', (e) => {
    const k = e.target.closest('[data-sec]');
    if (!k) return;
    if (k.checked) secilmeyen.delete(k.dataset.sec);
    else secilmeyen.add(k.dataset.sec);
    araGuncelle();
  });

  $('#bul-uygula').addEventListener('click', () => {
    const { bulunan } = aramaDurumu();
    const secilen = uygulanacak(bulunan);
    if (!secilen.length) return;
    const onceki = new Map();
    let yer = 0;
    secilen.forEach((x) => {
      const p = parcalar.get(x.id);
      const eski = degisiklik.get(x.id);
      onceki.set(x.id, eski ? Object.assign({}, eski) : null);
      if (x.yeni === p.t) {
        degisiklik.delete(x.id);
      } else {
        degisiklik.set(x.id, {
          yeni: x.yeni,
          hiza: eski ? eski.hiza : (p.hiza || 'sol'),
          font: eski ? eski.font : 'otomatik',
          sigdir: eski ? eski.sigdir : false,
        });
      }
      yer += x.adet;
    });
    geriAl = onceki;
    secilmeyen = new Set();
    degisti();
    araGuncelle();
    $('#bul-geri').hidden = false;
    const bd = $('#bul-durum');
    durum(bd, `${secilen.length} metinde ${yer} yer değiştirildi. Sayfada ve değişiklik listesinde denetleyin.`);
    bd.classList.remove('hata');
    olayBildir('toplu_degistirildi', { duzeltme: secilen.length });
  });

  $('#bul-geri').addEventListener('click', () => {
    if (!geriAl) return;
    geriAl.forEach((d, id) => {
      if (d) degisiklik.set(id, d);
      else degisiklik.delete(id);
    });
    const n = geriAl.size;
    geriAl = null;
    $('#bul-geri').hidden = true;
    degisti();
    araGuncelle();
    durum($('#bul-durum'), `Son toplu değiştirme geri alındı (${n} metin).`);
  });

  // ---------- Oluşturma, indirme, önizleme ----------

  function liste() {
    const l = [];
    degisiklik.forEach((d, id) => {
      l.push({ id, eski: parcalar.get(id).t, yeni: d.yeni, hiza: d.hiza, font: d.font, sigdir: d.sigdir });
    });
    return l;
  }

  function uyarilariGoster(uyarilar) {
    const uy = $('#uyarilar');
    uy.className = 'uyari';
    uy.textContent = '';
    if (!uyarilar || !uyarilar.length) { uy.hidden = true; return; }
    const b = document.createElement('strong');
    b.textContent = 'Dikkat:';
    const ul = document.createElement('ul');
    uyarilar.forEach((m) => { const li = document.createElement('li'); li.textContent = m; ul.appendChild(li); });
    uy.append(b, ul);
    uy.hidden = false;
    if (darEkran()) yanPanel(true);
  }

  function uyariCoz(baslik) {
    if (!baslik) return [];
    try {
      const ikili = atob(baslik);
      const bayt = Uint8Array.from(ikili, (c) => c.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bayt));
    } catch (_) {
      return [];
    }
  }

  function dosyaAdi(yanit, yedek) {
    const cd = yanit.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (m) { try { return decodeURIComponent(m[1]); } catch (_) { /* geç */ } }
    const m2 = cd.match(/filename="?([^";]+)"?/i);
    return m2 ? m2[1] : dosya.name.replace(/\.pdf$/i, '') + (yedek || '-duzenlenmis.pdf');
  }

  function eklemeListesi() {
    return Array.from(eklemeler.values()).map((e) => ({
      sayfa: e.sayfa, x: e.x, y: e.y, metin: e.metin, boyut: e.boyut, renk: e.renk, font: e.font,
    }));
  }

  function paragrafListesi() {
    return Array.from(paragrafDegisiklik.entries()).map(([id, d]) => ({
      id, eski: paragraflar.get(id).t, yeni: d.yeni, hiza: d.hiza,
    }));
  }

  function anahtarUret() {
    return JSON.stringify({ d: liste(), e: eklemeListesi(), p: paragrafListesi(),
      r: Array.from(resimler.entries()).map(([id, r]) => [id, r.sayfa, r.x, r.y, r.w, r.h, pisirmeAnahtari(r)]) });
  }

  // PDF üretimi ve çevirme aynı gövdeyi gönderir: dosya + bütün değişiklikler (pişirilmiş resimler dahil).
  async function govdeHazirla() {
    const fd = new FormData();
    fd.append('pdf', dosya, dosya.name);
    fd.append('duzeltmeler', JSON.stringify(liste()));
    fd.append('eklemeler', JSON.stringify(eklemeListesi()));
    fd.append('paragraflar', JSON.stringify(paragrafListesi()));
    // Resimler pişirilip (döndürme, beyaz zemin, saydamlık) ayrı dosya alanlarıyla gider.
    const meta = [];
    let toplam = dosya.size;
    for (const [id, r] of resimler) {
      const pis = await pisir(r);
      meta.push({ id, sayfa: r.sayfa, x: r.x, y: r.y, w: r.w, h: r.h });
      toplam += pis.blob.size;
    }
    if (toplam > AZAMI) {
      throw new Error(`PDF ile eklenen resimler birlikte ${mbYaz(toplam)} MB; sunucuya en çok ${AZAMI_MB} MB gönderilebilir. `
        + 'Resim sayısını azaltın ya da daha küçük resim kullanın.');
    }
    fd.append('resimler', JSON.stringify(meta));
    for (const [id, r] of resimler) {
      const b = r.pisirilmis.blob;
      fd.append(`resim_${id}`, b, `${id}.${b.type === 'image/png' ? 'png' : 'jpg'}`);
    }
    return fd;
  }

  async function olustur() {
    const anahtar = anahtarUret();
    if (son && son.anahtar === anahtar) return son;
    const fd = await govdeHazirla();
    const yanit = await istek('/api/uret', fd);
    kotaBaslik(yanit.headers.get('X-Kota'));
    const blob = await yanit.blob();
    son = { anahtar, blob, ad: dosyaAdi(yanit), uyarilar: uyariCoz(yanit.headers.get('X-Uyarilar')) };
    return son;
  }

  // PDF düğmeleri değişiklik ister; çevirme ve "Başka PDF" istemez.
  const DEGISIKLIK_ISTEYEN = ['#indir', '#onizle', '#paylas'];
  function kilit(acik) {
    [...DEGISIKLIK_ISTEYEN, '#baska-dosya', '#cevir-word', '#cevir-excel'].forEach((s) => {
      $(s).disabled = acik || (DEGISIKLIK_ISTEYEN.includes(s) && toplamDegisiklik() === 0);
    });
  }

  function blobIndir(blob, ad) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = ad;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function indir(s) {
    blobIndir(s.blob, s.ad);
    indirildi = true;
    olayBildir('indirildi', { duzeltme: degisiklik.size });
  }

  // ---------- Word / Excel'e çevirme ----------
  // Ekrandaki değişiklikler dahil (sunucu önce işler, sonra çevirir). Aylık belge hakkından
  // sayılır; aynı belge ayda bir kez. Uzun belgede bir iki dakika sürebilir (sunucu ölçümü:
  // 30 sayfalık tablo dolu ekstre Word'e ~1 dk).
  const CEVIRME = {
    word: { ad: 'Word', uzanti: '.docx' },
    excel: { ad: 'Excel', uzanti: '.xlsx' },
  };
  document.querySelectorAll('#cevir-word, #cevir-excel').forEach((dugme) => {
    dugme.addEventListener('click', async () => {
      const bicim = dugme.dataset.bicim;
      const c = CEVIRME[bicim];
      const d = $('#islem-durum');
      kilit(true);
      durum(d, `${c.ad} dosyası hazırlanıyor… Uzun belgede bir iki dakika sürebilir.`);
      try {
        const fd = await govdeHazirla();
        fd.append('bicim', bicim);
        const yanit = await istek('/api/cevir', fd);
        kotaBaslik(yanit.headers.get('X-Kota'));
        const ad = dosyaAdi(yanit, c.uzanti);
        const uyarilar = uyariCoz(yanit.headers.get('X-Uyarilar'));
        blobIndir(await yanit.blob(), ad);
        uyarilariGoster(uyarilar);
        durum(d, `İndirildi: ${ad}`);
      } catch (e) {
        uretimHatasi(d, e, `${c.ad} dosyası oluşturulmadı`);
      } finally {
        kilit(false);
      }
    });
  });

  $('#indir').addEventListener('click', async () => {
    const d = $('#islem-durum');
    kilit(true);
    durum(d, 'PDF oluşturuluyor…');
    try {
      const s = await olustur();
      uyarilariGoster(s.uyarilar);
      indir(s);
      durum(d, `İndirildi: ${s.ad}`);
    } catch (e) {
      uretimHatasi(d, e);
    } finally {
      kilit(false);
    }
  });

  const onizleme = $('#onizleme');
  $('#onizle').addEventListener('click', async () => {
    const d = $('#islem-durum');
    kilit(true);
    durum(d, 'Önizleme hazırlanıyor…');
    try {
      const s = await olustur();
      uyarilariGoster(s.uyarilar);
      const fd = new FormData();
      fd.append('pdf', new File([s.blob], s.ad, { type: 'application/pdf' }), s.ad);
      fd.append('yalniz_resim', '1');
      const j = await (await istek('/api/analiz', fd)).json();
      const kap = $('#onizleme-sayfalar');
      kap.textContent = '';
      j.sayfalar.forEach((sy) => {
        const img = document.createElement('img');
        img.src = sy.resim;
        img.alt = `Sayfa ${sy.no}`;
        img.width = sy.gen; img.height = sy.yuk;
        kap.appendChild(img);
      });
      durum(d, '');
      onizleme.showModal();
    } catch (e) {
      uretimHatasi(d, e);
    } finally {
      kilit(false);
    }
  });
  $('#onizleme-kapat').addEventListener('click', () => onizleme.close());
  $('#onizleme-paylas').addEventListener('click', () => { if (son) paylas(son); });
  $('#onizleme-indir').addEventListener('click', () => {
    if (son) { indir(son); durum($('#islem-durum'), `İndirildi: ${son.ad}`); }
    onizleme.close();
  });

  // ---------- Yakınlaştırma ----------

  const YAKINLIK = [1, 1.5, 2, 3];
  let yakinlik = 0;

  function yakinlikUygula() {
    const kap = $('#sayfalar');
    // Görünen yerin ortası yakınlaştırmadan sonra da ortada kalsın.
    const yatay = kap.scrollWidth ? (kap.scrollLeft + kap.clientWidth / 2) / kap.scrollWidth : 0.5;
    const ust = kap.getBoundingClientRect().top + window.scrollY;
    const dikey = kap.scrollHeight ? (window.scrollY + window.innerHeight / 2 - ust) / kap.scrollHeight : 0;
    kap.style.setProperty('--yakinlik', String(YAKINLIK[yakinlik]));
    olcekGuncelle();
    $('#yakin-oran').textContent = `%${Math.round(YAKINLIK[yakinlik] * 100)}`;
    $('#yakin-azalt').disabled = yakinlik === 0;
    $('#yakin-artir').disabled = yakinlik === YAKINLIK.length - 1;
    kap.scrollLeft = yatay * kap.scrollWidth - kap.clientWidth / 2;
    if (dikey > 0) window.scrollTo(0, ust + dikey * kap.scrollHeight - window.innerHeight / 2);
  }
  $('#yakin-azalt').addEventListener('click', () => { if (yakinlik > 0) { yakinlik -= 1; yakinlikUygula(); } });
  $('#yakin-artir').addEventListener('click', () => { if (yakinlik < YAKINLIK.length - 1) { yakinlik += 1; yakinlikUygula(); } });

  const cizgi = $('#cizgileri-goster');
  cizgi.checked = dokunmatik;
  function cizgiUygula() { $('#sayfalar').classList.toggle('cizgili', cizgi.checked); }
  cizgi.addEventListener('change', cizgiUygula);
  cizgiUygula();
  yakinlikUygula();

  // ---------- Dar ekranda alttan açılan panel ----------

  function yanPanel(acik) {
    $('#yan').classList.toggle('acik', acik);
    $('#yan-ac').setAttribute('aria-expanded', acik ? 'true' : 'false');
  }
  $('#yan-ac').addEventListener('click', () => yanPanel(!$('#yan').classList.contains('acik')));

  // ---------- Paylaş (telefonda dosyayı Dosyalar'a, WhatsApp'a, e-postaya) ----------

  async function paylas(s) {
    try {
      await navigator.share({ files: [new File([s.blob], s.ad, { type: 'application/pdf' })], title: s.ad });
      indirildi = true;
      olayBildir('paylasildi', { duzeltme: degisiklik.size });
      durum($('#islem-durum'), `Paylaşıldı: ${s.ad}`);
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      durum($('#islem-durum'), 'Paylaşılamadı. "PDF\'i oluştur ve indir" ile indirin.', true);
    }
  }

  if (paylasilabilir) {
    $('#paylas').hidden = false;
    $('#onizleme-paylas').hidden = false;
  }
  $('#paylas').addEventListener('click', async () => {
    const d = $('#islem-durum');
    const anahtar = anahtarUret();
    if (son && son.anahtar === anahtar) {
      // Dosya hazır: paylaşım aynı dokunuşta açılmalı (tarayıcı kuralı).
      paylas(son);
      return;
    }
    kilit(true);
    durum(d, 'PDF oluşturuluyor…');
    try {
      const s = await olustur();
      uyarilariGoster(s.uyarilar);
      durum(d, 'Hazır. Paylaşmak için "Paylaş"a yeniden dokunun.');
    } catch (e) {
      uretimHatasi(d, e);
    } finally {
      kilit(false);
    }
  });

  // ---------- Değişiklik listesini kaydet / yükle ----------

  async function parmakIzi(f) {
    try {
      const ozet = await crypto.subtle.digest('SHA-256', await f.arrayBuffer());
      return Array.from(new Uint8Array(ozet)).map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
    } catch (_) {
      return '';   // güvenli olmayan bağlamda subtle yok; eşleştirme metinle yine çalışır
    }
  }

  const SURUM = (document.querySelector('meta[name="uygulama-surumu"]') || {}).content || '';

  $('#liste-kaydet').addEventListener('click', () => {
    if (!toplamDegisiklik()) return;
    const veri = DegisiklikListesi.olustur({
      dosyaAdi: dosya.name, parmakIzi: dosyaIzi, sayfaSayisi, uygulamaSurumu: SURUM,
      parcalar, degisiklik, eklemeler, paragraflar, paragrafDegisiklik, resimler, zaman: new Date().toISOString(),
    });
    const blob = new Blob([JSON.stringify(veri, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = dosya.name.replace(/\.pdf$/i, '') + '-degisiklikler.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    const ekNot = (eklemeler.size ? `, ${eklemeler.size} eklenen metin` : '')
      + (paragrafDegisiklik.size ? `, ${paragrafDegisiklik.size} paragraf` : '')
      + (resimler.size ? `, ${resimler.size} resim` : '');
    durum($('#liste-durum'), `Liste kaydedildi: ${degisiklik.size} değişiklik${ekNot}. Aynı şablondaki bir PDF'e "Liste yükle" ile uygulayabilirsiniz.`);
    $('#liste-durum').classList.remove('hata');
    olayBildir('liste_kaydedildi', { duzeltme: toplamDegisiklik() });
  });

  $('#liste-yukle').addEventListener('click', () => $('#liste-dosya').click());

  $('#liste-dosya').addEventListener('change', async () => {
    const girdi = $('#liste-dosya');
    const f = girdi.files && girdi.files[0];
    girdi.value = '';
    if (!f) return;
    const kutu = $('#liste-durum');
    let sonuc;
    try {
      if (f.size > 30_000_000) throw new Error('Dosya bir değişiklik listesi için çok büyük.');   // resimler base64 içinde
      let veri;
      try { veri = JSON.parse(await f.text()); } catch (_) { throw new Error('Bu dosya bir değişiklik listesi değil.'); }
      sonuc = DegisiklikListesi.dogrula(veri);
      if (sonuc.hata) throw new Error(sonuc.hata);
    } catch (e) {
      kutu.hidden = false;
      kutu.className = 'uyari hata';
      kutu.textContent = e.message;
      if (darEkran()) yanPanel(true);
      return;
    }

    const { eslesen, bulunamayan } = DegisiklikListesi.eslestir(sonuc.liste, Array.from(parcalar.values()));
    let uygulanan = 0;
    let ustuneYazilan = 0;
    let baskaSayfada = 0;
    eslesen.forEach((x) => {
      const p = parcalar.get(x.id);
      if (x.yeni === p.t) return;      // değişiklik gerekmiyor
      if (degisiklik.has(x.id)) ustuneYazilan += 1;
      if (x.yol === 'baska_sayfa') baskaSayfada += 1;
      degisiklik.set(x.id, { yeni: x.yeni, hiza: x.hiza, font: x.font, sigdir: x.sigdir });
      uygulanan += 1;
    });
    let parSayisi = 0;
    const parSonuc = DegisiklikListesi.eslestirParagraf(sonuc.paragraflar, Array.from(paragraflar.values()));
    parSonuc.eslesen.forEach((x) => {
      paragraflar.get(x.id).parcalar.forEach((id) => degisiklik.delete(id));
      paragrafDegisiklik.set(x.id, { yeni: x.yeni, hiza: x.hiza });
      parSayisi += 1;
    });
    parSonuc.bulunamayan.forEach((x) => bulunamayan.push(Object.assign({}, x, { paragraf: true })));
    let eklenen = 0;
    sonuc.eklemeler.forEach((e) => {
      if (e.sayfa > sayfaSayisi) {
        bulunamayan.push({ sayfa: e.sayfa, eski: '', yeni: e.metin, neden: 'bu dosyada o sayfa yok', ekleme: true });
        return;
      }
      eklemeSayac += 1;
      eklemeler.set(`e${eklemeSayac}`, Object.assign({}, e, { font: Object.assign({}, e.font) }));
      eklenen += 1;
    });
    let resimSayisi = 0;
    sonuc.resimler.forEach((r) => {
      if (r.sayfa > sayfaSayisi) {
        bulunamayan.push({ sayfa: r.sayfa, eski: '', yeni: 'resim', neden: 'bu dosyada o sayfa yok', ekleme: true });
        return;
      }
      if (resimler.size >= AZAMI_RESIM) {
        bulunamayan.push({ sayfa: r.sayfa, eski: '', yeni: 'resim', neden: `en çok ${AZAMI_RESIM} resim eklenebilir`, ekleme: true });
        return;
      }
      resimSayac += 1;
      resimler.set(`r${resimSayac}`, Object.assign({}, r, { surum: 1 }));
      resimSayisi += 1;
    });
    degisti();

    kutu.hidden = false;
    kutu.className = bulunamayan.length ? 'uyari' : 'uyari basari';
    kutu.textContent = '';
    const baslik = document.createElement('strong');
    baslik.textContent = `${uygulanan} değişiklik` + (parSayisi ? `, ${parSayisi} paragraf` : '')
      + (eklenen ? `, ${eklenen} eklenen metin` : '') + (resimSayisi ? `, ${resimSayisi} resim` : '') + ' yüklendi.';
    kutu.appendChild(baslik);
    const notlar = [];
    if (sonuc.dosya.parmak_izi && dosyaIzi && sonuc.dosya.parmak_izi !== dosyaIzi) {
      notlar.push(`Liste başka bir dosyadan kaydedilmiş${sonuc.dosya.ad ? ` (${sonuc.dosya.ad})` : ''}; metinlere göre eşleştirildi. Sayfada denetleyin.`);
    }
    if (baskaSayfada) notlar.push(`${baskaSayfada} tanesi listedekinden farklı bir sayfada bulundu.`);
    if (ustuneYazilan) notlar.push(`${ustuneYazilan} tanesi yaptığınız bir değişikliğin üstüne yazıldı.`);
    if (eslesen.length > uygulanan) notlar.push(`${eslesen.length - uygulanan} tanesi bu dosyada zaten yeni hâlindeydi.`);
    notlar.forEach((n) => { const p = document.createElement('p'); p.textContent = n; kutu.appendChild(p); });
    if (bulunamayan.length) {
      const p = document.createElement('p');
      p.textContent = `${bulunamayan.length} tanesi uygulanamadı:`;
      const ul = document.createElement('ul');
      bulunamayan.slice(0, 20).forEach((x) => {
        const li = document.createElement('li');
        li.textContent = x.ekleme
          ? `S${x.sayfa}: eklenen “${kisa(x.yeni.trim())}” (${x.neden})`
          : `S${x.sayfa}${x.paragraf ? ' ¶' : ''}: “${kisa(x.eski.trim())}” → “${kisa(x.yeni.trim())}” (${x.neden})`;
        ul.appendChild(li);
      });
      kutu.append(p, ul);
      if (bulunamayan.length > 20) {
        const k = document.createElement('p');
        k.textContent = `İlk 20 tanesi gösteriliyor; toplam ${bulunamayan.length}.`;
        kutu.appendChild(k);
      }
    }
    if (darEkran()) yanPanel(true);
    olayBildir('liste_yuklendi', { duzeltme: uygulanan + eklenen + parSayisi });
  });

  // ---------- Paragraf düzenleme ----------
  // Motor satırları paragrafın genişliğine yeniden dizer (pdf_duzenle/motor.py).

  let acikPar = null;

  function kisa(t, n = 60) {
    const s = t.replace(/\s+/g, ' ').trim();
    return s.length > n ? `${s.slice(0, n - 1)}…` : s;
  }

  function paragrafPenceresi(pid) {
    const pp = paragraflar.get(pid);
    if (!pp) return;
    acikPar = pid;
    const d = paragrafDegisiklik.get(pid);
    $('#paragraf-metin').value = d ? d.yeni : pp.t;
    $('#paragraf-hiza').value = d ? d.hiza : pp.hiza;
    $('#paragraf-bilgi').textContent = `Sayfa ${pp.sayfa} · ${pp.satir} satır · asıl hizalama: ${HIZA_ADI[pp.hiza]}`;
    const satirDuzeltme = pp.parcalar.filter((id) => degisiklik.has(id)).length;
    const not = $('#paragraf-not');
    not.hidden = !satirDuzeltme;
    not.textContent = satirDuzeltme
      ? `Bu paragrafta ${satirDuzeltme} satır değişikliği var; paragrafı değiştirirseniz onların yerini paragraf değişikliği alır.`
      : '';
    $('#paragraf-kaldir').hidden = !d;
    $('#paragraf-pencere').showModal();
    $('#paragraf-metin').focus();
  }

  $('#paragraf-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const pp = paragraflar.get(acikPar);
    const yeni = $('#paragraf-metin').value.split(/\s+/).filter(Boolean).join(' ');
    const hiza = $('#paragraf-hiza').value;
    if (yeni === pp.t && hiza === pp.hiza) {
      paragrafDegisiklik.delete(acikPar);
    } else {
      pp.parcalar.forEach((id) => degisiklik.delete(id));
      paragrafDegisiklik.set(acikPar, { yeni, hiza });
    }
    $('#paragraf-pencere').close();
    degisti();
  });
  $('#paragraf-vazgec').addEventListener('click', () => $('#paragraf-pencere').close());
  $('#paragraf-kaldir').addEventListener('click', () => {
    paragrafDegisiklik.delete(acikPar);
    $('#paragraf-pencere').close();
    degisti();
  });

  $('#degisiklikler').addEventListener('click', (ev) => {
    const k = ev.target.closest('[data-par-kaldir]');
    if (k) { paragrafDegisiklik.delete(k.dataset.parKaldir); degisti(); return; }
    const g = ev.target.closest('[data-par-git]');
    if (!g) return;
    const m = document.querySelector(`.paragraf-isaret[data-par="${CSS.escape(g.dataset.parGit)}"]`);
    if (m) {
      m.scrollIntoView({ behavior: 'smooth', block: 'center' });
      m.classList.remove('parla'); void m.offsetWidth; m.classList.add('parla');
    }
    if (dokunmatik || ev.detail >= 2) { yanPanel(false); paragrafPenceresi(g.dataset.parGit); }
  });

  // ---------- Yeni metin ekleme ----------
  // Nokta sayfa görüntüsünde oran (sol üst 0,0); y yazının taban çizgisi.
  // Sayfadaki önizleme tarayıcının yazı tipiyle yaklaşık; asıl görünüm Önizle'de.

  const STANDART = [
    ['helv', 'Helvetica'], ['hebo', 'Helvetica Kalın'], ['heit', 'Helvetica Eğik'], ['hebi', 'Helvetica Kalın Eğik'],
    ['tiro', 'Times'], ['tibo', 'Times Kalın'], ['tiit', 'Times Eğik'], ['tibi', 'Times Kalın Eğik'],
    ['cour', 'Courier'], ['cobo', 'Courier Kalın'], ['coit', 'Courier Eğik'], ['cobi', 'Courier Kalın Eğik'],
  ];
  let eklemeModu = false;
  let acikEkleme = null;
  let yeniKonum = null;

  function eklemeModuAyarla(acik) {
    if (acik && resimModu) resimModuAyarla(false);
    eklemeModu = acik;
    $('#sayfalar').classList.toggle('ekleme-modu', acik);
    const d = $('#ekle-modu');
    d.setAttribute('aria-pressed', acik ? 'true' : 'false');
    d.classList.toggle('birincil', acik);
    d.classList.toggle('ikincil', !acik);
    $('#ekle-ipucu').hidden = !acik;
  }
  $('#ekle-modu').addEventListener('click', () => eklemeModuAyarla(!eklemeModu));

  function olcekGuncelle() {
    document.querySelectorAll('.sayfa-ic').forEach((ic) => {
      const bilgi = sayfaBilgi.get(Number(ic.dataset.no));
      const img = ic.querySelector('img');
      if (bilgi && bilgi.pt && img && img.clientWidth) ic.style.setProperty('--olcek', String(img.clientWidth / bilgi.pt[0]));
    });
  }
  window.addEventListener('resize', olcekGuncelle);

  function fontAnahtari(f) { return `${f.tur === 'standart' ? 's' : 'b'}:${f.ad}`; }

  function fontSecenekleriniDoldur(secili) {
    const sec = $('#ekle-font');
    sec.textContent = '';
    if (belgeFontlari.length) {
      const g = document.createElement('optgroup');
      g.label = 'Bu belgede kullanılan';
      belgeFontlari.forEach((f) => {
        const o = document.createElement('option');
        o.value = fontAnahtari({ tur: 'belge', ad: f.ad });
        o.textContent = f.ad;
        g.appendChild(o);
      });
      sec.appendChild(g);
    }
    const g = document.createElement('optgroup');
    g.label = 'Standart';
    STANDART.forEach(([kod, ad]) => {
      const o = document.createElement('option');
      o.value = `s:${kod}`;
      o.textContent = ad;
      g.appendChild(o);
    });
    sec.appendChild(g);
    sec.value = secili;
    if (!sec.value) sec.value = 's:helv';
  }

  function seciliFont() {
    const v = $('#ekle-font').value;
    if (v.startsWith('s:')) return { tur: 'standart', ad: v.slice(2), bayrak: 0 };
    const ad = v.slice(2);
    const f = belgeFontlari.find((x) => x.ad === ad);
    return { tur: 'belge', ad, bayrak: f ? f.bayrak : 0 };
  }

  function eklemePenceresi(id, konum) {
    acikEkleme = id;
    yeniKonum = konum || null;
    const e = id ? eklemeler.get(id) : null;
    const sayfaNo = e ? e.sayfa : konum.sayfa;
    const bilgi = sayfaBilgi.get(sayfaNo) || { yazi: [] };
    const ilk = bilgi.yazi[0] || belgeFontlari[0];
    if (e) {
      fontSecenekleriniDoldur(fontAnahtari(e.font));
      $('#ekle-metin').value = e.metin;
      $('#ekle-boyut').value = String(e.boyut);
      $('#ekle-renk').value = e.renk;
    } else {
      fontSecenekleriniDoldur(ilk ? fontAnahtari({ tur: 'belge', ad: ilk.ad }) : 's:helv');
      $('#ekle-metin').value = '';
      $('#ekle-boyut').value = String(ilk && ilk.boyut ? ilk.boyut : 12);
      $('#ekle-renk').value = ilk && ilk.renk ? ilk.renk : '#000000';
    }
    $('#ekle-baslik').textContent = e ? 'Eklenen metni düzenle' : 'Metin ekle';
    $('#ekle-sil').hidden = !e;
    $('#ekle-bilgi').textContent = `Sayfa ${sayfaNo}. Yazı tıklanan noktadan başlar; sayfada sürükleyerek taşıyabilirsiniz.`;
    durum($('#ekle-hata'), '');
    $('#ekle-pencere').showModal();
    $('#ekle-metin').focus();
  }

  $('#ekle-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const metin = $('#ekle-metin').value.replace(/\r\n?/g, '\n');
    const boyut = Number(String($('#ekle-boyut').value).replace(',', '.'));
    if (!metin.trim()) { durum($('#ekle-hata'), 'Metin yazın.', true); return; }
    if (!(boyut >= 4 && boyut <= 200)) { durum($('#ekle-hata'), 'Boyut 4 ile 200 pt arasında olmalı.', true); return; }
    const veri = { metin, boyut, renk: $('#ekle-renk').value.toLowerCase(), font: seciliFont() };
    if (acikEkleme) {
      Object.assign(eklemeler.get(acikEkleme), veri);
    } else {
      eklemeSayac += 1;
      eklemeler.set(`e${eklemeSayac}`, Object.assign({ sayfa: yeniKonum.sayfa, x: yeniKonum.x, y: yeniKonum.y }, veri));
      eklemeModuAyarla(false);
    }
    $('#ekle-pencere').close();
    degisti();
  });
  $('#ekle-vazgec').addEventListener('click', () => $('#ekle-pencere').close());
  $('#ekle-sil').addEventListener('click', () => {
    if (acikEkleme) eklemeler.delete(acikEkleme);
    $('#ekle-pencere').close();
    degisti();
  });

  function cssFont(f) {
    const ad = f.ad.toLowerCase();
    let aile = 'Helvetica, Arial, sans-serif';
    let kalin = false;
    let egik = false;
    if (f.tur === 'standart') {
      if (ad.startsWith('ti')) aile = '"Times New Roman", Times, serif';
      if (ad.startsWith('co')) aile = '"Courier New", Courier, monospace';
      kalin = ['hebo', 'hebi', 'tibo', 'tibi', 'cobo', 'cobi'].includes(ad);
      egik = ['heit', 'hebi', 'tiit', 'tibi', 'coit', 'cobi'].includes(ad);
    } else {
      const b = f.bayrak || 0;
      if (b & 8 || /courier|mono|consol/.test(ad)) aile = '"Courier New", Courier, monospace';
      else if (b & 4 || /times|georgia|serif|roman|garamond|cambria|book/.test(ad)) aile = 'Georgia, "Times New Roman", serif';
      kalin = !!(b & 16) || /bold|black|heavy/.test(ad);
      egik = !!(b & 2) || /italic|oblique/.test(ad);
    }
    return { aile, kalin, egik };
  }

  function eklemeleriCiz() {
    document.querySelectorAll('.ekleme').forEach((el) => el.remove());
    eklemeler.forEach((e, id) => {
      const ic = document.querySelector(`.sayfa-ic[data-no="${e.sayfa}"]`);
      if (!ic) return;
      const el = document.createElement('div');
      el.className = 'ekleme';
      el.dataset.ek = id;
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.title = 'Sürükleyerek taşıyın; tıklayınca düzenlenir.';
      el.textContent = e.metin;
      const f = cssFont(e.font);
      el.style.left = `${e.x * 100}%`;
      el.style.top = `${e.y * 100}%`;
      el.style.fontSize = `calc(var(--olcek, 1) * ${e.boyut}px)`;
      el.style.fontFamily = f.aile;
      el.style.fontWeight = f.kalin ? '700' : '400';
      el.style.fontStyle = f.egik ? 'italic' : 'normal';
      el.style.color = e.renk;
      ic.appendChild(el);
    });
    olcekGuncelle();
  }

  function eklemeListesiCiz() {
    const ul = $('#eklemeler');
    ul.textContent = '';
    $('#ekleme-baslik').hidden = eklemeler.size === 0;
    eklemeler.forEach((e, id) => {
      const li = document.createElement('li');
      const oz = document.createElement('span');
      oz.className = 'ozet';
      oz.dataset.ekGit = id;
      const sno = document.createElement('span');
      sno.className = 'sno';
      sno.textContent = `S${e.sayfa}`;
      const t = document.createElement('span');
      t.className = 'yeni-m';
      const ilkSatir = e.metin.split('\n')[0];
      t.textContent = `＋ ${ilkSatir}${e.metin.includes('\n') ? ' …' : ''}`;
      oz.append(sno, t);
      const k = document.createElement('button');
      k.type = 'button';
      k.className = 'dugme bag';
      k.textContent = 'Kaldır';
      k.dataset.ekKaldir = id;
      li.append(oz, k);
      ul.appendChild(li);
    });
  }

  $('#eklemeler').addEventListener('click', (ev) => {
    const k = ev.target.closest('[data-ek-kaldir]');
    if (k) { eklemeler.delete(k.dataset.ekKaldir); degisti(); return; }
    const g = ev.target.closest('[data-ek-git]');
    if (!g) return;
    const el = document.querySelector(`.ekleme[data-ek="${CSS.escape(g.dataset.ekGit)}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.remove('parla'); void el.offsetWidth; el.classList.add('parla');
    }
    if (dokunmatik || ev.detail >= 2) { yanPanel(false); eklemePenceresi(g.dataset.ekGit); }
  });

  // Sürükle-bırak (fare ve dokunma): 4 pikselden az hareket tıklama sayılır.
  $('#sayfalar').addEventListener('pointerdown', (ev) => {
    const el = ev.target.closest('.ekleme');
    if (!el || ev.button > 0) return;
    ev.preventDefault();
    const id = el.dataset.ek;
    const e = eklemeler.get(id);
    const r = el.parentElement.getBoundingClientRect();
    const bas = { cx: ev.clientX, cy: ev.clientY, x: e.x, y: e.y };
    let tasindi = false;
    el.setPointerCapture(ev.pointerId);
    const kis = (v) => Math.min(1, Math.max(0, v));
    const hareket = (m) => {
      const dx = m.clientX - bas.cx;
      const dy = m.clientY - bas.cy;
      if (!tasindi && Math.hypot(dx, dy) < 4) return;
      tasindi = true;
      e.x = kis(bas.x + dx / r.width);
      e.y = kis(bas.y + dy / r.height);
      el.style.left = `${e.x * 100}%`;
      el.style.top = `${e.y * 100}%`;
    };
    const birak = () => {
      el.removeEventListener('pointermove', hareket);
      el.removeEventListener('pointerup', birak);
      el.removeEventListener('pointercancel', birak);
      if (tasindi) degisti();
      else eklemePenceresi(id);
    };
    el.addEventListener('pointermove', hareket);
    el.addEventListener('pointerup', birak);
    el.addEventListener('pointercancel', birak);
  });
  $('#sayfalar').addEventListener('keydown', (ev) => {
    const el = ev.target.closest('.ekleme');
    if (el && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); eklemePenceresi(el.dataset.ek); }
  });

  // ---------- Resim ekleme (1.17) ----------
  // Asıl resim tarayıcıda en çok 1600 piksele küçültülür (kaynak). Döndürme, beyaz zemini
  // kaldırma ve saydamlık PDF'e gidecek resme burada işlenir ("pişirme"); ekranda da aynı
  // pişmiş resim görünür. Kutu sayfa oranıyla tutulur (resim.js); sunucu resmi kutuya oturtur.
  const AZAMI_RESIM = 20;                  // motor.AZAMI_RESIM ile aynı
  const RESIM_EN_BUYUK = 1600;
  const RESIM_AZAMI_BAYT = 2_800_000;      // motor.AZAMI_RESIM_BAYT (3 MB) altında
  let resimModu = false;
  let bekleyenKaynak = null;               // yerleştirilmeyi bekleyen yeni resim
  let degistirilecek = null;               // "Resmi değiştir" ile değişecek resmin kimliği
  let acikResim = null;

  const mbYaz = (n) => (n / 1e6).toFixed(1).replace('.', ',');
  const pisirmeAnahtari = (r) => `${r.surum}|${r.donus}|${r.beyaz ? 1 : 0}|${r.saydamlik}`;

  function resimModuAyarla(acik) {
    if (acik && eklemeModu) eklemeModuAyarla(false);
    resimModu = acik;
    $('#sayfalar').classList.toggle('resim-modu', acik);
    $('#resim-ipucu').hidden = !acik;
    const d = $('#resim-ekle');
    d.setAttribute('aria-pressed', acik ? 'true' : 'false');
    d.classList.toggle('birincil', acik);
    d.classList.toggle('ikincil', !acik);
    if (!acik) bekleyenKaynak = null;
  }

  function resimYukle(src) {
    return new Promise((coz, red) => {
      const img = new Image();
      img.onload = () => coz(img);
      img.onerror = () => red(new Error('Resim okunamadı.'));
      img.src = src;
    });
  }

  /* Seçilen dosya → küçültülmüş kaynak (data: adresi). Saydamlığı olan resim PNG kalır. */
  async function resmiOku(f) {
    if (!/^image\/(png|jpeg)$/.test(f.type)) throw new Error('Yalnız PNG ya da JPEG resim eklenebilir.');
    if (f.size > 25_000_000) throw new Error('Resim çok büyük (en çok 25 MB).');
    const url = URL.createObjectURL(f);
    try {
      const img = await resimYukle(url);
      const olcek = Math.min(1, RESIM_EN_BUYUK / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.naturalWidth * olcek));
      c.height = Math.max(1, Math.round(img.naturalHeight * olcek));
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, c.width, c.height);
      const alfa = f.type === 'image/png' && ResimGeo.alfaVar(ctx.getImageData(0, 0, c.width, c.height).data);
      return { kaynak: c.toDataURL(alfa ? 'image/png' : 'image/jpeg', 0.92), gen: c.width, yuk: c.height };
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /* Ayarların işlendiği son resim (Blob + gösterim adresi); aynı ayarda önbellekten. Sunucu
   * sınırını aşarsa küçültülerek yeniden pişirilir. */
  async function pisir(r) {
    const anahtar = pisirmeAnahtari(r);
    if (r.pisirilmis && r.pisirilmis.anahtar === anahtar) return r.pisirilmis;
    const img = await resimYukle(r.kaynak);
    const kaynakPng = r.kaynak.startsWith('data:image/png');
    for (let olcek = 1; ; olcek *= 0.75) {
      const gw = Math.max(1, Math.round(img.naturalWidth * olcek));
      const gh = Math.max(1, Math.round(img.naturalHeight * olcek));
      const dik = r.donus === 90 || r.donus === 270;
      const c = document.createElement('canvas');
      c.width = dik ? gh : gw;
      c.height = dik ? gw : gh;
      const ctx = c.getContext('2d');
      ctx.translate(c.width / 2, c.height / 2);
      ctx.rotate((r.donus * Math.PI) / 180);
      ctx.drawImage(img, -gw / 2, -gh / 2, gw, gh);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      let alfa = false;
      if (r.beyaz || r.saydamlik < 1 || kaynakPng) {
        const v = ctx.getImageData(0, 0, c.width, c.height);
        if (r.beyaz) ResimGeo.beyaziKaldir(v.data);
        ResimGeo.saydamlikUygula(v.data, r.saydamlik);
        ctx.putImageData(v, 0, 0);
        alfa = ResimGeo.alfaVar(v.data);
      }
      const blob = await new Promise((coz) => c.toBlob(coz, alfa ? 'image/png' : 'image/jpeg', 0.9));
      if (!blob) throw new Error('Resim hazırlanamadı.');
      if (blob.size <= RESIM_AZAMI_BAYT) {
        if (r.pisirilmis) URL.revokeObjectURL(r.pisirilmis.url);
        r.pisirilmis = { anahtar, blob, url: URL.createObjectURL(blob) };
        return r.pisirilmis;
      }
      if (olcek < 0.2) throw new Error('Resim çok büyük; daha küçük bir resim seçin.');
    }
  }

  function sayfaOrani(sayfa) {
    const ic = document.querySelector(`.sayfa-ic[data-no="${sayfa}"]`);
    const r = ic && ic.getBoundingClientRect();
    return r && r.height ? r.width / r.height : 1;
  }

  function kutuyuUygula(el, r) {
    el.style.left = `${r.x * 100}%`;
    el.style.top = `${r.y * 100}%`;
    el.style.width = `${r.w * 100}%`;
    el.style.height = `${r.h * 100}%`;
  }

  const TUTAMACLAR = ResimGeo.KOSELER.concat(ResimGeo.KENARLAR);

  function resimleriCiz() {
    document.querySelectorAll('.resim-ek').forEach((el) => el.remove());
    resimler.forEach((r, id) => {
      const ic = document.querySelector(`.sayfa-ic[data-no="${r.sayfa}"]`);
      if (!ic) return;
      const el = document.createElement('div');
      el.className = 'resim-ek' + (id === seciliResim ? ' secili' : '');
      el.dataset.rs = id;
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', 'Eklenen resim: sürükleyerek taşıyın, tıklayınca ayarları açılır');
      kutuyuUygula(el, r);
      const img = document.createElement('img');
      img.alt = '';
      img.draggable = false;
      el.appendChild(img);
      TUTAMACLAR.forEach((t) => {
        const h = document.createElement('span');
        h.className = `tutamac t-${t}`;
        h.dataset.t = t;
        el.appendChild(h);
      });
      ic.appendChild(el);
      const anahtar = pisirmeAnahtari(r);
      pisir(r).then((pis) => { if (pisirmeAnahtari(r) === anahtar) img.src = pis.url; })
        .catch((e) => durum($('#islem-durum'), e.message, true));
    });
  }

  function resimSec(id) {
    seciliResim = id;
    document.querySelectorAll('.resim-ek').forEach((el) => el.classList.toggle('secili', el.dataset.rs === id));
  }

  function resimListesiCiz() {
    const ul = $('#resim-listesi');
    ul.textContent = '';
    $('#resim-baslik').hidden = resimler.size === 0;
    resimler.forEach((r, id) => {
      const li = document.createElement('li');
      const oz = document.createElement('span');
      oz.className = 'ozet';
      oz.dataset.rsGit = id;
      const sno = document.createElement('span');
      sno.className = 'sno';
      sno.textContent = `S${r.sayfa}`;
      const t = document.createElement('span');
      t.className = 'yeni-m';
      const ayar = [r.donus ? `${r.donus}°` : '', r.beyaz ? 'zemin kaldırıldı' : '',
        r.saydamlik < 1 ? `saydamlık %${Math.round((1 - r.saydamlik) * 100)}` : ''].filter(Boolean).join(', ');
      t.textContent = `＋ Resim${ayar ? ` (${ayar})` : ''}`;
      oz.append(sno, t);
      const k = document.createElement('button');
      k.type = 'button';
      k.className = 'dugme bag';
      k.textContent = 'Kaldır';
      k.dataset.rsKaldir = id;
      li.append(oz, k);
      ul.appendChild(li);
    });
  }

  $('#resim-listesi').addEventListener('click', (ev) => {
    const k = ev.target.closest('[data-rs-kaldir]');
    if (k) { resimKaldir(k.dataset.rsKaldir); return; }
    const g = ev.target.closest('[data-rs-git]');
    if (!g) return;
    const el = document.querySelector(`.resim-ek[data-rs="${CSS.escape(g.dataset.rsGit)}"]`);
    if (el) {
      resimSec(g.dataset.rsGit);
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.remove('parla'); void el.offsetWidth; el.classList.add('parla');
    }
    if (dokunmatik || ev.detail >= 2) { yanPanel(false); resimPenceresi(g.dataset.rsGit); }
  });

  function resimKaldir(id) {
    const r = resimler.get(id);
    if (r && r.pisirilmis) URL.revokeObjectURL(r.pisirilmis.url);
    resimler.delete(id);
    if (seciliResim === id) seciliResim = null;
    degisti();
  }

  $('#resim-ekle').addEventListener('click', () => {
    if (resimModu) { resimModuAyarla(false); return; }
    if (resimler.size >= AZAMI_RESIM) { durum($('#islem-durum'), `En çok ${AZAMI_RESIM} resim eklenebilir.`, true); return; }
    degistirilecek = null;
    $('#resim-dosya').click();
  });

  $('#resim-dosya').addEventListener('change', async () => {
    const girdi = $('#resim-dosya');
    const f = girdi.files && girdi.files[0];
    girdi.value = '';
    if (!f) return;
    let yeni;
    try {
      yeni = await resmiOku(f);
    } catch (e) {
      durum(degistirilecek ? $('#resim-hata') : $('#islem-durum'), e.message, true);
      return;
    }
    if (degistirilecek && resimler.has(degistirilecek)) {
      // Aynı yerde, aynı ende; boy yeni resmin oranından. Döndürme sıfırlanır.
      const r = resimler.get(degistirilecek);
      const oranWH = sayfaOrani(r.sayfa);
      const merkezY = r.y + r.h / 2;
      let h = r.w * (yeni.yuk / yeni.gen) * oranWH;
      let w = r.w;
      if (h > 1) { w /= h; h = 1; }
      Object.assign(r, ResimGeo.tasi({ x: r.x, y: merkezY - h / 2, w, h }, 0, 0),
        { kaynak: yeni.kaynak, surum: r.surum + 1, donus: 0 });
      degistirilecek = null;
      resimDegisti(r);
      return;
    }
    resimModuAyarla(true);
    bekleyenKaynak = yeni;
    if (darEkran()) yanPanel(false);
  });

  function resmiYerlestir(e) {
    const ic = e.target.closest('.sayfa-ic');
    if (!ic || !bekleyenKaynak) return;
    const rr = ic.getBoundingClientRect();
    const kutu = ResimGeo.yerlestir((e.clientX - rr.left) / rr.width, (e.clientY - rr.top) / rr.height,
      bekleyenKaynak.gen, bekleyenKaynak.yuk, rr.width, rr.height);
    resimSayac += 1;
    const id = `r${resimSayac}`;
    resimler.set(id, Object.assign({ sayfa: Number(ic.dataset.no), donus: 0, saydamlik: 1, beyaz: false,
      kaynak: bekleyenKaynak.kaynak, surum: 1 }, kutu));
    resimModuAyarla(false);
    seciliResim = id;
    degisti();
  }

  // Sürükleyerek taşıma ve tutamaçla boyutlandırma (fare ve dokunma); 4 pikselden az hareket tıklamadır.
  $('#sayfalar').addEventListener('pointerdown', (ev) => {
    const el = ev.target.closest('.resim-ek');
    if (!el) { if (seciliResim && !ev.target.closest('.tutamac')) resimSec(null); return; }
    if (ev.button > 0) return;
    ev.preventDefault();
    const id = el.dataset.rs;
    const r = resimler.get(id);
    if (!r) return;
    resimSec(id);
    const tut = ev.target.closest('.tutamac');
    const sayfaR = el.parentElement.getBoundingClientRect();
    const bas = { cx: ev.clientX, cy: ev.clientY, kutu: { x: r.x, y: r.y, w: r.w, h: r.h } };
    let oynadi = false;
    el.setPointerCapture(ev.pointerId);
    const hareket = (m) => {
      if (!oynadi && Math.hypot(m.clientX - bas.cx, m.clientY - bas.cy) < 4) return;
      oynadi = true;
      const dx = (m.clientX - bas.cx) / sayfaR.width;
      const dy = (m.clientY - bas.cy) / sayfaR.height;
      Object.assign(r, tut ? ResimGeo.boyutla(bas.kutu, tut.dataset.t, dx, dy) : ResimGeo.tasi(bas.kutu, dx, dy));
      kutuyuUygula(el, r);
    };
    const birak = () => {
      el.removeEventListener('pointermove', hareket);
      el.removeEventListener('pointerup', birak);
      el.removeEventListener('pointercancel', birak);
      if (oynadi) degisti();
      else if (!tut) resimPenceresi(id);
    };
    el.addEventListener('pointermove', hareket);
    el.addEventListener('pointerup', birak);
    el.addEventListener('pointercancel', birak);
  });

  // Klavye: Enter ayarları açar, Delete kaldırır, ok tuşları taşır (Shift ile daha çok).
  $('#sayfalar').addEventListener('keydown', (ev) => {
    const el = ev.target.closest('.resim-ek');
    if (!el) return;
    const id = el.dataset.rs;
    const r = resimler.get(id);
    if (!r) return;
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); resimPenceresi(id); return; }
    if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); resimKaldir(id); return; }
    const adim = ev.shiftKey ? 0.02 : 0.005;
    const yon = { ArrowLeft: [-adim, 0], ArrowRight: [adim, 0], ArrowUp: [0, -adim], ArrowDown: [0, adim] }[ev.key];
    if (!yon) return;
    ev.preventDefault();
    Object.assign(r, ResimGeo.tasi(r, yon[0], yon[1]));
    kutuyuUygula(el, r);
    degisti();
    const yeniEl = document.querySelector(`.resim-ek[data-rs="${CSS.escape(id)}"]`);
    if (yeniEl) yeniEl.focus();
  });

  // ---------- Resim penceresi ----------
  function saydamlikYaz() {
    $('#resim-saydamlik-deger').textContent = `%${$('#resim-saydamlik').value}`;
  }

  function onizlemeGuncelle(r) {
    const img = $('#resim-onizleme');
    const anahtar = pisirmeAnahtari(r);
    pisir(r).then((pis) => { if (pisirmeAnahtari(r) === anahtar) img.src = pis.url; })
      .catch((e) => durum($('#resim-hata'), e.message, true));
  }

  function resimDegisti(r) {
    if (acikResim && resimler.get(acikResim) === r) onizlemeGuncelle(r);
    degisti();
  }

  function resimPenceresi(id) {
    const r = resimler.get(id);
    if (!r) return;
    acikResim = id;
    resimSec(id);
    durum($('#resim-hata'), '');
    $('#resim-bilgi').textContent = `Sayfa ${r.sayfa}`;
    $('#resim-saydamlik').value = String(Math.round((1 - r.saydamlik) * 100));
    saydamlikYaz();
    $('#resim-beyaz').checked = r.beyaz;
    $('#resim-onizleme').removeAttribute('src');
    onizlemeGuncelle(r);
    $('#resim-pencere').showModal();
  }

  function acikDondur(ileri) {
    const r = resimler.get(acikResim);
    if (!r) return;
    r.donus = (r.donus + (ileri ? 90 : 270)) % 360;
    Object.assign(r, ResimGeo.dondur(r, sayfaOrani(r.sayfa)));
    resimDegisti(r);
  }
  $('#resim-saga').addEventListener('click', () => acikDondur(true));
  $('#resim-sola').addEventListener('click', () => acikDondur(false));
  $('#resim-saydamlik').addEventListener('input', saydamlikYaz);
  $('#resim-saydamlik').addEventListener('change', () => {
    const r = resimler.get(acikResim);
    if (!r) return;
    r.saydamlik = Math.max(0.1, 1 - Number($('#resim-saydamlik').value) / 100);
    resimDegisti(r);
  });
  $('#resim-beyaz').addEventListener('change', () => {
    const r = resimler.get(acikResim);
    if (!r) return;
    r.beyaz = $('#resim-beyaz').checked;
    resimDegisti(r);
  });
  $('#resim-degistir').addEventListener('click', () => {
    degistirilecek = acikResim;
    durum($('#resim-hata'), '');
    $('#resim-dosya').click();
  });
  $('#resim-kaldir').addEventListener('click', () => {
    const id = acikResim;
    $('#resim-pencere').close();
    resimKaldir(id);
  });
  $('#resim-form').addEventListener('submit', (ev) => { ev.preventDefault(); $('#resim-pencere').close(); });
  $('#resim-pencere').addEventListener('close', () => { acikResim = null; });

  window.addEventListener('beforeunload', (e) => {
    if (toplamDegisiklik() && !indirildi) { e.preventDefault(); e.returnValue = ''; }
  });
})();
