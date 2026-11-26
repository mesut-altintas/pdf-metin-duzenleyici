/* public/static/liste.js — Node'un kendi test aracıyla (node --test). */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const L = require('../../public/static/liste.js');

function parca(id, t, sayfa, k, duzenlenebilir = true) {
  return { id, t, sayfa, k, duzenlenebilir };
}

const ASIL = [
  parca('0-1-0-0', 'Fatura No: 123', 1, [0.1, 0.1, 0.3, 0.12]),
  parca('0-2-0-0', '1.250,00 TL', 1, [0.6, 0.3, 0.7, 0.32]),
  parca('0-3-0-0', '1.250,00 TL', 1, [0.6, 0.5, 0.7, 0.52]),
  parca('1-1-0-0', 'Toplam', 2, [0.1, 0.8, 0.2, 0.82]),
];

function kayit(degisiklikler) {
  const parcalar = new Map(ASIL.map((p) => [p.id, p]));
  return L.olustur({
    dosyaAdi: 'a.pdf', parmakIzi: 'abc', sayfaSayisi: 2, uygulamaSurumu: '1.4', parcalar,
    degisiklik: new Map(degisiklikler), zaman: '2026-11-22T12:00:00Z',
  });
}

test('kaydet → dosya → doğrula → aynı dosyada kimlikle eşleşir', () => {
  const k = kayit([
    ['0-3-0-0', { yeni: '2.000,00 TL', hiza: 'sag', font: 'otomatik', sigdir: false }],
    ['0-1-0-0', { yeni: 'Fatura No: 124', hiza: 'sol', font: 'benzer', sigdir: true }],
  ]);
  assert.equal(k.tur, L.TUR);
  assert.equal(k.degisiklikler[0].id, '0-1-0-0');   // sayfa / konum sırasıyla
  const d = L.dogrula(JSON.parse(JSON.stringify(k)));
  assert.equal(d.hata, undefined);
  assert.equal(d.dosya.parmak_izi, 'abc');
  const e = L.eslestir(d.liste, ASIL);
  assert.deepEqual(e.bulunamayan, []);
  assert.deepEqual(e.eslesen.map((x) => [x.id, x.yol, x.yeni, x.hiza, x.font, x.sigdir]), [
    ['0-1-0-0', 'kimlik', 'Fatura No: 124', 'sol', 'benzer', true],
    ['0-3-0-0', 'kimlik', '2.000,00 TL', 'sag', 'otomatik', false],
  ]);
});

test('aynı şablondan başka dosya: kimlik değişmiş, metin ve konumla eşleşir', () => {
  const k = kayit([['0-3-0-0', { yeni: '9,00 TL', hiza: 'sag', font: 'otomatik', sigdir: false }]]);
  const yeni = [
    parca('0-5-0-0', '1.250,00 TL', 1, [0.6, 0.31, 0.7, 0.33]),
    parca('0-6-0-0', '1.250,00 TL', 1, [0.6, 0.51, 0.7, 0.53]),   // listedekine en yakın
  ];
  const e = L.eslestir(L.dogrula(k).liste, yeni);
  assert.deepEqual(e.eslesen.map((x) => [x.id, x.yol]), [['0-6-0-0', 'metin']]);
});

test('aynı metin iki satırda: iki parça ayrı ayrı eşleşir, biri iki kez kullanılmaz', () => {
  const k = kayit([
    ['0-2-0-0', { yeni: 'A', hiza: 'sol', font: 'otomatik', sigdir: false }],
    ['0-3-0-0', { yeni: 'B', hiza: 'sol', font: 'otomatik', sigdir: false }],
  ]);
  const yeni = [
    parca('x1', '1.250,00 TL', 1, [0.6, 0.3, 0.7, 0.32]),
    parca('x2', '1.250,00 TL', 1, [0.6, 0.5, 0.7, 0.52]),
  ];
  const e = L.eslestir(L.dogrula(k).liste, yeni);
  assert.deepEqual(e.eslesen.map((x) => [x.id, x.yeni]), [['x1', 'A'], ['x2', 'B']]);
});

test('başka sayfada: tekse eşleşir, birden çoksa belirsiz diye eşleşmez', () => {
  const k = kayit([['1-1-0-0', { yeni: 'Genel toplam', hiza: 'sol', font: 'otomatik', sigdir: false }]]);
  const tek = L.eslestir(L.dogrula(k).liste, [parca('2-1-0-0', 'Toplam', 3, [0.1, 0.8, 0.2, 0.82])]);
  assert.deepEqual(tek.eslesen.map((x) => [x.id, x.yol, x.sayfa, x.listedekiSayfa]), [['2-1-0-0', 'baska_sayfa', 3, 2]]);
  const iki = L.eslestir(L.dogrula(k).liste, [
    parca('2-1-0-0', 'Toplam', 3, [0.1, 0.8, 0.2, 0.82]),
    parca('3-1-0-0', 'Toplam', 4, [0.1, 0.8, 0.2, 0.82]),
  ]);
  assert.equal(iki.eslesen.length, 0);
  assert.match(iki.bulunamayan[0].neden, /birden çok/);
});

test('bulunamayan ve değiştirilemez nedenleriyle bildirilir', () => {
  const k = kayit([
    ['0-1-0-0', { yeni: 'x', hiza: 'sol', font: 'otomatik', sigdir: false }],
    ['1-1-0-0', { yeni: 'y', hiza: 'sol', font: 'otomatik', sigdir: false }],
  ]);
  const e = L.eslestir(L.dogrula(k).liste, [parca('0-1-0-0', 'Fatura No: 123', 1, [0, 0, 1, 1], false)]);
  assert.equal(e.eslesen.length, 0);
  assert.deepEqual(e.bulunamayan.map((x) => [x.eski, x.neden]), [
    ['Fatura No: 123', 'bu dosyada değiştirilemez'],
    ['Toplam', 'bu dosyada bulunamadı'],
  ]);
});

test('bozuk ya da yabancı dosya reddedilir', () => {
  const iyi = kayit([['0-1-0-0', { yeni: 'x', hiza: 'sol', font: 'otomatik', sigdir: false }]]);
  const bozuk = (f) => { const c = JSON.parse(JSON.stringify(iyi)); f(c); return L.dogrula(c).hata; };
  assert.match(L.dogrula({ a: 1 }).hata, /değişiklik listesi değil/);
  assert.match(L.dogrula(null).hata, /değişiklik listesi değil/);
  assert.match(bozuk((c) => { c.bicim = 2; }), /başka bir sürümüyle/);
  assert.match(bozuk((c) => { c.degisiklikler = []; }), /değişiklik yok/);
  assert.match(bozuk((c) => { c.degisiklikler[0].hiza = 'yukari'; }), /1\. değişiklik/);
  assert.match(bozuk((c) => { c.degisiklikler[0].sayfa = 0; }), /1\. değişiklik/);
  assert.match(bozuk((c) => { c.degisiklikler[0].yeni = 'a'.repeat(1001); }), /1\. değişiklik/);
  assert.match(bozuk((c) => { c.degisiklikler = Array(501).fill(c.degisiklikler[0]); }), /en fazla 500/);
  assert.match(bozuk((c) => { c.degisiklikler.push(Object.assign({}, c.degisiklikler[0], { yeni: 'z' })); }),
    /2\. değişiklik aynı metni ikinci kez/);
});

test('eklenen metinler: kaydedilir, doğrulanır; eski listeler (eklemesiz) yine okunur', () => {
  const parcalar = new Map(ASIL.map((p) => [p.id, p]));
  const ek = new Map([['e1', { sayfa: 2, x: 0.1, y: 0.9, metin: 'Onaylı\nİmza', boyut: 11, renk: '#CC0000',
    font: { tur: 'belge', ad: 'ArialMT', bayrak: 0 } }]]);
  const k = L.olustur({ dosyaAdi: 'a.pdf', parmakIzi: 'x', sayfaSayisi: 2, uygulamaSurumu: '1.6', parcalar,
    degisiklik: new Map(), eklemeler: ek, zaman: 'z' });
  assert.equal(k.degisiklikler.length, 0);
  const d = L.dogrula(JSON.parse(JSON.stringify(k)));
  assert.equal(d.hata, undefined);
  assert.deepEqual(d.eklemeler, [{ sayfa: 2, x: 0.1, y: 0.9, metin: 'Onaylı\nİmza', boyut: 11, renk: '#cc0000',
    font: { tur: 'belge', ad: 'ArialMT', bayrak: 0 } }]);

  const eski = kayit([['0-1-0-0', { yeni: 'x', hiza: 'sol', font: 'otomatik', sigdir: false }]]);
  delete eski.eklemeler;
  const de = L.dogrula(JSON.parse(JSON.stringify(eski)));
  assert.equal(de.hata, undefined);
  assert.deepEqual(de.eklemeler, []);
});

test('bozuk eklenen metin reddedilir', () => {
  const temel = { sayfa: 1, x: 0.5, y: 0.5, metin: 'a', boyut: 10, renk: '#000000', font: { tur: 'standart', ad: 'helv' } };
  const k = kayit([]);
  const dene = (e) => { const c = JSON.parse(JSON.stringify(k)); c.eklemeler = [Object.assign({}, temel, e)]; return L.dogrula(c).hata; };
  assert.equal(dene({}), undefined);
  for (const bozuk of [{ x: 2 }, { y: -1 }, { metin: '  ' }, { boyut: 1 }, { renk: 'red' }, { sayfa: 0 },
    { font: { tur: 'x', ad: 'helv' } }, { font: { tur: 'standart', ad: '' } }]) {
    assert.match(dene(bozuk), /1\. eklenen metin/, JSON.stringify(bozuk));
  }
});

test('paragraflar: kaydedilir, doğrulanır, metinle eşleşir; eski listeler yine okunur', () => {
  const parcalar = new Map(ASIL.map((p) => [p.id, p]));
  const paragraflar = new Map([['P0-3', { id: 'P0-3', t: 'Eski paragraf metni', sayfa: 1 }]]);
  const k = L.olustur({ dosyaAdi: 'a.pdf', parmakIzi: 'x', sayfaSayisi: 2, uygulamaSurumu: '1.7', parcalar,
    degisiklik: new Map(), eklemeler: new Map(), paragraflar,
    paragrafDegisiklik: new Map([['P0-3', { yeni: 'Yeni paragraf', hiza: 'iki' }]]), zaman: 'z' });
  assert.deepEqual(k.paragraflar, [{ sayfa: 1, eski: 'Eski paragraf metni', yeni: 'Yeni paragraf', hiza: 'iki' }]);
  const d = L.dogrula(JSON.parse(JSON.stringify(k)));
  assert.equal(d.hata, undefined);
  const e = L.eslestirParagraf(d.paragraflar, [
    { id: 'P0-9', t: 'Başka', sayfa: 1 }, { id: 'P1-2', t: 'Eski paragraf metni', sayfa: 2 },
  ]);
  assert.deepEqual(e.eslesen.map((x) => [x.id, x.yeni, x.hiza, x.sayfa]), [['P1-2', 'Yeni paragraf', 'iki', 2]]);
  const yok = L.eslestirParagraf(d.paragraflar, [{ id: 'P0-1', t: 'Başka', sayfa: 1 }]);
  assert.equal(yok.eslesen.length, 0);
  assert.match(yok.bulunamayan[0].neden, /bulunamadı/);
  // Bozuk paragraf
  const c = JSON.parse(JSON.stringify(k)); c.paragraflar[0].hiza = 'yukari';
  assert.match(L.dogrula(c).hata, /1\. paragraf/);
});

test('eklenen resimler listeye girer ve geri okunur; eski liste resimsiz okunur', () => {
  const kaynak = 'data:image/png;base64,iVBORw0KGgo=';
  const resimler = new Map([['r1', { sayfa: 2, x: 0.1, y: 0.2, w: 0.3, h: 0.1, donus: 90, saydamlik: 0.5, beyaz: true, kaynak }]]);
  const veri = JSON.parse(JSON.stringify(L.olustur({
    dosyaAdi: 'a.pdf', parmakIzi: 'x', sayfaSayisi: 2, uygulamaSurumu: '1.17', parcalar: new Map(),
    degisiklik: new Map(), eklemeler: new Map(), resimler, zaman: 't',
  })));
  const s = L.dogrula(veri);
  assert.equal(s.hata, undefined);
  assert.deepEqual(s.resimler, [{ sayfa: 2, x: 0.1, y: 0.2, w: 0.3, h: 0.1, donus: 90, saydamlik: 0.5, beyaz: true, kaynak }]);
  const eski = Object.assign({}, veri, { resimler: undefined, eklemeler: [{ sayfa: 1, x: 0.1, y: 0.1, metin: 'a', boyut: 10, renk: '#000000', font: { tur: 'standart', ad: 'helv' } }] });
  assert.deepEqual(L.dogrula(eski).resimler, []);
});

test('bozuk resim satırı reddedilir', () => {
  const temel = { sayfa: 1, x: 0.1, y: 0.1, w: 0.2, h: 0.2, donus: 0, saydamlik: 1, beyaz: false, kaynak: 'data:image/jpeg;base64,/9j/' };
  const liste = (r) => ({ tur: 'pdf-metin-duzenleyici/degisiklikler', bicim: 1, degisiklikler: [], resimler: [r] });
  assert.equal(L.dogrula(liste(temel)).hata, undefined);
  for (const bozuk of [{ kaynak: 'javascript:alert(1)' }, { kaynak: 'data:image/svg+xml;base64,PHN2Zz4=' },
    { donus: 45 }, { saydamlik: 0 }, { x: 0.9, w: 0.2 }, { beyaz: 'evet' }, { sayfa: 0 }]) {
    assert.match(L.dogrula(liste(Object.assign({}, temel, bozuk))).hata || '', /resim/, JSON.stringify(bozuk));
  }
});
