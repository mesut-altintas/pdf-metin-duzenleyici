"""PDF → Word / Excel çevirme. Flask'a bağımlı değildir.

    word(pdf)  -> (docx baytları, uyarılar)
    excel(pdf) -> (xlsx baytları, uyarılar)

Çözümleme pdf2docx (MIT, Artifex) ile: sayfa düzeni, paragraflar, resimler ve tablolar
(çizgili ve çizgisiz). Ölçüm, 26.11.2026: PyMuPDF'in kendi tablo bulucusu çizgisiz
tabloda sözcükleri bölüyor; pdf2docx'inki banka ekstresinde hücre hücre doğru. Bu yüzden
Excel de aynı çözümlemeden yazılır (docs/cevirme.md).

pdf2docx OpenCV ve NumPy'ı da çeker (ağır): yalnız çevirmede, işlev içinde yüklenir;
düzenleyicinin öbür istekleri bu yükü taşımaz.

PDF de çıkan dosya da yalnız istek belleğinde işlenir, diske yazılmaz.
"""
import io
import logging
import re
import time
from datetime import datetime

from pdf_duzenle import motor

log = logging.getLogger(__name__)

# Bir istekte çözümlemeye ayrılan süre. Vercel işlevi 300 sn'de kesiliyor (vercel.json).
# Ölçüm, 26.11.2026, önizleme (fra1, 1 vCPU): 30 sayfalık ekstre Word'e 61 sn, Excel'e
# 27 sn; Word'de yazım çözümleme kadar sürüyor. Aşılırsa yarım dosya gönderilmez,
# kullanıcıya söylenir.
SURE_BUTCESI = 120.0
AZAMI_HUCRE = 32_767          # Excel hücresinin üst sınırı
SAYFA_ADI = 'Tablolar'

_yuklu = None


def _yukle():
    """pdf2docx'i ilk çevirmede yükler.

    pdf2docx yüklenirken kök kaydediciye `logging.basicConfig(level=INFO)` uyguluyor
    (kök kaydedicide işleyici yoksa). Uygulamanın log düzeyi değişmesin diye
    yükleme anında köke geçici bir boş işleyici konur, sonra kaldırılır.
    """
    global _yuklu
    if _yuklu is None:
        kok = logging.getLogger()
        bos = logging.NullHandler()
        kok.addHandler(bos)
        try:
            from pdf2docx import Converter
            from pdf2docx.common import docx as p2d_docx
        finally:
            kok.removeHandler(bos)
        # pdf2docx bağlantıyı bir w:r içine yazıyor (w:r > w:hyperlink > w:r); OOXML
        # şemasına aykırı (ölçüldü, 0.5.13). Bağlantı paragrafın doğrudan çocuğu olur.
        p2d_docx.add_hyperlink = _baglanti_ekle
        _yuklu = Converter
    return _yuklu


_GUVENLI_BAGLANTI = re.compile(r'(?i)^(https?://|mailto:)')


def _baglanti_ekle(paragraph, url, text):
    """pdf2docx'in `add_hyperlink` yerine. Yalnız http(s) ve e-posta bağlantısı taşınır:
    `file:` / UNC / `javascript:` gibi adresler Word'de tıklanınca zarar verebilir;
    onların metni düz yazı olarak kalır."""
    if not isinstance(url, str) or not _GUVENLI_BAGLANTI.match(url.strip()):
        return paragraph.add_run(text)
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.text.run import Run

    r_id = paragraph.part.relate_to(url.strip(), RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    baglanti = OxmlElement('w:hyperlink')
    baglanti.set(qn('r:id'), r_id)
    baglanti.set(qn('w:history'), '1')
    r = OxmlElement('w:r')
    rpr = OxmlElement('w:rPr')
    stil = OxmlElement('w:rStyle')
    stil.set(qn('w:val'), 'Hyperlink')
    rpr.append(stil)
    r.append(rpr)
    baglanti.append(r)
    paragraph._p.append(baglanti)
    run = Run(r, paragraph)
    run.text = text
    return run


class _Cozum:
    def __init__(self, cv, uyarilar, metin_var):
        self.cv = cv
        self.uyarilar = uyarilar
        self.metin_var = metin_var

    def sayfalar(self):
        return [p for p in self.cv.pages if p.finalized]


def _cozumle(veri, butce=SURE_BUTCESI):
    """PDF'i açar, denetler ve sayfa sayfa çözümler. Çözümlenemeyen sayfa atlanır ve
    uyarıya yazılır (pdf2docx'in kendi döngüsü bunu sessizce yapıyor)."""
    bas = time.monotonic()
    doc = motor._ac(veri)          # sayfa sınırı, şifre, boş belge
    try:
        if not (doc.permissions & motor.fitz.PDF_PERM_COPY):
            raise motor.PdfHatasi('Bu PDF\'in sahibi içeriğinin kopyalanmasına izin vermiyor.', kod='izin')
        metin_var = any(sayfa.get_text('text').strip() for sayfa in doc)
    finally:
        doc.close()

    Converter = _yukle()
    try:
        cv = Converter(stream=veri)
        ayar = cv.default_settings
        ayar.update(ignore_page_error=False, raw_exceptions=True, multi_processing=False)
        cv.load_pages().parse_document(**ayar)
    except motor.PdfHatasi:
        raise
    except Exception:
        log.exception('çevirme: belge çözümlenemedi')
        raise motor.PdfHatasi('PDF çevrilemedi: belgenin düzeni okunamadı.', kod='pdf')

    uyarilar = []
    atlanan = []
    for sayfa in cv.pages:
        if sayfa.skip_parsing:
            continue
        if time.monotonic() - bas > butce:
            cv.close()
            raise motor.PdfHatasi(f'Belge süre sınırı içinde çevrilemedi ({sayfa.id}/{len(cv.pages)} sayfa '
                                  'çözümlendi). Daha az sayfalı bir parçasıyla deneyin.', kod='sure')
        try:
            sayfa.parse(**ayar)
        except Exception:
            log.warning('çevirme: sayfa %d çözümlenemedi', sayfa.id + 1, exc_info=True)
            atlanan.append(sayfa.id + 1)
    if atlanan and len(atlanan) == len(cv.pages):
        cv.close()
        raise motor.PdfHatasi('PDF çevrilemedi: hiçbir sayfanın düzeni okunamadı.', kod='pdf')
    if atlanan:
        uyarilar.append('Şu sayfalar çevrilemedi ve dosyada yok: ' + ', '.join(map(str, atlanan)) + '.')
    return _Cozum(cv, uyarilar, metin_var)


# ---------------------------------------------------------------- Word

def word(veri, butce=SURE_BUTCESI):
    cz = _cozumle(veri, butce)
    from docx import Document
    try:
        belge = Document()
        oz = belge.core_properties
        # python-docx şablonunun "python-docx" yazar / açıklama değerleri dosyaya geçmesin.
        oz.author = oz.last_modified_by = oz.comments = oz.title = oz.subject = oz.keywords = ''
        oz.created = oz.modified = datetime.now()
        for sayfa in cz.sayfalar():
            sayfa.make_docx(belge)
        cikti = io.BytesIO()
        belge.save(cikti)
    except Exception:
        log.exception('çevirme: Word dosyası yazılamadı')
        raise motor.PdfHatasi('Word dosyası oluşturulamadı. Beklenmeyen bir hata oldu.', kod='pdf')
    finally:
        cz.cv.close()
    uyarilar = list(cz.uyarilar)
    if not cz.metin_var:
        uyarilar.append('PDF\'te seçilebilir metin yok (taranmış belge olabilir): sayfalar Word\'e '
                        'resim olarak aktarıldı, metin düzenlenemez.')
    return cikti.getvalue(), uyarilar


# ---------------------------------------------------------------- Excel

def _tablolar(cz):
    """Belge sırasıyla [(sayfa no, sayfadaki sıra, sayfadaki tablo sayısı, TableBlock)].
    Tek satırlı ya da tek sütunlu "tablo" (çerçeveli metin kutusu) alınmaz."""
    sonuc = []
    for sayfa in cz.sayfalar():
        bloklar = [t for bolum in sayfa.sections for sutun in bolum
                   for t in sutun.blocks.table_blocks if t.num_rows >= 2 and t.num_cols >= 2]
        for i, t in enumerate(bloklar):
            sonuc.append((sayfa.id + 1, i, len(bloklar), t))
    return sonuc


def _hucre_metni(hucre):
    if not hucre:
        return None             # birleştirilmiş hücrenin örttüğü yer
    metin = hucre.text or ''
    metin = metin.replace('<image>', '').replace('<NEST TABLE>', '')
    return '\n'.join(s.strip() for s in metin.splitlines() if s.strip())


def _izgara(t):
    """TableBlock → satır listesi; her hücre (metin, (satır, sütun) kaplaması) ya da None."""
    satirlar = []
    for satir in t:
        satirlar.append([(_hucre_metni(h), tuple(h.merged_cells)) if h else None for h in satir])
    return satirlar


def _sutun_sinirlari(t):
    ilk = list(t)[0]
    return [round(h.bbox[0]) for h in ilk if h and h.bbox] + [round(t.bbox[2])]


def _basliklar(satir):
    return [h[0] if h else None for h in satir]


def _grupla(tablolar):
    """Sayfalara bölünmüş aynı tabloyu birleştirir (kullanıcı kararı, 26.11.2026).

    Birleşir: sütun sayısı aynı ve (a) ilk satırı grubun başlığıyla aynı — tekrarlanan
    başlık atlanır — ya da (b) tablo sayfasının ilk tablosu, önceki sayfanın son tablosunun
    devamı ve sütun sınırları 3 pt içinde aynı."""
    gruplar = []
    onceki = None
    for sayfa, sira, adet, t in tablolar:
        izgara = _izgara(t)
        sinir = _sutun_sinirlari(t)
        g = gruplar[-1] if gruplar else None
        if g and onceki and t.num_cols == g['sutun']:
            ayni_baslik = _basliklar(izgara[0]) == g['baslik']
            devam = (sira == 0 and onceki[0] == sayfa - 1 and onceki[1] == onceki[2] - 1
                     and len(sinir) == len(g['sinir'])
                     and all(abs(a - b) <= 3 for a, b in zip(sinir, g['sinir'])))
            if ayni_baslik and onceki[0] != sayfa:
                g['satirlar'].extend(izgara[1:])
                onceki = (sayfa, sira, adet)
                continue
            if devam:
                g['satirlar'].extend(izgara)
                onceki = (sayfa, sira, adet)
                continue
        gruplar.append({'sutun': t.num_cols, 'baslik': _basliklar(izgara[0]), 'sinir': sinir,
                        'satirlar': list(izgara)})
        onceki = (sayfa, sira, adet)
    return gruplar


# Sayı biçimi. Belgede ondalık virgül mü nokta mı kullanıldığı açık örneklerden
# sayılır ("12,50" Türkçe, "12.50" İngilizce); "1.250" gibi iki yana çekilebilen
# değer çoğunluğa göre okunur, eşitlikte Türkçe.
_TR_ACIK = re.compile(r'\d,\d{1,2}(?!\d)|\.\d{3},\d')
_EN_ACIK = re.compile(r'\d\.\d{1,2}(?!\d)|,\d{3}\.\d')
_SAYI = re.compile(
    r'^(?P<isaret>[-−+])?\s*(?P<on>[₺$€])?\s*(?P<govde>\d[\d.,]*)\s*'
    r'(?P<son>TL|₺|TRY|USD|EUR|\$|€)?$')
_YUZDE = re.compile(r'^(?:%\s?(?P<a>[-−]?\d[\d.,]*)|(?P<b>[-−]?\d[\d.,]*)\s?%)$')
_TARIH = re.compile(r'^(\d{1,2})([./])(\d{1,2})\2(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$')
_ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


def _yerel(metinler):
    tr = en = 0
    for m in metinler:
        if m and not _TARIH.match(m.strip()):       # "01.10.2025" ondalık örneği değil
            tr += len(_TR_ACIK.findall(m))
            en += len(_EN_ACIK.findall(m))
    return 'en' if en > tr else 'tr'


def _govde_sayi(govde, yerel):
    """'1.250,50' → (1250.5, 2). Sayı değilse None."""
    binlik, ondalik = ('.', ',') if yerel == 'tr' else (',', '.')
    b, o = re.escape(binlik), re.escape(ondalik)
    if re.fullmatch(rf'\d{{1,3}}({b}\d{{3}})+({o}\d+)?', govde) or re.fullmatch(rf'\d+({o}\d+)?', govde):
        tam, _, kesir = govde.replace(binlik, '').partition(ondalik)
        if not kesir and (len(tam) > 9 or (len(tam) > 1 and tam.startswith('0'))):
            return None     # kimlik / hesap numarası gibi: metin kalır
        if len(tam) + len(kesir) > 15:
            return None     # Excel 15 haneden sonrasını yuvarlar
        return (float(f'{tam}.{kesir}') if kesir else int(tam)), len(kesir)
    return None


def _ondalik_bicimi(basamak):
    return '#,##0' + ('.' + '0' * basamak if basamak else '')


def deger(metin, yerel='tr'):
    """Hücre metni → (değer, Excel sayı biçimi). Sayı / tarih / yüzde değilse (metin, None)."""
    if metin is None:
        return None, None
    m = metin.strip()
    if not m or '\n' in m:
        return metin, None
    t = _TARIH.match(m)
    if t and (t.group(2) == '.' or yerel == 'tr'):
        try:
            gun, ay, yil = int(t.group(1)), int(t.group(3)), int(t.group(4))
            if t.group(5):
                return (datetime(yil, ay, gun, int(t.group(5)), int(t.group(6)), int(t.group(7) or 0)),
                        'dd.mm.yyyy hh:mm')
            return datetime(yil, ay, gun), 'dd.mm.yyyy'
        except ValueError:
            return metin, None
    t = _ISO.match(m)
    if t:
        try:
            return datetime(int(t.group(1)), int(t.group(2)), int(t.group(3))), 'yyyy-mm-dd'
        except ValueError:
            return metin, None
    y = _YUZDE.match(m)
    if y:
        ham = (y.group('a') or y.group('b')).replace('−', '-')
        eksi = ham.startswith('-')
        s = _govde_sayi(ham.lstrip('-'), yerel)
        if s:
            v, basamak = s
            return (-v if eksi else v) / 100, '0' + ('.' + '0' * basamak if basamak else '') + '%'
        return metin, None
    parantez = m.startswith('(') and m.endswith(')')
    if parantez:
        m = m[1:-1].strip()
    s = _SAYI.match(m)
    if not s or (s.group('on') and s.group('son')):
        return metin, None
    sayi = _govde_sayi(s.group('govde'), yerel)
    if sayi is None:
        return metin, None
    v, basamak = sayi
    if s.group('isaret') in ('-', '−') or parantez:
        v = -v
    bicim = _ondalik_bicimi(basamak)
    if s.group('son'):
        bicim += f' "{s.group("son")}"'
    elif s.group('on'):
        bicim = f'"{s.group("on")}"' + bicim
    return v, bicim


def excel(veri, butce=SURE_BUTCESI):
    cz = _cozumle(veri, butce)
    try:
        gruplar = _grupla(_tablolar(cz))
    finally:
        cz.cv.close()
    if not gruplar:
        if not cz.metin_var:
            raise motor.PdfHatasi('PDF\'te seçilebilir metin yok (taranmış belge olabilir); '
                                  'tablo okunamadı.', kod='tablo_yok')
        raise motor.PdfHatasi('Bu PDF\'te tablo bulunamadı. Metni almak için Word\'e çevirin.',
                              kod='tablo_yok')
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    yerel = _yerel(h[0] for g in gruplar for s in g['satirlar'] for h in s if h)
    kitap = Workbook()
    kitap.properties.creator = ''
    sayfa = kitap.active
    sayfa.title = SAYFA_ADI
    kalin = Font(bold=True)
    sar = Alignment(wrap_text=True, vertical='top')
    genislik = {}
    satir_no = 1
    for g in gruplar:
        for i, satir in enumerate(g['satirlar']):
            for j, h in enumerate(satir):
                if h is None:
                    continue
                metin, (n_satir, n_sutun) = h
                if metin:
                    metin = ILLEGAL_CHARACTERS_RE.sub('', metin)[:AZAMI_HUCRE]
                v, bicim = deger(metin, yerel) if metin else (None, None)
                hucre = sayfa.cell(row=satir_no, column=j + 1)
                hucre.value = v
                if isinstance(v, str):
                    hucre.data_type = 's'       # "=" ile başlayan metin formül olmasın
                    if '\n' in v:
                        hucre.alignment = sar
                if bicim:
                    hucre.number_format = bicim
                if i == 0:
                    hucre.font = kalin
                if n_satir > 1 or n_sutun > 1:
                    sayfa.merge_cells(start_row=satir_no, start_column=j + 1,
                                      end_row=satir_no + n_satir - 1, end_column=j + n_sutun)
                elif metin:
                    uzun = max(len(s) for s in metin.split('\n'))
                    genislik[j] = max(genislik.get(j, 0), uzun)
            satir_no += 1
        satir_no += 1           # tablolar arasında bir boş satır
    for j, n in genislik.items():
        sayfa.column_dimensions[get_column_letter(j + 1)].width = min(60, max(8, n * 1.15 + 2))
    cikti = io.BytesIO()
    kitap.save(cikti)
    return cikti.getvalue(), list(cz.uyarilar)
