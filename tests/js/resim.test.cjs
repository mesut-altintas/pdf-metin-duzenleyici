/* public/static/resim.js — node --test */
const test = require('node:test');
const assert = require('node:assert/strict');
const G = require('../../public/static/resim.js');

const yakin = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} ≉ ${b}`);
const sayfada = (k) => assert.ok(k.x >= -1e-12 && k.y >= -1e-12 && k.x + k.w <= 1 + 1e-12 && k.y + k.h <= 1 + 1e-12, JSON.stringify(k));
const oran = (k, oranWH) => (k.w * oranWH) / k.h;   // görünen en-boy oranı (sayfa boyu 1 birim)

test('taşıma sayfadan taşmaz', () => {
  const k = { x: 0.1, y: 0.1, w: 0.3, h: 0.2 };
  assert.deepEqual(G.tasi(k, 0.1, 0.1), { x: 0.2, y: 0.2, w: 0.3, h: 0.2 });
  const t = G.tasi(k, 5, -5);
  yakin(t.x, 0.7); yakin(t.y, 0); sayfada(t);
});

test('köşe tutamacı oranı korur, karşı köşe yerinde kalır', () => {
  const k = { x: 0.2, y: 0.3, w: 0.2, h: 0.1 };
  const oranWH = 0.7;
  for (const [t, dx, dy] of [['sag-alt', 0.1, 0.01], ['sol-ust', -0.05, -0.2], ['sag-ust', 0.02, 0.03], ['sol-alt', 0.1, 0]]) {
    const y = G.boyutla(k, t, dx, dy);
    yakin(oran(y, oranWH), oran(k, oranWH));
    sayfada(y);
    if (t === 'sag-alt') { yakin(y.x, k.x); yakin(y.y, k.y); }
    if (t === 'sol-ust') { yakin(y.x + y.w, k.x + k.w); yakin(y.y + y.h, k.y + k.h); }
    if (t === 'sag-ust') { yakin(y.x, k.x); yakin(y.y + y.h, k.y + k.h); }
    if (t === 'sol-alt') { yakin(y.x + y.w, k.x + k.w); yakin(y.y, k.y); }
  }
  const b = G.boyutla(k, 'sag-alt', 0.1, 0);
  yakin(b.w, 0.3); yakin(b.h, 0.15);           // en 0,2→0,3: boy da aynı ölçekte
});

test('köşe sayfa kenarında ve en küçük boyutta durur, oran bozulmaz', () => {
  const k = { x: 0.6, y: 0.6, w: 0.2, h: 0.1 };
  const y = G.boyutla(k, 'sag-alt', 5, 5);
  sayfada(y); yakin(y.w / y.h, 2); yakin(y.x + y.w, 1);
  const kucuk = G.boyutla(k, 'sag-alt', -5, -5);
  yakin(kucuk.w / kucuk.h, 2); assert.ok(kucuk.h >= G.EN_AZ - 1e-12 && kucuk.w >= G.EN_AZ - 1e-12);
});

test('kenar tutamacı yalnız bir boyutu değiştirir, karşı kenar yerinde kalır', () => {
  const k = { x: 0.2, y: 0.3, w: 0.2, h: 0.1 };
  assert.deepEqual(G.boyutla(k, 'sag', 0.1, 0.5), { x: 0.2, y: 0.3, w: 0.30000000000000004, h: 0.1 });
  const s = G.boyutla(k, 'sol', -0.1, 0.5);
  yakin(s.x, 0.1); yakin(s.w, 0.3); yakin(s.y, 0.3); yakin(s.h, 0.1);
  const u = G.boyutla(k, 'ust', 0.5, -0.1);
  yakin(u.y, 0.2); yakin(u.h, 0.2); yakin(u.x, 0.2); yakin(u.w, 0.2);
  const a = G.boyutla(k, 'alt', 0, 5);
  yakin(a.y + a.h, 1); sayfada(a);
  const ters = G.boyutla(k, 'sol', 0.5, 0);    // karşı kenarı geçemez
  yakin(ters.x + ters.w, 0.4); yakin(ters.w, G.EN_AZ);
});

test('ilk yerleştirme: ortalı, oranlı, en çok %30, sayfada', () => {
  const k = G.yerlestir(0.5, 0.5, 2000, 1000, 600, 800);
  yakin(k.w, 0.3); yakin(oran(k, 600 / 800), 2); yakin(k.x + k.w / 2, 0.5); sayfada(k);
  const kucuk = G.yerlestir(0.02, 0.98, 60, 30, 600, 800);
  yakin(kucuk.w, 0.1); sayfada(kucuk);
});

test('90° döndürme görünen en ile boyu değiştirir, merkezi korur', () => {
  const oranWH = 0.75;
  const k = { x: 0.3, y: 0.4, w: 0.2, h: 0.1 };
  const d = G.dondur(k, oranWH);
  yakin(oran(d, oranWH), 1 / oran(k, oranWH));
  yakin(d.x + d.w / 2, 0.4); yakin(d.y + d.h / 2, 0.45); sayfada(d);
  const dort = G.dondur(G.dondur(G.dondur(d, oranWH), oranWH), oranWH);
  yakin(dort.w, k.w); yakin(dort.h, k.h);
});

test('beyaz zemin saydam olur, koyu çizgi kalır; saydamlık alfayı çarpar', () => {
  const v = new Uint8ClampedArray([255, 255, 255, 255, 250, 248, 252, 255, 225, 230, 240, 255, 20, 30, 40, 255]);
  G.beyaziKaldir(v);
  assert.deepEqual(Array.from(v.filter((_, i) => i % 4 === 3)), [0, 0, 128, 255]);
  G.saydamlikUygula(v, 0.5);
  assert.deepEqual(Array.from(v.filter((_, i) => i % 4 === 3)), [0, 0, 64, 128]);
  assert.equal(G.alfaVar(new Uint8ClampedArray([1, 2, 3, 255])), false);
  assert.equal(G.alfaVar(v), true);
});
