"""PDF → Word / Excel çevirme (pdf_duzenle/cevir.py)."""
import io
import logging
import re
import sys
import zipfile
from datetime import datetime

import pymupdf as fitz
import pytest

from pdf_duzenle import cevir, motor


def _docx(veri):
    import docx
    return docx.Document(io.BytesIO(veri))


def _belge_xml(veri):
    with zipfile.ZipFile(io.BytesIO(veri)) as z:
        return z.read('word/document.xml').decode('utf-8'), z.read('word/_rels/document.xml.rels').decode('utf-8')


def _xlsx(veri):
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(veri)).active


def _izgara_pdf(hucreler, sutun_eni=120, satir_boyu=22, x0=50, y0=60, sayfa_sayisi=1, baslik=None):
    """Çizgili (kafes) tablo: her sayfada aynı başlık + `hucreler` satırları."""
    doc = fitz.open()
    for _ in range(sayfa_sayisi):
        page = doc.new_page()
        satirlar = ([baslik] if baslik else []) + hucreler
        n_sut = len(satirlar[0])
        for i, satir in enumerate(satirlar):
            for j, metin in enumerate(satir):
                r = fitz.Rect(x0 + j * sutun_eni, y0 + i * satir_boyu,
                              x0 + (j + 1) * sutun_eni, y0 + (i + 1) * satir_boyu)
                page.draw_rect(r, color=(0, 0, 0), width=0.8)
                page.insert_text((r.x0 + 4, r.y1 - 7), metin, fontsize=9, fontname='helv')
        assert n_sut
    return doc.tobytes()


# ---------------------------------------------------------------- Word

def test_word_fatura_metin_tablo_ve_baglanti(ornek):
    veri, uyarilar = cevir.word(ornek('chrome-fatura.pdf'))
    assert uyarilar == []
    d = _docx(veri)
    metin = '\n'.join(p.text for p in d.paragraphs)
    assert 'SATIŞ FATURASI' in metin
    hucreler = [c.text for t in d.tables for r in t.rows for c in r.cells]
    assert 'Ürün' in hucreler and '1.250,00 TL' in hucreler
    xml, rels = _belge_xml(veri)
    # Bağlantı paragrafın çocuğu olmalı; pdf2docx'in kendi yazımı w:r > w:hyperlink idi.
    assert '<w:hyperlink' in xml and 'bağlantıya tıklayın' in xml
    assert not re.search(r'<w:r>(?:(?!</w:r>).)*<w:hyperlink', xml, re.S)
    assert 'https://example.com/fatura' in rels
    # python-docx şablonunun yazar bilgisi dosyaya geçmez.
    assert d.core_properties.author == '' and 'python-docx' not in (d.core_properties.comments or '')


@pytest.mark.parametrize('adres', ['javascript:alert(1)', 'file:///etc/passwd', 'smb://sunucu/paylasim'])
def test_word_guvensiz_baglanti_tasinmaz_metin_kalir(adres):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), 'Tiklanacak yazi burada', fontsize=12, fontname='helv')
    page.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(70, 88, 220, 104), 'uri': adres})
    veri, _ = cevir.word(doc.tobytes())
    xml, rels = _belge_xml(veri)
    assert 'Tiklanacak yazi burada' in ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', xml))
    assert adres not in rels and '<w:hyperlink' not in xml


def test_word_taranmis_belge_uyarir():
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
    pix.set_rect(pix.irect, (40, 90, 160))
    page.insert_image(fitz.Rect(72, 72, 472, 272), pixmap=pix)
    veri, uyarilar = cevir.word(doc.tobytes())
    assert any('seçilebilir metin yok' in u for u in uyarilar)
    assert len(_docx(veri).inline_shapes) >= 1 or b'media/' in veri


def test_cozumlenemeyen_sayfa_uyarida_ve_dosyada_yok(monkeypatch, ornek):
    from pdf2docx.page.Page import Page
    asil = Page.parse

    def bozuk(self, **ayar):
        if self.id == 1:
            raise RuntimeError('deneme')
        return asil(self, **ayar)
    monkeypatch.setattr(Page, 'parse', bozuk)
    veri, uyarilar = cevir.word(ornek('chrome-ekstre.pdf'))
    assert uyarilar == ['Şu sayfalar çevrilemedi ve dosyada yok: 2.']
    assert len(_docx(veri).tables) == 2


def test_hicbir_sayfa_cozumlenemezse_hata(monkeypatch, ornek):
    from pdf2docx.page.Page import Page

    def bozuk(self, **ayar):
        raise RuntimeError('deneme')
    monkeypatch.setattr(Page, 'parse', bozuk)
    with pytest.raises(motor.PdfHatasi) as e:
        cevir.word(ornek('chrome-fatura.pdf'))
    assert e.value.kod == 'pdf'


def test_sure_butcesi_asilinca_yarim_dosya_yok(ornek):
    with pytest.raises(motor.PdfHatasi) as e:
        cevir.excel(ornek('chrome-ekstre.pdf'), butce=-1)
    assert e.value.kod == 'sure' and '0/3' in str(e.value)


def test_kopyalama_izni_yoksa_cevrilmez(ornek):
    doc = fitz.open(stream=ornek('chrome-fatura.pdf'))
    izin = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_ACCESSIBILITY
    veri = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='sahip', user_pw='', permissions=izin)
    for islev in (cevir.word, cevir.excel):
        with pytest.raises(motor.PdfHatasi) as e:
            islev(veri)
        assert e.value.kod == 'izin'


def test_sayfa_siniri_ve_sifre_motorla_ayni():
    doc = fitz.open()
    for _ in range(motor.AZAMI_SAYFA + 1):
        doc.new_page()
    with pytest.raises(motor.PdfHatasi) as e:
        cevir.word(doc.tobytes())
    assert e.value.kod == 'sayfa_siniri'
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), 'gizli')
    veri = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='a', user_pw='b')
    with pytest.raises(motor.PdfHatasi) as e:
        cevir.excel(veri)
    assert e.value.kod == 'sifreli'


def test_yukleme_kok_kaydediciyi_degistirmez(monkeypatch):
    """pdf2docx yüklenirken `logging.basicConfig(level=INFO)` çağırıyor."""
    kok = logging.getLogger()
    monkeypatch.setattr(kok, 'handlers', [])
    monkeypatch.setattr(kok, 'level', logging.WARNING)
    for ad in [m for m in sys.modules if m == 'pdf2docx' or m.startswith('pdf2docx.')]:
        monkeypatch.delitem(sys.modules, ad)
    monkeypatch.setattr(cevir, '_yuklu', None)
    cevir._yukle()
    assert kok.handlers == [] and kok.level == logging.WARNING


# ---------------------------------------------------------------- Excel

def test_excel_ekstre_sayfalari_tek_tabloda_basliksiz_tekrar(ornek):
    veri, uyarilar = cevir.excel(ornek('chrome-ekstre.pdf'))
    assert uyarilar == []
    ws = _xlsx(veri)
    satirlar = [[c.value for c in r] for r in ws.iter_rows()]
    assert satirlar[0] == ['Tarih', 'Açıklama', 'Tutar (TL)', 'Bakiye (TL)']
    assert ws.title == 'Tablolar'
    # 110 hareket, üç sayfaya bölünmüş; başlık yalnız bir kez, araya boş satır girmez.
    assert len(satirlar) == 111
    assert sum(1 for s in satirlar if s[0] == 'Tarih') == 1
    assert satirlar[1][0] == datetime(2025, 10, 1) and ws['A2'].number_format == 'dd.mm.yyyy'
    assert satirlar[1][2] == pytest.approx(-3792.5) and ws['C2'].number_format == '#,##0.00'
    assert all(isinstance(s[3], float) for s in satirlar[1:])
    assert ws['A1'].font.bold and not ws['A2'].font.bold


def test_excel_fatura_tutar_para_birimi_ve_birlesik_hucre(ornek):
    ws = _xlsx(cevir.excel(ornek('chrome-fatura.pdf'))[0])
    satirlar = [[c.value for c in r] for r in ws.iter_rows()]
    # Çerçeveli tek hücreli başlık kutusu tablo sayılmaz; kalem tablosu ilk satırda.
    assert satirlar[0] == ['Ürün', 'Adet', 'Birim fiyat', 'Tutar']
    assert satirlar[1] == ['Güç kaynağı ünitesi', 2, 1250, 2500]
    assert ws['C2'].number_format == '#,##0.00 "TL"' and ws['B2'].number_format == '#,##0'
    assert satirlar[3][0] == 'Genel toplam' and satirlar[3][3] == 3355
    assert 'A4:C4' in {str(r) for r in ws.merged_cells.ranges}


def test_excel_formul_gibi_metin_formul_olmaz():
    veri = _izgara_pdf([['=1+1', '=HYPERLINK("http://x")'], ['a', 'b']], baslik=['Ad', 'Deger'])
    ws = _xlsx(cevir.excel(veri)[0])
    hucreler = {c.value: c.data_type for r in ws.iter_rows() for c in r if c.value is not None}
    assert hucreler['=1+1'] == 's' and hucreler['=HYPERLINK("http://x")'] == 's'


def test_excel_farkli_tablolar_ayri_kalir_ayni_baslik_birlesir():
    baslik = ['Kod', 'Tutar']
    veri = _izgara_pdf([['A1', '10,00'], ['A2', '20,00']], baslik=baslik, sayfa_sayisi=2)
    ws = _xlsx(cevir.excel(veri)[0])
    satirlar = [[c.value for c in r] for r in ws.iter_rows()]
    assert satirlar == [['Kod', 'Tutar'], ['A1', 10], ['A2', 20], ['A1', 10], ['A2', 20]]
    # Başlığı ve sütun sayısı farklı ikinci tablo ayrı kalır, araya bir boş satır.
    doc = fitz.open(stream=_izgara_pdf([['x', '1']], baslik=['Ad', 'Sayi']))
    doc.insert_pdf(fitz.open(stream=_izgara_pdf([['p', 'q', 'r']], baslik=['K', 'L', 'M'])))
    ws = _xlsx(cevir.excel(doc.tobytes())[0])
    satirlar = [[c.value for c in r] for r in ws.iter_rows()]
    assert satirlar == [['Ad', 'Sayi', None], ['x', 1, None], [None, None, None], ['K', 'L', 'M'],
                        ['p', 'q', 'r']]


def test_excel_tablo_yoksa_hata(ornek):
    with pytest.raises(motor.PdfHatasi) as e:
        cevir.excel(ornek('chrome-paragraf.pdf'))
    assert e.value.kod == 'tablo_yok' and "Word'e çevirin" in str(e.value)


@pytest.mark.parametrize('metin, yerel, beklenen, bicim', [
    ('1.250,00 TL', 'tr', 1250.0, '#,##0.00 "TL"'),
    ('-3.792,50', 'tr', -3792.5, '#,##0.00'),
    ('−12,5', 'tr', -12.5, '#,##0.0'),
    ('(1.000,00)', 'tr', -1000.0, '#,##0.00'),
    ('₺85,50', 'tr', 85.5, '"₺"#,##0.00'),
    ('1.250', 'tr', 1250, '#,##0'),
    ('1.250', 'en', 1.25, '#,##0.000'),
    ('1,250.75', 'en', 1250.75, '#,##0.00'),
    ('$1,250.75', 'en', 1250.75, '"$"#,##0.00'),
    ('42', 'tr', 42, '#,##0'),
    ('%18', 'tr', 0.18, '0%'),
    ('%18,5', 'tr', 0.185, '0.0%'),
    ('20%', 'en', 0.2, '0%'),
    ('01.10.2025', 'tr', datetime(2025, 10, 1), 'dd.mm.yyyy'),
    ('01.10.2025 14:30', 'tr', datetime(2025, 10, 1, 14, 30), 'dd.mm.yyyy hh:mm'),
    ('2025-10-01', 'tr', datetime(2025, 10, 1), 'yyyy-mm-dd'),
])
def test_deger_sayi_tarih_yuzde(metin, yerel, beklenen, bicim):
    v, b = cevir.deger(metin, yerel)
    if beklenen is None:
        assert (v, b) == (metin, None)
    else:
        assert v == pytest.approx(beklenen) if isinstance(beklenen, float) else v == beklenen
        assert b == bicim


@pytest.mark.parametrize('metin', [
    '1234567890',               # VKN: kimlik numarası sayı olmaz
    '0012',                     # baştaki sıfır kaybolmasın
    'TR12 0001 2345',           # IBAN parçası
    '31.02.2025',               # geçersiz tarih
    '01/10/2025',               # İngilizce belgede ay / gün belirsiz (aşağıda en ile)
    '12,50 TL ₺',
    'Adet 2',
    '1.2.3',
    '1234567890123456,5',       # 15 haneden uzun
])
def test_deger_metin_kalanlar(metin):
    assert cevir.deger(metin, 'en' if '/' in metin else 'tr') == (metin, None)


def test_yerel_cogunluktan_tarih_sayilmaz():
    assert cevir._yerel(['1.250,00', '85,50', '01.10.2025', '01.11.2025', '02.11.2025']) == 'tr'
    assert cevir._yerel(['1,250.00', '85.50', '3.25']) == 'en'
    assert cevir._yerel(['01.10.2025', '02.10.2025']) == 'tr'
