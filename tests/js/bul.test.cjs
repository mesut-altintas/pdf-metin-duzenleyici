/* public/static/bul.js — Node'un kendi test aracıyla. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const B = require('../../public/static/bul.js');

test('Türkçe büyük/küçük harf duyarsız', () => {
  assert.deepEqual(B.yerler('İSTANBUL Şubesi', 'istanbul'), [[0, 8]]);
  assert.deepEqual(B.yerler('IŞIK GÜVENLİK', 'ışık'), [[0, 4]]);
  assert.deepEqual(B.yerler('Işık', 'isik'), []);            // ı ≠ i, ş ≠ s
  assert.deepEqual(B.yerler('ISIK', 'isik'), []);            // Türkçede I'nın küçüğü ı
  assert.deepEqual(B.yerler('İzmir', 'izmir'), [[0, 5]]);
});

test('büyük/küçük harfe dikkat', () => {
  const s = { buyukKucuk: true };
  assert.deepEqual(B.yerler('Toplam TOPLAM toplam', 'toplam', s), [[14, 20]]);
  assert.deepEqual(B.yerler('Toplam TOPLAM toplam', 'toplam'), [[0, 6], [7, 13], [14, 20]]);
});

test('tam sözcük: harf ya da rakamın parçası olan eşleşme sayılmaz', () => {
  const s = { tamSozcuk: true };
  assert.deepEqual(B.yerler('2025 ve 20256', '2025', s), [[0, 4]]);
  assert.deepEqual(B.yerler('Ağ ağaç ağ.', 'ağ', s), [[0, 2], [8, 10]]);
  assert.deepEqual(B.yerler('Ağ ağaç ağ.', 'ağ'), [[0, 2], [3, 5], [8, 10]]);
});

test('özel karakterler düz metin sayılır, eşleşmeler çakışmaz', () => {
  assert.deepEqual(B.yerler('1.250,00 TL ve 1x250,00', '1.250,00'), [[0, 8]]);
  assert.deepEqual(B.yerler('aaaa', 'aa'), [[0, 2], [2, 4]]);
  assert.deepEqual(B.yerler('(a+b)*', '(a+b)*'), [[0, 6]]);
});

test('değiştirme: hepsi, boşla silme, eşleşme yoksa aynı', () => {
  assert.equal(B.degistir('Tarih: 14.11.2025 / 14.11.2025', '14.11.2025', '20.12.2025'),
    'Tarih: 20.12.2025 / 20.12.2025');
  assert.equal(B.degistir('ÖRNEK Şirketi A.Ş.', 'örnek', 'Yıldız'), 'Yıldız Şirketi A.Ş.');
  assert.equal(B.degistir('Taslak belge', 'Taslak ', ''), 'belge');
  assert.equal(B.degistir('aynı', 'yok', 'x'), 'aynı');
});

test('iki birimlik karakterler (emoji) konumu bozmaz', () => {
  assert.deepEqual(B.yerler('😀 Toplam', 'toplam'), [[2, 8]]);
  assert.equal(B.degistir('😀 Toplam 😀', 'toplam', 'Tutar'), '😀 Tutar 😀');
});

test('eşleşmeler: yalnız eşleşen parçalar, adet ve yeni hâl', () => {
  const p = [
    { id: 'a', metin: 'Fatura 2025', duzenlenebilir: true },
    { id: 'b', metin: 'Yok', duzenlenebilir: true },
    { id: 'c', metin: '2025 / 2025', duzenlenebilir: false },
  ];
  assert.deepEqual(B.eslesmeler(p, '2025', '2026'), [
    { id: 'a', metin: 'Fatura 2025', adet: 1, duzenlenebilir: true, yeni: 'Fatura 2026' },
    { id: 'c', metin: '2025 / 2025', adet: 2, duzenlenebilir: false, yeni: '2026 / 2026' },
  ]);
  assert.equal(B.eslesmeler(p, '2025', null)[0].yeni, null);   // yalnız arama
  assert.deepEqual(B.eslesmeler(p, '', 'x'), []);
});

test('Türkçe dil verisi yoksa (İ → i̇, iki karakter) konum kaymaz', () => {
  const asil = String.prototype.toLocaleLowerCase;
  // ICU'suz ortam taklidi: kök kurala göre İ iki kod noktasına (i + birleşen nokta) iner.
  String.prototype.toLocaleLowerCase = function () {
    return this.toString() === 'İ' ? 'i̇' : this.toLowerCase();
  };
  try {
    assert.equal(B.degistir('İzmir Şubesi', 'şubesi', 'Bölge'), 'İzmir Bölge');
    assert.deepEqual(B.yerler('İİ Toplam', 'toplam'), [[3, 9]]);
  } finally {
    String.prototype.toLocaleLowerCase = asil;
  }
});
