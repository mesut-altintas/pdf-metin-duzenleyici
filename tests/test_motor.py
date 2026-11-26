"""Motorun sözleşmesi: yalnız seçilen metin değişir, gerisi yerinde kalır."""
import pymupdf as fitz
import pytest

from pdf_duzenle import motor


# ---------------------------------------------------------------- yardımcılar

def _pdf(ciz, **sayfa):
    doc = fitz.open()
    page = doc.new_page(**sayfa)
    ciz(doc, page)
    return doc.tobytes()


def _parcalar(veri):
    return {p['t']: p for s in motor.analiz(veri)['sayfalar'] for p in s['parcalar']}


def _duzelt(veri, *istekler, **ortak):
    parcalar = _parcalar(veri)
    liste = []
    for eski, yeni, *hiza in istekler:
        p = parcalar[eski]
        liste.append({'id': p['id'], 'eski': eski, 'yeni': yeni,
                      'hiza': hiza[0] if hiza else 'sol', 'font': ortak.get('font', 'otomatik'),
                      'sigdir': ortak.get('sigdir', False)})
    return motor.uygula(veri, liste)


def _harfler(veri, pno=0):
    """[(harf, x, y, font)] boşluksuz."""
    page = fitz.open(stream=veri)[pno]
    sonuc = []
    for p in motor._parcalar(page, pno):
        for h in p.harfler:
            if not h.c.isspace() and not h.sentetik:
                sonuc.append((h.c, round(h.o.x, 1), round(h.o.y, 1), p.font))
    return sonuc


def _metin(veri, pno=0):
    """Sayfa metni harflerin yerine göre: satır satır (taban çizgisi), soldan sağa.
    Düz get_text() içerik akışı sırasını verir; sona eklenen yazı sonda görünür."""
    page = fitz.open(stream=veri)[pno]
    satirlar = {}
    for p in motor._parcalar(page, pno):
        for h in p.harfler:
            satirlar.setdefault(round(h.o.y), []).append(h)
    return '\n'.join(''.join(h.g for h in sorted(s, key=lambda h: h.o.x))
                     for _, s in sorted(satirlar.items()))


def _gomulu_nimbus(doc, page):
    page.insert_font(fontname='F0', fontbuffer=fitz.Font('helv').buffer)


def _alt_kume_belgesi():
    """Alt küme Type0 yazı tipi; ToUnicode bütün glifleri eşliyor ama yalnız
    kullanılanlar çizili (PyMuPDF subset_fonts; ölçüldü)."""
    doc = fitz.open()
    page = doc.new_page()
    _gomulu_nimbus(doc, page)
    page.insert_text((50, 100), 'Fatura No: ABC-2025-0042', fontname='F0', fontsize=12)
    page.insert_text((50, 130), 'Komşu satır yerinde kalmalı', fontname='F0', fontsize=12)
    page.insert_text((300, 160), 'Tutar 1.250,00', fontname='F0', fontsize=12)
    doc.subset_fonts()
    return doc.tobytes()


# ---------------------------------------------------------------- analiz

def test_analiz_parca_resim_ve_gorunum(ornek):
    a = motor.analiz(ornek('chrome-fatura.pdf'))
    s = a['sayfalar'][0]
    assert s['resim'].startswith('data:image/jpeg;base64,')
    assert s['gen'] > 0 and s['yuk'] > 0
    metinler = [p['t'] for p in s['parcalar']]
    assert 'SATIŞ FATURASI' in metinler
    # U+00A0 ekranda düz boşluk
    assert any(t.startswith(' EKS2025000123') and '\xa0' not in t for t in metinler)
    for p in s['parcalar']:
        assert all(0 <= v <= 1 for v in p['k'])
        assert p['duzenlenebilir'] is True


def test_analiz_yalniz_resim(ornek):
    a = motor.analiz(ornek('chrome-fatura.pdf'), yalniz_resim=True)
    assert 'parcalar' not in a['sayfalar'][0]
    assert a['sayfalar'][0]['resim']


def test_aciklama_metni_parca_olarak_gelmez():
    def ciz(doc, page):
        page.insert_text((50, 100), 'Sayfa metni', fontname='helv', fontsize=12)
        page.add_freetext_annot(fitz.Rect(50, 200, 200, 220), 'Açıklama metni')
    veri = _pdf(ciz)
    assert set(_parcalar(veri)) == {'Sayfa metni'}


def test_gorunmez_metin_duzenlenemez():
    def ciz(doc, page):
        page.insert_text((50, 100), 'Görünmez OCR', fontname='helv', fontsize=12, render_mode=3)
    veri = _pdf(ciz)
    p = next(iter(_parcalar(veri).values()))
    assert p['duzenlenebilir'] is False
    with pytest.raises(motor.PdfHatasi, match='değiştirilemez'):
        motor.uygula(veri, [{'id': p['id'], 'eski': p['t'], 'yeni': 'X'}])


def test_dondurulmus_ve_kirpilmis_sayfada_kutu_sayfa_icinde():
    def ciz(doc, page):
        page.insert_text((150, 200), 'Kirpik metin', fontname='helv', fontsize=12)
        page.set_cropbox(fitz.Rect(100, 100, 500, 700))
        page.set_rotation(90)
    veri = _pdf(ciz, width=600, height=800)
    p = _parcalar(veri)['Kirpik metin']
    x0, y0, x1, y1 = p['k']
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    # Döndürülmüş sayfada metin dikey görünür: kutu yüksekliği genişliğinden büyük.
    assert (y1 - y0) * 600 > (x1 - x0) * 400


def test_bozuk_ve_sifreli_dosya():
    with pytest.raises(motor.PdfHatasi):
        motor.analiz(b'%PDF-1.4 bozuk')
    doc = fitz.open()
    doc.new_page().insert_text((50, 100), 'gizli', fontname='helv')
    veri = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='u', owner_pw='o')
    with pytest.raises(motor.PdfHatasi, match='şifreli'):
        motor.analiz(veri)


# ---------------------------------------------------------------- uygulama: dokunulmayanlar

def test_dokunulmayan_harfler_yerinde_ve_ayni_fontta(ornek):
    veri = ornek('chrome-fatura.pdf')
    once = _harfler(veri)
    sonuc, _ = _duzelt(veri, ('1.250,00 TL', '1.350,00 TL', 'sag'))
    sonra = set(_harfler(sonuc))
    # "1.250,00 TL" dışındaki bütün harfler aynı yer ve fontta.
    page = fitz.open(stream=veri)[0]
    hedef = next(q for q in motor._parcalar(page, 0) if q.metin == '1.250,00 TL')
    hedef_harfler = {(h.c, round(h.o.x, 1), round(h.o.y, 1)) for h in hedef.harfler}
    for h in once:
        if (h[0], h[1], h[2]) in hedef_harfler:
            continue
        assert h in sonra, h


def test_yalniz_degisen_harf_yazilir_ortak_ekler_kalir(ornek):
    veri = ornek('chrome-fatura.pdf')
    sonuc, uyarilar = _duzelt(veri, (' EKS2025000123   ', ' EKS2025000124   '))
    assert uyarilar == []
    once = [h for h in _harfler(veri) if h[3] == 'ArialMT']
    sonra = _harfler(sonuc)
    # '3' yerine '4', aynı yerde, aynı yazı tipiyle.
    uc = next(h for h in once if h[0] == '3' and 160 < h[1] < 190 and 100 < h[2] < 106)
    assert ('4', uc[1], uc[2], 'ArialMT') in sonra
    assert uc not in sonra
    assert 'EKS2025000124' in _metin(sonuc)


def test_asil_yazi_tipi_yeniden_kullanilir_yeni_font_gomulmez(ornek):
    veri = ornek('chrome-fatura.pdf')
    sonuc, uyarilar = _duzelt(veri, ('Güç kaynağı ünitesi', 'Güç kaynağı modülü'))
    assert uyarilar == []
    fontlar = {f[3] for f in fitz.open(stream=sonuc)[0].get_fonts()}
    assert not any('Nimbus' in f or f in ('Helvetica',) for f in fontlar)
    assert 'Güç kaynağı modülü' in _metin(sonuc)


def test_baglanti_ve_aciklamalar_korunur(ornek):
    veri = ornek('chrome-fatura.pdf')
    once = fitz.open(stream=veri)[0]
    linkler = [(l['uri'], tuple(round(v) for v in l['from'])) for l in once.get_links()]
    assert linkler
    sonuc, _ = _duzelt(veri, ('bağlantıya tıklayın', 'bağlantıya basın'))
    sonra = fitz.open(stream=sonuc)[0]
    assert [(l['uri'], tuple(round(v) for v in l['from'])) for l in sonra.get_links()] == linkler

    def ciz(doc, page):
        page.insert_text((50, 100), 'Not alani', fontname='helv', fontsize=12)
        page.add_freetext_annot(fitz.Rect(45, 85, 160, 105), 'serbest not')
        page.add_highlight_annot(fitz.Rect(45, 85, 160, 105))
    veri = _pdf(ciz)
    sonuc, _ = _duzelt(veri, ('Not alani', 'Not yeri'))
    turler = sorted(a.type[1] for a in fitz.open(stream=sonuc)[0].annots())
    assert turler == ['FreeText', 'Highlight']


def test_resim_ve_cizgiler_korunur():
    def ciz(doc, page):
        page.draw_rect(fitz.Rect(40, 80, 400, 130), color=None, fill=(0.85, 0.92, 1.0))
        page.draw_line((40, 98), (400, 98), color=(1, 0, 0), width=0.5)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
        pix.set_rect(pix.irect, (0, 128, 0))
        page.insert_image(fitz.Rect(420, 80, 470, 130), pixmap=pix)
        page.insert_text((50, 100), 'Zemin üstünde metin', fontname='helv', fontsize=12)
    veri = _pdf(ciz)
    once = fitz.open(stream=veri)[0]
    sonuc, _ = _duzelt(veri, ('Zemin üstünde metin', 'Zemin üstünde yazı'))
    sonra = fitz.open(stream=sonuc)[0]
    assert len(sonra.get_images()) == len(once.get_images()) == 1
    assert len(sonra.get_drawings()) == len(once.get_drawings())
    # Silinen harfin yerinde beyaz kutu yok: zemin rengi duruyor.
    pix = sonra.get_pixmap(dpi=72)
    assert pix.pixel(390, 90)[:3] == once.get_pixmap(dpi=72).pixel(390, 90)[:3]
    assert pix.pixel(200, 120)[2] > 240 and pix.pixel(200, 120)[0] < 240


def test_birden_cok_sayfa_ve_duzeltme():
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        _gomulu_nimbus(doc, page)
        page.insert_text((50, 100), f'Sayfa {i + 1} başlığı', fontname='F0', fontsize=14)
    veri = doc.tobytes()
    a = motor.analiz(veri)
    p1 = a['sayfalar'][0]['parcalar'][0]
    p3 = a['sayfalar'][2]['parcalar'][0]
    sonuc, _ = motor.uygula(veri, [
        {'id': p1['id'], 'eski': p1['t'], 'yeni': 'Birinci başlık'},
        {'id': p3['id'], 'eski': p3['t'], 'yeni': 'Üçüncü başlık'},
    ])
    d = fitz.open(stream=sonuc)
    assert d.page_count == 3
    assert 'Birinci başlık' in _metin(sonuc, 0)
    assert 'Sayfa 2 başlığı' in _metin(sonuc, 1)
    assert 'Üçüncü başlık' in _metin(sonuc, 2)


# ---------------------------------------------------------------- hizalama ve sığdırma

def _span(veri, metin_parcasi):
    page = fitz.open(stream=veri)[0]
    return next(p for p in motor._parcalar(page, 0) if metin_parcasi in p.metin)


def test_saga_dayali_sag_kenar_sabit(ornek):
    veri = ornek('reportlab-teklif.pdf')
    once = _span(veri, '1.234,56')
    sonuc, _ = _duzelt(veri, ('Sağa dayalı 1.234,56', 'Sağa dayalı 21.234,56', 'sag'))
    page = fitz.open(stream=sonuc)[0]
    harfler = [h for p in motor._parcalar(page, 0) for h in p.harfler if abs(h.o.y - once.harfler[0].o.y) < 1]
    sag = max(h.k.x1 for h in harfler)
    assert abs(sag - once.kutu.x1) < 0.3
    assert 'Sağa dayalı 21.234,56' in ''.join(h.g for h in sorted(harfler, key=lambda h: h.o.x))


def test_ortali_merkez_sabit():
    def ciz(doc, page):
        page.insert_text((100, 100), 'Orta baslik', fontname='helv', fontsize=16)
    veri = _pdf(ciz)
    once = _span(veri, 'Orta')
    sonuc, _ = _duzelt(veri, ('Orta baslik', 'Ortalanmis uzun baslik', 'orta'))
    sonra = _span(sonuc, 'Ortalanmis')
    assert abs((once.kutu.x0 + once.kutu.x1) / 2 - (sonra.kutu.x0 + sonra.kutu.x1) / 2) < 0.5


def test_sigdir_asil_genislikte_tutar():
    def ciz(doc, page):
        page.insert_text((50, 100), 'Kisa metin', fontname='helv', fontsize=12)
    veri = _pdf(ciz)
    once = _span(veri, 'Kisa')
    sonuc, _ = _duzelt(veri, ('Kisa metin', 'Kisa metin uzadi'), sigdir=True)
    sonra = [h for p in motor._parcalar(fitz.open(stream=sonuc)[0], 0) for h in p.harfler]
    assert max(h.k.x1 for h in sonra) <= once.kutu.x1 + 0.5
    # sığdırmasız: taşar
    sonuc, _ = _duzelt(veri, ('Kisa metin', 'Kisa metin uzadi'))
    sonra = [h for p in motor._parcalar(fitz.open(stream=sonuc)[0], 0) for h in p.harfler]
    assert max(h.k.x1 for h in sonra) > once.kutu.x1 + 10


def test_cakisma_uyarisi(ornek):
    _, uyarilar = _duzelt(ornek('chrome-fatura.pdf'), ('bağlantıya tıklayın', 'bağlantıya tıklayınız'))
    assert any('çakışıyor' in u for u in uyarilar)


# ---------------------------------------------------------------- yazı tipi seçimi

def test_eslemede_olup_cizimi_olmayan_glif_kullanilmaz():
    veri = _alt_kume_belgesi()
    # 'Z' ToUnicode'da var ama alt kümede çizimi yok → benzer yazı tipi.
    sonuc, uyarilar = _duzelt(veri, ('Fatura No: ABC-2025-0042', 'Fatura No: ABZ-2025-0042'))
    assert any('“Z”' in u for u in uyarilar)
    assert 'ABZ-2025-0042' in _metin(sonuc)
    # Z görünür: sayfada mürekkep olmalı (boş glif yazılmadı).
    sp = _span(sonuc, 'Z')
    z = next(h for h in sp.harfler if h.c == 'Z')
    pix = fitz.open(stream=sonuc)[0].get_pixmap(dpi=144, clip=z.k)
    assert min(pix.samples) < 128


def test_karma_yazim_var_olan_harfler_asil_fontta():
    veri = _alt_kume_belgesi()
    asil = _span(veri, 'Tutar').font
    sonuc, uyarilar = _duzelt(veri, ('Tutar 1.250,00', 'Tutar 1.250,06'))
    assert any('“6”' in u for u in uyarilar)
    page = fitz.open(stream=sonuc)[0]
    harfler = [(h.c, p.font) for p in motor._parcalar(page, 0) for h in p.harfler
               if abs(h.o.y - 160) < 1 and not h.c.isspace()]
    assert [c for c, _ in harfler].count('6') == 1
    assert ('6', asil) not in harfler                       # eksik harf benzer fontta
    assert all(f == asil for c, f in harfler if c != '6')   # gerisi asıl fontta, yerinde


def test_benzer_secenegi_standart_font_kullanir(ornek):
    veri = ornek('chrome-fatura.pdf')
    sonuc, _ = _duzelt(veri, ('Genel toplam', 'Genel toplam tutar'), font='benzer')
    page = fitz.open(stream=sonuc)[0]
    fontlar = {p.font for p in motor._parcalar(page, 0) if 'tutar' in p.metin}
    assert any('Nimbus' in f or 'Helvetica' in f for f in fontlar)


def test_gomulu_olmayan_helvetica_ayni_nesneyle_yazilir(ornek):
    veri = ornek('reportlab-teklif.pdf')
    sonuc, uyarilar = _duzelt(veri, ('Teklif Formu 2025', 'Teklif Formu 2026'))
    assert uyarilar == []
    page = fitz.open(stream=sonuc)[0]
    sp = next(p for p in motor._parcalar(page, 0) if p.metin == '6')
    assert sp.font == 'Helvetica-Bold'
    assert 'Teklif Formu 2026' in _metin(sonuc)


def test_winansi_disi_turkce_harf_helvetica_nesnesine_yazilmaz(ornek):
    # Gömülmemiş WinAnsi Helvetica'da 'ş' yok → karma yazım, uyarı.
    veri = ornek('reportlab-teklif.pdf')
    sonuc, uyarilar = _duzelt(veri, ('Teklif Formu 2025', 'Teklif Formuş 2025'))
    assert any('“ş”' in u for u in uyarilar)
    assert 'Teklif Formuş 2025' in _metin(sonuc)


# ---------------------------------------------------------------- sınırlar ve doğrulama

def test_silme_ve_sona_ekleme():
    def ciz(doc, page):
        page.insert_text((50, 100), 'Silinecek metin', fontname='helv', fontsize=12)
        page.insert_text((50, 130), 'Ekle', fontname='helv', fontsize=12)
    veri = _pdf(ciz)
    sonuc, _ = _duzelt(veri, ('Silinecek metin', ''), ('Ekle', 'Ekleme yapıldı'))
    metin = _metin(sonuc)
    assert 'Silinecek' not in metin
    assert 'Ekleme yapıldı' in metin


def test_eski_metin_uyusmazsa_hata(ornek):
    veri = ornek('chrome-fatura.pdf')
    p = _parcalar(veri)['Genel toplam']
    with pytest.raises(motor.PdfHatasi, match='bulunamadı'):
        motor.uygula(veri, [{'id': p['id'], 'eski': 'Başka metin', 'yeni': 'x'}])


@pytest.mark.parametrize('liste', [
    [],
    [{'id': '0-0-0-0'}],
    [{'id': '../x', 'eski': 'a', 'yeni': 'b'}],
    [{'id': '0-0-0-0', 'eski': 'a', 'yeni': 'b', 'hiza': 'yukari'}],
    [{'id': '0-0-0-0', 'eski': 'a', 'yeni': 'b'}, {'id': '0-0-0-0', 'eski': 'a', 'yeni': 'c'}],
    [{'id': '0-0-0-0', 'eski': 'a', 'yeni': 'b' * (motor.AZAMI_METIN + 1)}],
])
def test_gecersiz_liste(ornek, liste):
    with pytest.raises(motor.PdfHatasi):
        motor.uygula(ornek('chrome-fatura.pdf'), liste)


def test_degistirme_izni_yoksa_hata():
    doc = fitz.open()
    doc.new_page().insert_text((50, 100), 'Kilitli belge', fontname='helv')
    izin = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
    veri = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='sahip', permissions=izin)
    p = _parcalar(veri)['Kilitli belge']
    with pytest.raises(motor.PdfHatasi, match='izin'):
        motor.uygula(veri, [{'id': p['id'], 'eski': p['t'], 'yeni': 'Açık belge'}])


def test_sahte_kalin_yanlis_alarm_vermez():
    def ciz(doc, page):
        page.insert_text((50, 100), 'Kalin gorunum', fontname='helv', fontsize=12)
        page.insert_text((50.3, 100), 'Kalin gorunum', fontname='helv', fontsize=12)
        page.insert_text((50, 140), 'Komsu', fontname='helv', fontsize=12)
    veri = _pdf(ciz)
    parcalar = [p for s in motor.analiz(veri)['sayfalar'] for p in s['parcalar'] if 'Kalin' in p['t']]
    assert len(parcalar) == 2
    p = parcalar[0]
    sonuc, _ = motor.uygula(veri, [{'id': p['id'], 'eski': p['t'], 'yeni': 'Kalin yazi'}])
    assert 'Komsu' in _metin(sonuc)
    assert 'gorunum' not in _metin(sonuc)


def test_denetim_dokunulan_metni_yakalar(monkeypatch, ornek):
    """Silme başka bir harfe dokunursa PDF üretilmez (denetim mekanizması)."""
    gercek = motor._merkez

    def genis(r, yari=0.2):
        return fitz.Rect(r.x0 - 30, r.y0 - 30, r.x1 + 30, r.y1 + 30)
    monkeypatch.setattr(motor, '_merkez', genis)
    with pytest.raises(motor.PdfHatasi, match='etkilendi'):
        _duzelt(ornek('chrome-fatura.pdf'), ('2.500,00 TL', '2.600,00 TL', 'sag'))
    monkeypatch.setattr(motor, '_merkez', gercek)


def test_form_xobject_icindeki_metin():
    kaynak = fitz.open()
    kaynak.new_page(width=300, height=200).insert_text((20, 50), 'Gömülü form metni', fontname='helv', fontsize=12)
    doc = fitz.open()
    page = doc.new_page()
    page.show_pdf_page(fitz.Rect(50, 50, 350, 250), kaynak, 0)
    page.insert_text((50, 400), 'Sayfa metni', fontname='helv', fontsize=12)
    veri = doc.tobytes()
    sonuc, _ = _duzelt(veri, ('Gömülü form metni', 'Gömülü form yazısı'))
    metin = _metin(sonuc)
    assert 'Gömülü form yazısı' in metin
    assert 'Gömülü form metni' not in metin
    assert 'Sayfa metni' in metin


def test_yalniz_orta_degisir_aralikli_kuyruk_kimildamaz():
    """Genişliği aynı kalan değişiklikte kuyruk yeniden yazılmaz: düzensiz harf
    aralığı (TJ kerning) olan son ek, harfi harfine yerinde kalır."""
    def ciz(doc, page):
        page.insert_text((10, 10), '.', fontname='helv', fontsize=1)   # /helv kaynağı
        fitz.TOOLS._insert_contents(
            page, b'BT /helv 12 Tf 1 0 0 1 50 700 Tm [(Toplam 1)-80(0)120(0,)-60(00 T)90(L)] TJ ET', 1)
    veri = _pdf(ciz)
    p = next(p for p in _parcalar(veri).values() if 'Toplam' in p['t'])
    yeni = p['t'].replace('1', '2', 1)
    once = [h for h in _harfler(veri) if h[2] > 100]
    sonuc, _ = motor.uygula(veri, [{'id': p['id'], 'eski': p['t'], 'yeni': yeni}])
    sonra = set(_harfler(sonuc))
    bir = next(h for h in once if h[0] == '1')
    for h in once:
        if h != bir:
            assert h in sonra, h
    assert ('2', bir[1], bir[2], bir[3]) in sonra


def test_hiza_onerisi_tablo_sutunlari(ornek):
    parcalar = _parcalar(ornek('chrome-fatura.pdf'))
    for t in ('1.250,00 TL', '85,50 TL', '2.500,00 TL', '855,00 TL', '3.355,00 TL', '10'):
        assert parcalar[t]['hiza'] == 'sag', t
    for t in ('Güç kaynağı ünitesi', 'Işıklı uyarı levhası', 'SATIŞ FATURASI'):
        assert parcalar[t]['hiza'] == 'sol', t


# ---------------------------------------------------------------- yeni metin ekleme

def _ekle(veri, *eklemeler, duzeltmeler=()):
    return motor.uygula(veri, list(duzeltmeler), list(eklemeler))


def _ek(sayfa=1, x=0.1, y=0.9, metin='Yeni yazı', boyut=12, renk='#000000', font=None):
    return {'sayfa': sayfa, 'x': x, 'y': y, 'metin': metin, 'boyut': boyut, 'renk': renk,
            'font': font or {'tur': 'standart', 'ad': 'helv'}}


def _span_ile(veri, parca, pno=0):
    page = fitz.open(stream=veri)[pno]
    return next(p for p in motor._parcalar(page, pno) if parca in p.metin)


def test_analiz_yazi_istatistigi_ve_sayfa_boyutu(ornek):
    s = motor.analiz(ornek('chrome-fatura.pdf'))['sayfalar'][0]
    assert s['pt'] == [612.0, 792.0]
    ilk = s['yazi'][0]
    assert ilk['ad'] == 'ArialMT' and ilk['boyut'] == 9.0 and ilk['renk'] == '#222222'
    assert {y['ad'] for y in s['yazi']} >= {'ArialMT', 'Arial-BoldMT', 'Georgia-Bold'}


def test_standart_yazi_tipiyle_eklenir_yerinde_ve_digerlerine_dokunmaz(ornek):
    veri = ornek('chrome-fatura.pdf')
    once = _harfler(veri)
    sonuc, uyarilar = _ekle(veri, _ek(x=0.5, y=0.6, metin='Onaylandı İ.Ş.', boyut=14, renk='#cc0000'))
    assert uyarilar == []
    sonra = _harfler(sonuc)
    for h in once:
        assert h in sonra, h
    sp = _span_ile(sonuc, 'Onaylandı')
    assert abs(sp.harfler[0].o.x - 0.5 * 612) < 0.5 and abs(sp.harfler[0].o.y - 0.6 * 792) < 0.5
    assert abs(sp.boyut - 14) < 0.1 and sp.renk == 0xcc0000
    assert 'Helvetica' in sp.font or 'Nimbus' in sp.font


def test_belgenin_yazi_tipi_yeniden_kullanilir(ornek):
    veri = ornek('chrome-fatura.pdf')
    sonuc, uyarilar = _ekle(veri, _ek(metin='Teslim edildi', boyut=9,
                                       font={'tur': 'belge', 'ad': 'ArialMT', 'bayrak': 0}))
    assert uyarilar == []
    assert _span_ile(sonuc, 'Teslim').font == 'ArialMT'
    fontlar = {f[3] for f in fitz.open(stream=sonuc)[0].get_fonts()}
    assert not any('Nimbus' in f for f in fontlar)          # yeni yazı tipi gömülmedi


def test_belge_yazi_tipinde_olmayan_harf_benzerle_ve_uyariyla(ornek):
    veri = ornek('chrome-fatura.pdf')
    sonuc, uyarilar = _ekle(veri, _ek(metin='Yeni QX', font={'tur': 'belge', 'ad': 'ArialMT', 'bayrak': 0}))
    assert any('“Q”' in u and '“X”' in u for u in uyarilar)
    assert 'Yeni QX' in _metin(sonuc)


def test_birden_cok_satir_alt_alta():
    def ciz(doc, page):
        page.insert_text((50, 50), 'Var olan', fontname='helv', fontsize=12)
    veri = _pdf(ciz)
    sonuc, _ = _ekle(veri, _ek(x=0.2, y=0.5, metin='Birinci satir\n\nUcuncu satir', boyut=10))
    bir = _span_ile(sonuc, 'Birinci').harfler[0].o
    uc = _span_ile(sonuc, 'Ucuncu').harfler[0].o
    assert abs(bir.x - uc.x) < 0.1
    assert abs((uc.y - bir.y) - 2 * 1.2 * 10) < 0.2


def test_dondurulmus_sayfada_ekranda_yatay_ve_tiklanan_yerde():
    def ciz(doc, page):
        page.insert_text((50, 50), 'Eski metin', fontname='helv', fontsize=12)
        page.set_rotation(90)
    veri = _pdf(ciz, width=600, height=400)
    sonuc, _ = _ekle(veri, _ek(x=0.25, y=0.5, metin='Yatay ekleme', boyut=12))
    s = motor.analiz(sonuc)['sayfalar'][0]
    p = next(p for p in s['parcalar'] if 'Yatay' in p['t'])
    x0, y0, x1, y1 = p['k']
    # Ekranda (döndürülmüş görüntüde) yatay: genişlik yükseklikten büyük, sol kenar tıklanan yerde.
    assert (x1 - x0) * s['pt'][0] > (y1 - y0) * s['pt'][1]
    assert abs(x0 - 0.25) < 0.01 and y0 < 0.5 < y1 + 0.01
    assert 'Eski metin' in _metin(sonuc)


def test_mevcut_metnin_ustune_binerse_uyari(ornek):
    veri = ornek('chrome-fatura.pdf')
    s = motor.analiz(veri)['sayfalar'][0]
    p = next(p for p in s['parcalar'] if p['t'] == 'Genel toplam')
    _, uyarilar = _ekle(veri, _ek(x=p['k'][0], y=p['k'][3] - 0.002, metin='Ustune yazi', boyut=9))
    assert any('üstüne biniyor' in u for u in uyarilar)


def test_duzeltme_ve_ekleme_birlikte(ornek):
    veri = ornek('chrome-fatura.pdf')
    p = _parcalar(veri)['Genel toplam']
    sonuc, _ = motor.uygula(veri, [{'id': p['id'], 'eski': p['t'], 'yeni': 'Genel tutar'}],
                            [_ek(metin='Ek not')])
    metin = _metin(sonuc)
    assert 'Genel tutar' in metin and 'Ek not' in metin


@pytest.mark.parametrize('bozuk', [
    {'font': {'tur': 'standart', 'ad': 'comic'}},
    {'font': {'tur': 'baska', 'ad': 'helv'}},
    {'x': 1.5}, {'y': -0.1}, {'x': True}, {'boyut': 2}, {'boyut': 500},
    {'metin': '   '}, {'metin': 'a\n' * 60}, {'renk': 'kirmizi'}, {'sayfa': 0}, {'sayfa': 9},
])
def test_gecersiz_ekleme(ornek, bozuk):
    with pytest.raises(motor.PdfHatasi):
        _ekle(ornek('chrome-fatura.pdf'), {**_ek(), **bozuk})


def test_bos_liste_hatasi(ornek):
    with pytest.raises(motor.PdfHatasi, match='eklenecek metin yok'):
        motor.uygula(ornek('chrome-fatura.pdf'), [], [])


# ---------------------------------------------------------------- paragraf

def _paragraflar_(veri):
    return {p['id']: p for p in motor.analiz(veri)['sayfalar'][0]['paragraflar']}


def _par_duzelt(veri, pid, yeni, hiza=None, duzeltmeler=()):
    p = _paragraflar_(veri)[pid]
    return motor.uygula(veri, list(duzeltmeler), None, [{'id': pid, 'eski': p['t'], 'yeni': yeni, 'hiza': hiza}])


def _satirlar(veri, metin_parcasi):
    """Yeni paragrafın satırları: [(taban y, x0, x1, boyut)] — parçayı içeren paragraftan."""
    page = fitz.open(stream=veri)[0]
    for par in motor._paragraflar(motor._parcalar(page, 0)):
        if metin_parcasi in par.metin:
            sonuc = []
            for satir in par.satirlar:
                harf = [h for p in satir for h in p.harfler if not h.c.isspace()]
                sonuc.append((harf[0].o.y, min(h.k.x0 for h in harf), max(h.k.x1 for h in harf), satir[0].boyut))
            return par, sonuc
    raise AssertionError(metin_parcasi)


def test_paragraf_algilama(ornek):
    c = _paragraflar_(ornek('chrome-paragraf.pdf'))
    assert {k: (v['hiza'], v['satir']) for k, v in c.items()} == {
        'P0-0': ('sol', 3), 'P0-1': ('iki', 3), 'P0-2': ('iki', 3), 'P0-3': ('orta', 2),
        'P0-5': ('iki', 3), 'P0-6': ('iki', 3)}
    assert c['P0-2']['t'].startswith('Ödeme, faturanın tebliğinden itibaren otuz gün')
    r = _paragraflar_(ornek('reportlab-paragraf.pdf'))
    assert [v['hiza'] for v in r.values()] == ['iki', 'iki', 'sol']


def test_paragraf_sola_dayali_yeniden_dizilir_digerleri_yerinde(ornek):
    veri = ornek('chrome-paragraf.pdf')
    once = _paragraflar_(veri)
    p = once['P0-0']
    yeni = p['t'].replace('bir yıl süreyle', 'iki yıl süreyle').replace('otuz gün', 'altmış takvim günü')
    sonuc, uyarilar = _par_duzelt(veri, 'P0-0', yeni)
    assert not any('biniyor' in u for u in uyarilar)
    par, satirlar = _satirlar(sonuc, 'altmış takvim')
    assert par.metin == yeni and len(satirlar) == 3
    assert max(x0 for _y, x0, _x1, _b in satirlar) - min(x0 for _y, x0, _x1, _b in satirlar) < 0.5
    # Öbür paragraflar aynen.
    sonra = _paragraflar_(sonuc)
    for pid in ('P0-1', 'P0-2', 'P0-5', 'P0-6'):
        assert any(v['t'] == once[pid]['t'] for v in sonra.values()), pid


def test_paragraf_iki_yana_yasli_sag_kenar_ve_kucultme(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-1']
    _, asil = _satirlar(veri, 'Satıcı, teslim')
    yeni = p['t'].replace('on dört gün', 'yirmi bir iş günü') + ' Ayıplı ürün bedeli iade edilir.'
    sonuc, uyarilar = _par_duzelt(veri, 'P0-1', yeni)
    assert any('küçültüldü' in u for u in uyarilar)
    _, satirlar = _satirlar(sonuc, 'Ayıplı ürün')
    assert len(satirlar) == 3                                  # satır sayısı korundu
    sag = asil[0][2]
    for _y, _x0, x1, _b in satirlar[:-1]:
        assert abs(x1 - sag) < 0.6                             # iki yana yaslı
    assert 0.84 * asil[0][3] <= satirlar[0][3] < asil[0][3]    # en çok %15 küçüldü
    assert [round(y, 1) for y, *_ in satirlar] == [round(y, 1) for y, *_ in asil]   # satır aralığı aynı


def test_paragraf_kalin_sozcukler_korunur(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-2']
    yeni = p['t'].replace('otuz gün', 'kırk gün').replace('banka havalesi', 'banka havalesi veya EFT')
    sonuc, _ = _par_duzelt(veri, 'P0-2', yeni)
    par, _ = _satirlar(sonuc, 'kırk')
    stil = {}
    for k in par.kelimeler:
        stil.setdefault(k.metin, set()).add('kalin' if 'Bold' in k.stil.font else 'duz')
    assert stil['kırk'] == {'kalin'} and stil['gün'] == {'kalin'}           # değiştirilen yerinin biçimini aldı
    assert stil['aylık'] == {'kalin'} and stil['yüzde'] == {'kalin'}        # değişmeyen korundu
    assert stil['EFT'] == {'duz'} and stil['veya'] == {'duz'}               # eklenen soldakinin biçimi


def test_paragraf_ortali_ve_girintili(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-3']
    _, asil = _satirlar(veri, 'Bu belge elektronik')
    merkez = sum((x0 + x1) / 2 for _y, x0, x1, _b in asil) / len(asil)
    sonuc, _ = _par_duzelt(veri, 'P0-3', p['t'].replace('ıslak imza', 'ıslak imza ya da kaşe'))
    _, satirlar = _satirlar(sonuc, 'ya da kaşe')
    for _y, x0, x1, _b in satirlar:
        assert abs((x0 + x1) / 2 - merkez) < 1.0
    g = _paragraflar_(veri)['P0-5']
    _, gasil = _satirlar(veri, 'Gizlilik')
    sonuc, _ = _par_duzelt(veri, 'P0-5', g['t'].replace('beş yıl', 'on yıl'))
    _, gsat = _satirlar(sonuc, 'on yıl')
    assert abs((gsat[0][1] - gsat[1][1]) - (gasil[0][1] - gasil[1][1])) < 0.5   # ilk satır girintisi


def test_paragraf_uzarsa_asil_boyutta_ve_alttakine_binerse_uyari(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-0']
    _, asil = _satirlar(veri, 'Bu sözleşme')
    yeni = p['t'] + ' ' + ' '.join(['Ek hüküm metni burada sürer.'] * 6)
    sonuc, uyarilar = _par_duzelt(veri, 'P0-0', yeni)
    assert any('aşağı uzadı' in u for u in uyarilar)
    assert any('biniyor' in u for u in uyarilar)              # altındaki paragrafa biniyor
    _, satirlar = _satirlar(sonuc, 'Ek hüküm')
    assert len(satirlar) > 3 and abs(satirlar[0][3] - asil[0][3]) < 0.05


def test_paragraf_hiza_degistirilebilir_ve_silinebilir(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-0']
    sonuc, _ = _par_duzelt(veri, 'P0-0', p['t'], hiza='sag')
    _, satirlar = _satirlar(sonuc, 'Bu sözleşme')
    assert max(x1 for *_, x1, _b in satirlar) - min(x1 for *_, x1, _b in satirlar) < 0.6
    sonuc, _ = _par_duzelt(veri, 'P0-0', '')
    metin = _metin(sonuc)
    assert 'sözleşme aynı koşullarla' not in metin and 'Gizlilik' in metin


def test_paragraf_ve_satir_ayni_anda_degistirilemez(ornek):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)['P0-0']
    span = p['parcalar'][0]
    s = motor.analiz(veri)['sayfalar'][0]
    t = next(x['t'] for x in s['parcalar'] if x['id'] == span)
    with pytest.raises(motor.PdfHatasi, match='hem satır hem paragraf'):
        _par_duzelt(veri, 'P0-0', p['t'] + ' x', duzeltmeler=[{'id': span, 'eski': t, 'yeni': 'x'}])
    with pytest.raises(motor.PdfHatasi, match='bulunamadı'):
        motor.uygula(veri, [], None, [{'id': 'P0-0', 'eski': 'başka metin', 'yeni': 'x'}])


@pytest.mark.parametrize('bozuk', [
    'x', [{'id': 'X0-0', 'eski': 'a', 'yeni': 'b'}], [{'id': 'P0-0', 'eski': 1, 'yeni': 'b'}],
    [{'id': 'P0-0', 'eski': 'a', 'yeni': 'b', 'hiza': 'yukari'}],
    [{'id': 'P0-0', 'eski': 'a', 'yeni': 'b'}, {'id': 'P0-0', 'eski': 'a', 'yeni': 'c'}],
])
def test_gecersiz_paragraf_listesi(ornek, bozuk):
    with pytest.raises(motor.PdfHatasi):
        motor.uygula(ornek('chrome-paragraf.pdf'), [], None, bozuk)


@pytest.mark.parametrize('pid,degis', [
    ('P0-1', lambda t: t.replace('on dört gün', 'yirmi bir iş günü') + ' Ayıplı ürün bedeli iade edilir.'),  # küçülen
    ('P0-2', lambda t: t.replace('otuz gün', 'kırk gün')),                                                  # karma yazı tipi
    ('P0-3', lambda t: t.replace('ıslak imza', 'ıslak imza ya da kaşe')),                                     # ortalı
])
def test_duzenlenen_paragraf_yeniden_paragraf_olarak_tanınır(ornek, pid, degis):
    veri = ornek('chrome-paragraf.pdf')
    p = _paragraflar_(veri)[pid]
    yeni = degis(p['t'])
    sonuc, _ = _par_duzelt(veri, pid, yeni)
    assert yeni in [v['t'] for v in _paragraflar_(sonuc).values()]


# ---------------------------------------------------------------- resim ekleme (1.17)

def _dort_renk_png():
    """Sol üst kırmızı, sağ üst yeşil, sol alt mavi, sağ alt sarı (40×20)."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    for yy in range(20):
        for xx in range(40):
            pix.set_pixel(xx, yy, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)][(yy >= 10) * 2 + (xx >= 20)])
    return pix.tobytes('png')


def _renkler(veri, kutu, pno=0):
    """Kutunun (ekran oranı x, y, w, h) dört çeyreğindeki renk adları: K Y M S."""
    page = fitz.open(stream=veri)[pno]
    g = page.get_pixmap(dpi=72)
    x, y, w, h = kutu
    adlar = {(255, 0, 0): 'K', (0, 255, 0): 'Y', (0, 0, 255): 'M', (255, 255, 0): 'S'}
    return ''.join(adlar.get(tuple(g.pixel(int((x + w * a) * g.width), int((y + h * b) * g.height))[:3]), '?')
                   for b in (0.25, 0.75) for a in (0.25, 0.75))


def _resim(sayfa=1, kutu=(0.1, 0.2, 0.4, 0.2), veri=None):
    x, y, w, h = kutu
    return {'sayfa': sayfa, 'x': x, 'y': y, 'w': w, 'h': h, 'veri': veri or _dort_renk_png()}


@pytest.mark.parametrize('donus', [0, 90, 180, 270])
def test_resim_kutuya_ekranda_dik_oturur(donus):
    def ciz(doc, page):
        # Hiçbir dönüşte kutunun altında kalmayan yer (kesişirse uyarı doğru olarak çıkar).
        page.insert_text((160, 480), 'Dokunulmayan satır', fontsize=12)
        page.set_rotation(donus)
    veri = _pdf(ciz, width=300, height=500)
    once = _harfler(veri)
    sonuc, uyarilar = motor.uygula(veri, [], resimler=[_resim()])
    assert _renkler(sonuc, (0.1, 0.2, 0.4, 0.2)) == 'KYMS'
    assert _harfler(sonuc) == once and uyarilar == []


def test_resim_metnin_ustune_binerse_uyarir_metin_yerinde_kalir():
    veri = _pdf(lambda d, p: p.insert_text((50, 100), 'Altta kalan satır', fontsize=12), width=300, height=500)
    once = _harfler(veri)
    sonuc, uyarilar = motor.uygula(veri, [], resimler=[_resim(kutu=(0.1, 0.15, 0.5, 0.1))])
    assert _harfler(sonuc) == once
    assert any('resim' in u and 'Altta kalan' in u for u in uyarilar)


def test_resim_dogrulamasi():
    veri = _pdf(lambda d, p: p.insert_text((50, 100), 'x', fontsize=12))
    for bozuk, kod in (({'veri': b'GIF89a....'}, 'resim'), ({'veri': b'\x89PNG\r\n\x1a\nbozuk'}, 'resim'),
                       ({'x': 0.8, 'w': 0.4}, 'liste'), ({'w': 0}, 'liste'), ({'sayfa': 0}, 'liste'),
                       ({'x': float('nan')}, 'liste')):
        r = dict(_resim(), **bozuk)
        with pytest.raises(motor.PdfHatasi) as e:
            motor.uygula(veri, [], resimler=[r])
        assert e.value.kod == kod, bozuk
    with pytest.raises(motor.PdfHatasi) as e:
        motor.uygula(veri, [], resimler=[_resim()] * (motor.AZAMI_RESIM + 1))
    assert e.value.kod == 'liste'
    with pytest.raises(motor.PdfHatasi) as e:
        motor.uygula(veri, [], resimler=[_resim(sayfa=2)])
    assert e.value.kod == 'eslesmedi'


def test_saydam_png_saydam_kalir():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), True)
    pix.set_rect(pix.irect, (0, 0, 0, 0))      # tamamen saydam (clear_with alfayı 255 bırakıyor)
    veri = _pdf(lambda d, p: p.draw_rect(fitz.Rect(0, 0, 300, 500), color=None, fill=(0, 0, 1)), width=300, height=500)
    sonuc, _ = motor.uygula(veri, [], resimler=[_resim(veri=pix.tobytes('png'))])
    g = fitz.open(stream=sonuc)[0].get_pixmap(dpi=72)
    assert tuple(g.pixel(int(0.3 * g.width), int(0.3 * g.height))[:3]) == (0, 0, 255)   # zemin görünür
