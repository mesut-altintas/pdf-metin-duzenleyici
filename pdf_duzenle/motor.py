"""PDF metin düzenleme motoru. Flask'a bağımlı değildir.

    analiz(pdf)              -> sayfa görüntüleri + tıklanabilir metin parçaları
    uygula(pdf, duzeltmeler) -> (yeni pdf, uyarılar)

Nasıl değiştirir:
  * Yalnız seçilen parçanın değişen karakterleri silinir (PyMuPDF
    redaksiyonu; resim ve çizgiye dokunmaz, beyaz kutu çizmez). Ortak baş ve
    son ek yerinde kalır.
  * Silme işlemi üst üste binen bağlantı ve açıklamaları da kaldırıyor
    (ölçüldü, PyMuPDF 1.28.2): sayfanın /Annots dizisi önce saklanır, sonra
    aynen geri konur.
  * Yeni metnin yazı tipi sırasıyla:
      1. asil    — aynı yazı tipi nesnesi. Harf → kod eşlemesi ToUnicode'dan
                   (yoksa WinAnsi kodlamasından); gerekli her glifin dosyada
                   gerçekten çizildiği geçici bir sayfada denetlenir (alt küme
                   yazı tiplerinde eşlemede olup çizimi olmayan glif olur).
      2. program — gömülü yazı tipi programı kendi harf tablosuyla.
      3. benzer  — Helvetica / Times / Courier ailesinden en yakını.
  * Sonunda dokunulmayan bütün karakterlerin yerinde durduğu denetlenir;
    durmuyorsa dosya üretilmez (PdfHatasi).
"""
import base64
import difflib
import math
import re
import statistics

import pymupdf as fitz

AZAMI_SAYFA = 40
AZAMI_DUZELTME = 500
AZAMI_EKLEME = 200
AZAMI_RESIM = 20
AZAMI_RESIM_BAYT = 3_000_000
AZAMI_RESIM_PIKSEL = 4096       # en uzun kenar; tarayıcı 1600'e küçültüyor
AZAMI_PARAGRAF = 100
PARAGRAF_KUCULTME = (1.0, 0.97, 0.94, 0.91, 0.88, 0.85)   # en çok %15 (kullanıcı kararı)
STANDART_FONTLAR = ('helv', 'hebo', 'heit', 'hebi', 'tiro', 'tibo', 'tiit', 'tibi',
                    'cour', 'cobo', 'coit', 'cobi')
AZAMI_METIN = 1000

_BAYRAK = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
_DOLU, _KONTUR = 16, 32  # span['char_flags']: dolgu / kontur ile çizilmiş

# Ekranda ve karşılaştırmada kullanılan biçim. Alt küme yazı tiplerinde
# boşluk çoğu zaman U+00A0, tire U+00AD olarak eşlenir (ölçüldü).
_GORUNUM = {' ': ' ', '­': '-', ' ': ' ', ' ': ' ', ' ': ' ',
            ' ': ' ', ' ': ' '}
_LIGATUR = {'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
            'ﬅ': 'st', 'ﬆ': 'st'}

_STD14 = {
    'helvetica': 'helv', 'helvetica-bold': 'hebo', 'helvetica-oblique': 'heit',
    'helvetica-boldoblique': 'hebi', 'times-roman': 'tiro', 'times-bold': 'tibo',
    'times-italic': 'tiit', 'times-bolditalic': 'tibi', 'courier': 'cour',
    'courier-bold': 'cobo', 'courier-oblique': 'coit', 'courier-boldoblique': 'cobi',
}


class PdfHatasi(Exception):
    """Kullanıcıya olduğu gibi gösterilecek hata. `kod` kullanım kaydına yazılır
    (mesaj belgeden alıntı içerebilir, kayda yazılmaz)."""

    def __init__(self, mesaj, kod='pdf'):
        super().__init__(mesaj)
        self.kod = kod


def _gorunum(c):
    return _GORUNUM.get(c) or _LIGATUR.get(c) or c


def _kisalt(metin, n=40):
    metin = ' '.join(metin.split())
    return metin if len(metin) <= n else metin[:n - 1] + '…'


# ---------------------------------------------------------------- açma

def _ac(veri):
    try:
        doc = fitz.open(stream=veri, filetype='pdf')
    except Exception:
        raise PdfHatasi('Dosya PDF olarak açılamadı.')
    if not doc.is_pdf:
        raise PdfHatasi('Dosya PDF değil.')
    if doc.needs_pass:
        raise PdfHatasi('PDF şifreli. Şifresiz bir kopyasıyla deneyin.', kod='sifreli')
    if doc.page_count == 0:
        raise PdfHatasi('PDF\'te sayfa yok.')
    if doc.page_count > AZAMI_SAYFA:
        raise PdfHatasi(f'PDF {doc.page_count} sayfa; en fazla {AZAMI_SAYFA} sayfa açılabilir.', kod='sayfa_siniri')
    return doc


# ---------------------------------------------------------------- metin parçaları

class _Harf:
    __slots__ = ('c', 'g', 'o', 'k', 'sentetik')

    def __init__(self, ham):
        self.c = ham['c']
        self.g = _gorunum(self.c)
        self.o = fitz.Point(ham['origin'])
        self.k = fitz.Rect(ham['bbox'])
        self.sentetik = bool(ham.get('synthetic'))


class _Parca:
    """Aynı satırda, aynı yazı tipi / boyut / renkteki kesintisiz metin."""

    def __init__(self, pid, sayfa, span, yon, wmode):
        self.id = pid
        self.sayfa = sayfa
        self.font = span['font']
        self.boyut = float(span['size'])
        self.renk = int(span['color'])
        self.bayrak = int(span['flags'])
        self.kutu = fitz.Rect(span['bbox'])
        self.yon = (float(yon[0]), float(yon[1]))
        self.harfler = [_Harf(h) for h in span['chars']]
        self.bas = []
        parcalar = []
        n = 0
        for h in self.harfler:
            self.bas.append(n)
            parcalar.append(h.g)
            n += len(h.g)
        self.bas.append(n)
        self.metin = ''.join(parcalar)
        self.neden = None
        if wmode:
            self.neden = 'dikey yazım desteklenmiyor'
        elif not (int(span.get('char_flags', _DOLU)) & (_DOLU | _KONTUR)) or span.get('alpha', 255) == 0:
            self.neden = 'görünmez metin (taranmış belgenin metin katmanı olabilir)'

    # Satır yönündeki konum (başlangıç noktası 0)
    def t(self, nokta):
        o = self.harfler[0].o
        return (nokta.x - o.x) * self.yon[0] + (nokta.y - o.y) * self.yon[1]

    def nokta(self, t):
        o = self.harfler[0].o
        return fitz.Point(o.x + t * self.yon[0], o.y + t * self.yon[1])

    def bitis(self, i):
        """i. harfin satır yönündeki bitişi."""
        k = self.harfler[i].k
        return max(self.t(p) for p in (k.tl, k.tr, k.bl, k.br))

    def yatay(self):
        return abs(self.yon[0] - 1) < 1e-3 and abs(self.yon[1]) < 1e-3


def _ham_metin(page):
    """Yalnız sayfa içeriğinin metni (açıklama görünümleri hariç), döndürülmemiş düzlemde."""
    rot = page.rotation
    if rot:
        page.set_rotation(0)
    try:
        tp = fitz.TextPage(page.get_displaylist(annots=False).get_textpage(flags=_BAYRAK))
        tp.parent = page
        return tp.extractRAWDICT()
    finally:
        if rot:
            page.set_rotation(rot)


def _parcalar(page, pno):
    d = _ham_metin(page)
    sonuc = []
    for bno, blok in enumerate(d.get('blocks', [])):
        if blok.get('type', 0) != 0:
            continue
        for lno, satir in enumerate(blok.get('lines', [])):
            for sno, span in enumerate(satir.get('spans', [])):
                if not span.get('chars') or span.get('size', 0) < 1:
                    continue
                p = _Parca(f'{pno}-{bno}-{lno}-{sno}', pno, span, satir['dir'], satir.get('wmode', 0))
                if p.metin.strip():
                    sonuc.append(p)
    return sonuc


# ---------------------------------------------------------------- analiz

def analiz(veri, yalniz_resim=False, resim_butcesi=4_000_000):
    doc = _ac(veri)
    sayfalar = []
    for pno, page in enumerate(doc):
        kayit = {'no': pno + 1}
        if not yalniz_resim:
            rotm = page.rotation_matrix
            pr = page.rect
            parcalar = []
            liste = _parcalar(page, pno)
            hizalar = _hiza_onerileri(liste)
            for p in liste:
                r = p.kutu * rotm
                k = [(r.x0 - pr.x0) / pr.width, (r.y0 - pr.y0) / pr.height,
                     (r.x1 - pr.x0) / pr.width, (r.y1 - pr.y0) / pr.height]
                parcalar.append({
                    'id': p.id,
                    't': p.metin,
                    'k': [round(min(max(v, 0.0), 1.0), 5) for v in k],
                    'font': p.font,
                    'boyut': round(p.boyut, 2),
                    'duzenlenebilir': p.neden is None,
                    'neden': p.neden,
                    'hiza': hizalar.get(p.id, 'sol'),
                })
            kayit['parcalar'] = parcalar
            kayit['pt'] = [round(pr.width, 2), round(pr.height, 2)]
            kayit['yazi'] = _yazi_istatistigi(liste)
            kayit['paragraflar'] = []
            for par in _paragraflar(liste):
                r = par.kutu * rotm
                k = [(r.x0 - pr.x0) / pr.width, (r.y0 - pr.y0) / pr.height,
                     (r.x1 - pr.x0) / pr.width, (r.y1 - pr.y0) / pr.height]
                kayit['paragraflar'].append({
                    'id': par.id, 't': par.metin, 'satir': len(par.satirlar), 'hiza': par.hiza,
                    'k': [round(min(max(v, 0.0), 1.0), 5) for v in k],
                    'parcalar': [p.id for p in par.parcalar],
                })
        sayfalar.append(kayit)
    _resimleri_ekle(doc, sayfalar, resim_butcesi)
    return {'sayfalar': sayfalar}


def _yazi_istatistigi(parcalar, sinir=12):
    """Sayfada kullanılan yazı tipleri, en çok kullanılandan: yeni metin eklemenin
    varsayılanları (yazı tipi, boyut, renk) buradan seçilir."""
    toplam = {}
    for p in parcalar:
        if p.neden:
            continue
        harf = sum(1 for h in p.harfler if not h.c.isspace() and not h.sentetik)
        if not harf:
            continue
        k = toplam.setdefault(p.font, {'ad': p.font, 'bayrak': p.bayrak, 'harf': 0, 'boyut': {}, 'renk': {}})
        k['harf'] += harf
        b = round(p.boyut, 1)
        k['boyut'][b] = k['boyut'].get(b, 0) + harf
        k['renk'][p.renk] = k['renk'].get(p.renk, 0) + harf
    sonuc = []
    for k in sorted(toplam.values(), key=lambda v: -v['harf'])[:sinir]:
        sonuc.append({
            'ad': k['ad'], 'bayrak': k['bayrak'], 'harf': k['harf'],
            'boyut': max(k['boyut'], key=k['boyut'].get),
            'renk': '#%06x' % max(k['renk'], key=k['renk'].get),
        })
    return sonuc


def _hiza_onerileri(parcalar):
    """Sütun hizası önerisi: alt alta duran metinlerden sağ kenarı aynı olanlar
    sağa, ortası aynı olanlar ortaya dayalı sayılır. Yalnız düzenleme
    penceresinin varsayılanı; kullanıcı değiştirebilir."""
    kutular = []
    for p in parcalar:
        dolu = [h.k for h in p.harfler if not h.c.isspace() and not h.sentetik]
        if not dolu or not p.yatay():
            continue
        x0, x1 = min(k.x0 for k in dolu), max(k.x1 for k in dolu)
        kutular.append((p.id, x0, x1, p.harfler[0].o.y))
    sonuc = {}
    for pid, x0, x1, y in kutular:
        sol = sag = orta = 0
        for qid, q0, q1, qy in kutular:
            if qid == pid or abs(qy - y) < 1:
                continue
            if abs(q0 - x0) < 0.6:
                sol += 1
            elif abs(q1 - x1) < 0.6:
                sag += 1
            elif abs((q0 + q1) / 2 - (x0 + x1) / 2) < 0.6:
                orta += 1
        if sag and sag > sol:
            sonuc[pid] = 'sag'
        elif orta >= 2 and orta > sol and orta > sag:
            sonuc[pid] = 'orta'
    return sonuc


def _resimleri_ekle(doc, sayfalar, butce):
    dpi = 110.0
    while True:
        resimler = []
        toplam = 0
        for page in doc:
            uzun = max(page.rect.width, page.rect.height) or 1
            z = min(dpi, 1700 * 72 / uzun) / 72
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            veri = pix.tobytes('jpeg', jpg_quality=80)
            toplam += len(veri)
            resimler.append((pix.width, pix.height, veri))
        if toplam * 4 / 3 <= butce:
            break
        if dpi <= 40:
            raise PdfHatasi('Sayfaların görüntüsü aktarılamayacak kadar büyük. Daha az sayfalı bir PDF deneyin.', kod='gorunum')
        dpi *= 0.75
    for kayit, (gen, yuk, veri) in zip(sayfalar, resimler):
        kayit['gen'] = gen
        kayit['yuk'] = yuk
        kayit['resim'] = 'data:image/jpeg;base64,' + base64.b64encode(veri).decode('ascii')


# ---------------------------------------------------------------- PDF nesnesi okuma

def _sayi(metin, varsayilan=None):
    try:
        return float(metin)
    except (TypeError, ValueError):
        return varsayilan


def _ref(deger):
    m = re.fullmatch(r'\s*(\d+)\s+0\s+R\s*', deger or '')
    return int(m.group(1)) if m else None


def _anahtar(doc, xref, anahtar):
    """(tür, değer); dolaylı başvuru çözülmüş olarak."""
    tur, deger = doc.xref_get_key(xref, anahtar)
    if tur == 'xref':
        hedef = _ref(deger)
        if hedef:
            return 'nesne', doc.xref_object(hedef, compressed=True)
    return tur, deger


def _dizi(doc, xref, anahtar):
    tur, deger = _anahtar(doc, xref, anahtar)
    if tur not in ('array', 'nesne') or not deger.strip().startswith('[') or ' R' in deger:
        return None
    return re.findall(r'\[|\]|-?\d*\.?\d+', deger)


def _hex(jeton):
    temiz = re.sub(r'[^0-9A-Fa-f]', '', jeton)
    return int(temiz or '0', 16), len(temiz)


def _utf16(jeton):
    temiz = re.sub(r'[^0-9A-Fa-f]', '', jeton)
    if len(temiz) % 2:
        temiz += '0'
    return bytes.fromhex(temiz).decode('utf-16-be', errors='replace')


def _cmap(metin):
    """ToUnicode CMap → {kod: unicode}."""
    sonuc = {}
    for govde in re.findall(r'beginbfchar(.*?)endbfchar', metin, re.S):
        h = re.findall(r'<[0-9A-Fa-f\s]*>', govde)
        for kaynak, hedef in zip(h[0::2], h[1::2]):
            sonuc[_hex(kaynak)[0]] = _utf16(hedef)
    for govde in re.findall(r'beginbfrange(.*?)endbfrange', metin, re.S):
        j = re.findall(r'<[0-9A-Fa-f\s]*>|\[|\]', govde)
        i = 0
        while i + 2 < len(j):
            if j[i] in '[]' or j[i + 1] in '[]':
                i += 1
                continue
            lo, hi = _hex(j[i])[0], _hex(j[i + 1])[0]
            if hi < lo or hi - lo > 0xFFFF:
                i += 3
                continue
            if j[i + 2] == '[':
                k = i + 3
                ofs = 0
                while k < len(j) and j[k] != ']':
                    if lo + ofs <= hi:
                        sonuc[lo + ofs] = _utf16(j[k])
                    ofs += 1
                    k += 1
                i = k + 1
            else:
                temiz = re.sub(r'[^0-9A-Fa-f]', '', j[i + 2])
                if len(temiz) % 2:
                    temiz += '0'
                taban = bytes.fromhex(temiz)
                deger = int.from_bytes(taban, 'big') if taban else 0
                for ofs in range(hi - lo + 1):
                    try:
                        u = (deger + ofs).to_bytes(len(taban) or 2, 'big').decode('utf-16-be', errors='replace')
                    except OverflowError:
                        break
                    sonuc[lo + ofs] = u
                i += 3
        if len(sonuc) > 200_000:
            break
    return sonuc


def _winansi():
    sonuc = {}
    for kod in range(32, 256):
        try:
            sonuc[kod] = bytes([kod]).decode('cp1252')
        except UnicodeDecodeError:
            pass
    return sonuc


_WINANSI = _winansi()


def _basit_kodlama(doc, xref):
    """ToUnicode'u olmayan tek baytlık yazı tipi için {kod: unicode}; bilinmiyorsa None."""
    tur, deger = _anahtar(doc, xref, 'Encoding')
    farklar = None
    if tur == 'name':
        taban = deger
    elif tur in ('dict', 'nesne'):
        m = re.search(r'/BaseEncoding\s*/(\w+)', deger)
        taban = '/' + m.group(1) if m else None
        m = re.search(r'/Differences\s*\[(.*?)\]', deger, re.S)
        farklar = m.group(1) if m else None
    else:
        taban = None
    if taban not in ('/WinAnsiEncoding', None):
        return None
    if taban is None:
        # Kodlama yazılmamış: yalnız standart kodlamayla WinAnsi'nin ortak
        # olduğu ASCII harf ve rakamlarına güvenilir.
        kodlar = {k: v for k, v in _WINANSI.items() if 32 <= k < 127 and k not in (0x27, 0x60)}
    else:
        kodlar = dict(_WINANSI)
    if farklar:
        kod = None
        for jeton in re.findall(r'-?\d+|/[^\s/\[\]]+', farklar):
            if jeton[0] != '/':
                kod = int(jeton)
                continue
            if kod is None:
                continue
            u = fitz.glyph_name_to_unicode(jeton[1:])
            if u and u != 0xFFFD:
                kodlar[kod] = chr(u)
            else:
                kodlar.pop(kod, None)
            kod += 1
    return kodlar


class _FontKaynagi:
    """Sayfada kullanılan bir yazı tipi nesnesi ve onu yeniden kullanmak için gerekenler."""

    def __init__(self, doc, xref):
        self.xref = xref
        self.gecerli = False
        self.nbayt = 1
        self.ters = {}         # görünür harf -> [kod, ...] (tam eşleşen önce)
        self._gen = {}
        self._varsayilan_gen = 0.0
        self._std = None
        try:
            self._kur(doc)
        except Exception:
            self.gecerli = False

    def _kur(self, doc):
        xref = self.xref
        tur = doc.xref_get_key(xref, 'Subtype')[1].lstrip('/')
        if tur == 'Type0':
            if doc.xref_get_key(xref, 'Encoding') != ('name', '/Identity-H'):
                return
            self.nbayt = 2
            m = re.search(r'(\d+)\s+0\s+R', doc.xref_get_key(xref, 'DescendantFonts')[1])
            if not m:
                return
            alt = int(m.group(1))
            self._varsayilan_gen = _sayi(_anahtar(doc, alt, 'DW')[1], 1000.0)
            jetonlar = _dizi(doc, alt, 'W')
            if jetonlar:
                self._cid_genislik(jetonlar[1:-1])
        elif tur in ('TrueType', 'Type1', 'MMType1'):
            ilk = _sayi(_anahtar(doc, xref, 'FirstChar')[1])
            jetonlar = _dizi(doc, xref, 'Widths')
            if ilk is not None and jetonlar:
                for i, j in enumerate(x for x in jetonlar if x not in '[]'):
                    self._gen[int(ilk) + i] = float(j)
                tur_fd, fd = _anahtar(doc, xref, 'FontDescriptor')
                m = re.search(r'/MissingWidth\s+(-?[\d.]+)', fd or '')
                self._varsayilan_gen = float(m.group(1)) if m else 0.0
            else:
                ad = re.sub(r'^[A-Z]{6}\+', '', doc.xref_get_key(xref, 'BaseFont')[1].lstrip('/'))
                kod = _STD14.get(ad.lower())
                if not kod:
                    return
                self._std = fitz.Font(kod)
        else:
            return

        tu = doc.xref_get_key(xref, 'ToUnicode')
        if tu[0] == 'xref' and _ref(tu[1]):
            kodlar = _cmap(doc.xref_stream(_ref(tu[1])).decode('latin-1'))
        elif self.nbayt == 1:
            kodlar = _basit_kodlama(doc, xref)
        else:
            kodlar = None
        if not kodlar:
            return
        sinir = 0xFFFF if self.nbayt == 2 else 0xFF
        for kod, u in sorted(kodlar.items()):
            if kod > sinir or not u:
                continue
            g = ''.join(_gorunum(c) for c in u)
            if len(g) != 1:
                continue
            self.ters.setdefault(g, []).append((0 if u == g else 1, kod))
        for g in self.ters:
            self.ters[g] = [k for _, k in sorted(self.ters[g])]
        self.gecerli = bool(self.ters)

    def _cid_genislik(self, j):
        i = 0
        while i < len(j):
            if j[i] in '[]':
                i += 1
                continue
            ilk = int(float(j[i]))
            if i + 1 < len(j) and j[i + 1] == '[':
                k = i + 2
                while k < len(j) and j[k] != ']':
                    self._gen[ilk + (k - i - 2)] = float(j[k])
                    k += 1
                i = k + 1
            elif i + 2 < len(j):
                son, w = int(float(j[i + 1])), float(j[i + 2])
                for kod in range(ilk, min(son, ilk + 0xFFFF) + 1):
                    self._gen[kod] = w
                i += 3
            else:
                break

    def genislik(self, kod, harf):
        """Kodun ilerleme genişliği (1000 birim)."""
        if self._std is not None:
            return self._std.glyph_advance(ord(harf)) * 1000
        return self._gen.get(kod, self._varsayilan_gen)


# ---------------------------------------------------------------- yazı tipi seçimi

class _Belge:
    """Bir uygulama boyunca belgeyle ilgili önbellekler."""

    def __init__(self, doc):
        self.doc = doc
        self.sayfa_sayisi = doc.page_count   # geçici denetim sayfaları sayılmasın
        self.fontlar = {}       # xref -> _FontKaynagi
        self.murekkep = {}      # (xref, kod) -> bool
        self.programlar = {}    # xref -> fitz.Font | None
        self.sayfa_fontlari = {}

    def fonts(self, pno):
        if pno not in self.sayfa_fontlari:
            liste = []
            for f in self.doc[pno].get_fonts(full=True):
                if f[0] and f[0] not in [x for x, _ in liste]:
                    liste.append((f[0], re.sub(r'^[A-Z]{6}\+', '', f[3])))
            self.sayfa_fontlari[pno] = liste
        return self.sayfa_fontlari[pno]

    def _adaylar_sayfada(self, pno, ad):
        liste = self.fonts(pno)
        tam = [x for x, a in liste if a == ad]
        return tam or [x for x, a in liste if a and ad and (a.startswith(ad) or ad.startswith(a))]

    def adaylar(self, pno, ad):
        bulunan = self._adaylar_sayfada(pno, ad)
        if bulunan:
            return bulunan
        # Yeni metin başka sayfadaki bir yazı tipiyle istenebilir: belgenin geri kalanı.
        for diger in range(self.sayfa_sayisi):
            if diger != pno:
                bulunan = self._adaylar_sayfada(diger, ad)
                if bulunan:
                    return bulunan
        return []

    def kaynak(self, xref):
        if xref not in self.fontlar:
            self.fontlar[xref] = _FontKaynagi(self.doc, xref)
        return self.fontlar[xref]

    def program(self, xref):
        if xref not in self.programlar:
            font = None
            try:
                _ad, uzanti, _tur, tampon = self.doc.extract_font(xref)
                if tampon and uzanti in ('ttf', 'otf', 'cff', 'pfa', 'pfb'):
                    font = fitz.Font(fontbuffer=tampon)
            except Exception:
                font = None
            self.programlar[xref] = font
        return self.programlar[xref]

    # Geçici sayfada glif çizimi denetimi -----------------------------------
    def _murekkep_olc(self, adet, ciz):
        hucre, sutun = 32, 16
        satir = max(1, math.ceil(adet / sutun))
        gen, yuk = sutun * hucre, satir * hucre
        page = self.doc.new_page(-1, width=gen, height=yuk)
        no = page.number
        try:
            hucreler = []
            for i in range(adet):
                x = (i % sutun) * hucre
                y = (i // sutun) * hucre
                hucreler.append((x, y))
            ciz(page, hucreler, yuk)
            pix = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
            ornek = pix.samples
            w = pix.stride
            sonuc = []
            for x, y in hucreler:
                var = False
                for yy in range(y, min(y + hucre, pix.height)):
                    if min(ornek[yy * w + x: yy * w + x + hucre]) < 160:
                        var = True
                        break
                sonuc.append(var)
            return sonuc
        finally:
            self.doc.delete_page(no)

    def kodlarda_murekkep(self, fk, kodlar):
        eksik = [k for k in kodlar if (fk.xref, k) not in self.murekkep]
        if eksik:
            def ciz(page, hucreler, yuk):
                ad = _font_kaydet(self.doc, page, fk.xref)
                parca = []
                for (x, y), kod in zip(hucreler, eksik):
                    parca.append(f'BT /{ad} 20 Tf 1 0 0 1 {x + 6} {yuk - (y + 24)} Tm '
                                 f'<{kod:0{2 * fk.nbayt}X}> Tj ET')
                fitz.TOOLS._insert_contents(page, '\n'.join(parca).encode('latin-1'), 1)
            for kod, var in zip(eksik, self._murekkep_olc(len(eksik), ciz)):
                self.murekkep[(fk.xref, kod)] = var
        return {k for k in kodlar if self.murekkep[(fk.xref, k)]}

    def fontta_murekkep(self, font, harfler):
        harfler = list(harfler)

        def ciz(page, hucreler, _yuk):
            tw = fitz.TextWriter(page.rect)
            for (x, y), h in zip(hucreler, harfler):
                tw.append((x + 6, y + 24), h, font=font, fontsize=20)
            tw.write_text(page)
        return {h for h, var in zip(harfler, self._murekkep_olc(len(harfler), ciz)) if var}


def _font_kaydet(doc, page, font_xref):
    """Sayfanın /Resources/Font sözlüğüne font nesnesini ekler, kaynak adını döner."""
    tur, deger = doc.xref_get_key(page.xref, 'Resources')
    if tur == 'xref':
        hedef, yol = _ref(deger), ''
    elif tur == 'dict':
        hedef, yol = page.xref, 'Resources/'
    else:
        # Kalıtılmış kaynaklar: sayfaya kopyalanır (aynı nesneye başvuru).
        kalit = None
        ust = page.xref
        for _ in range(32):
            ust_tur, ust_deger = doc.xref_get_key(ust, 'Parent')
            if ust_tur != 'xref':
                break
            ust = _ref(ust_deger)
            r_tur, r_deger = doc.xref_get_key(ust, 'Resources')
            if r_tur in ('xref', 'dict'):
                kalit = r_deger
                break
        doc.xref_set_key(page.xref, 'Resources', kalit or '<<>>')
        return _font_kaydet(doc, page, font_xref)

    f_tur, f_deger = doc.xref_get_key(hedef, yol + 'Font' if yol else 'Font')
    if f_tur == 'xref':
        hedef, yol = _ref(f_deger), ''
        mevcut = doc.xref_object(hedef, compressed=True)
    elif f_tur == 'dict':
        yol = yol + 'Font/'
        mevcut = f_deger
    else:
        doc.xref_set_key(hedef, yol + 'Font', '<<>>')
        yol = yol + 'Font/'
        mevcut = ''
    for ad, ref in re.findall(r'/([^\s/<>\[\]()]+)\s*(\d+\s+0\s+R)', mevcut):
        if _ref(ref) == font_xref and ad.startswith('FEd'):
            return ad
    n = 0
    while f'/FEd{n}' in mevcut:
        n += 1
    ad = f'FEd{n}'
    doc.xref_set_key(hedef, yol + ad, f'{font_xref} 0 R')
    return ad


class _AsilYazim:
    tur = 'asil'

    def __init__(self, fk, kodlar, bosluk):
        self.fk = fk
        self.kodlar = kodlar      # [(kod | None, harf)]
        self.bosluk = bosluk      # eşlemesi olmayan boşluğun genişliği (1000 birim)
        self.ad = None

    def genislik(self, boyut, tc):
        toplam = 0.0
        for kod, harf in self.kodlar:
            if kod is None:
                toplam += self.bosluk * boyut / 1000
            else:
                toplam += self.fk.genislik(kod, harf) * boyut / 1000 + tc
        return toplam

    def yaz(self, doc, page, nokta, yon, boyut, renk, tc, sx):
        ad = _font_kaydet(doc, page, self.fk.xref)
        ters = ~page.transformation_matrix
        p0 = nokta * ters
        p1 = fitz.Point(nokta.x + yon[0], nokta.y + yon[1]) * ters
        vx, vy = p1.x - p0.x, p1.y - p0.y
        n = math.hypot(vx, vy) or 1.0
        vx, vy = vx / n, vy / n
        tj = []
        hex_ = []
        for kod, _harf in self.kodlar:
            if kod is None:
                if hex_:
                    tj.append('<' + ''.join(hex_) + '>')
                    hex_ = []
                tj.append(_f(-self.bosluk))
            else:
                hex_.append(f'{kod:0{2 * self.fk.nbayt}X}')
        if hex_:
            tj.append('<' + ''.join(hex_) + '>')
        r, g, b = _rgb(renk)
        icerik = (
            f'q {_f(r)} {_f(g)} {_f(b)} rg BT 0 Tr 0 Ts 0 Tw {_f(tc)} Tc {_f(sx * 100)} Tz '
            f'/{ad} {_f(boyut)} Tf {_f(vx)} {_f(vy)} {_f(-vy)} {_f(vx)} {_f(p0.x)} {_f(p0.y)} Tm '
            f'[{" ".join(tj)}] TJ ET Q\n'
        )
        fitz.TOOLS._insert_contents(page, icerik.encode('latin-1'), 1)


class _FontYazim:
    fk = None

    def __init__(self, tur, font):
        self.tur = tur            # 'program' | 'benzer'
        self.font = font

    def genislik(self, boyut, _tc):
        return self.metin_genislik * boyut

    def hazirla(self, metin):
        self.metin = metin
        self.metin_genislik = self.font.text_length(metin, fontsize=1)
        return self

    def yaz(self, doc, page, nokta, yon, boyut, renk, _tc, sx):
        tw = fitz.TextWriter(page.rect)
        tw.append(nokta, self.metin, font=self.font, fontsize=boyut)
        dx, dy = yon
        # TextWriter morph matrisi y'si yukarı bakan düzlemde çalışıyor (ölçüldü).
        m = fitz.Matrix(sx, 0, 0, 1, 0, 0) * fitz.Matrix(dx, -dy, dy, dx, 0, 0)
        kimlik = all(abs(a - b) < 1e-6 for a, b in zip(m, (1, 0, 0, 1, 0, 0)))
        tw.write_text(page, color=_rgb(renk), morph=None if kimlik else (nokta, m))


def _f(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def _rgb(renk):
    return ((renk >> 16) & 255) / 255, ((renk >> 8) & 255) / 255, (renk & 255) / 255


def _benzer_kod(p):
    ad = p.font.lower()
    kalin = bool(p.bayrak & 16) or any(s in ad for s in ('bold', 'black', 'heavy', 'semibold', 'demi'))
    egik = bool(p.bayrak & 2) or any(s in ad for s in ('italic', 'oblique'))
    if p.bayrak & 8 or any(s in ad for s in ('courier', 'mono', 'consol')):
        aile = ('cour', 'cobo', 'coit', 'cobi')
    elif p.bayrak & 4 or any(s in ad for s in ('times', 'georgia', 'serif', 'roman', 'garamond',
                                                 'cambria', 'book', 'minion', 'palatino')):
        aile = ('tiro', 'tibo', 'tiit', 'tibi')
    else:
        aile = ('helv', 'hebo', 'heit', 'hebi')
    return aile[(1 if kalin else 0) + (2 if egik else 0)]


def _fk_secim(belge, fk, gerekli):
    """Asıl yazı tipinde her harf için çizimi olan bir kod. ({harf: kod | None}, eksikler)."""
    secim, eksik, aday = {}, [], {}
    for h in gerekli:
        kodlar = fk.ters.get(h)
        if h == ' ':
            secim[' '] = kodlar[0] if kodlar else None   # None: boşluk aralıkla verilir
        elif kodlar:
            aday[h] = kodlar
        else:
            eksik.append(h)
    var = belge.kodlarda_murekkep(fk, sorted({k for ks in aday.values() for k in ks}))
    for h, ks in aday.items():
        uygun = [k for k in ks if k in var]
        if uygun:
            secim[h] = uygun[0]
        else:
            eksik.append(h)
    return secim, eksik


def _harf_listesi(harfler):
    return ', '.join(f'“{h}”' for h in harfler[:8]) + (' …' if len(harfler) > 8 else '')


def _yazim_sec(belge, p, metin, istenen):
    """(yazım, uyarı | None). metin boş değil.

    Otomatikte: bütün harfler asıl yazı tipindeyse o; değilse yazı tipinin
    kendi programı hepsini çiziyorsa o; değilse asıl yazı tipinde olan harfler
    asılla, olmayanlar benzer yazı tipiyle (harf harf karma).
    """
    gerekli = list(dict.fromkeys(metin))
    harfler = [h for h in gerekli if h != ' ']
    benzer = fitz.Font(_benzer_kod(p))
    bosluk = 250.0 if (p.bayrak & 4) else 278.0
    uyari = []
    en_iyi = None
    if istenen == 'otomatik':
        adaylar = belge.adaylar(p.sayfa, p.font)
        for xref in adaylar:
            fk = belge.kaynak(xref)
            if not fk.gecerli:
                continue
            secim, eksik = _fk_secim(belge, fk, gerekli)
            if en_iyi is None or len(eksik) < len(en_iyi[2]):
                en_iyi = (fk, secim, eksik)
            if not eksik:
                return _AsilYazim(fk, [(secim[h], h) for h in metin], bosluk), None
        for xref in adaylar:
            font = belge.program(xref)
            if font is not None and all(font.has_glyph(ord(h)) for h in gerekli):
                if len(belge.fontta_murekkep(font, harfler)) == len(harfler):
                    return _FontYazim('program', font).hazirla(metin), None
        if en_iyi:
            uyari.append(f'asıl yazı tipinde {_harf_listesi(en_iyi[2])} yok; bu harfler benzer yazı '
                         f'tipiyle ({benzer.name}) yazıldı')
            if len(en_iyi[2]) >= len(harfler):
                en_iyi = None   # karışacak asıl harf yok
        else:
            uyari.append(f'asıl yazı tipi bu dosyada yeniden kullanılamıyor; benzer yazı tipiyle '
                         f'({benzer.name}) yazıldı')
    olmayan = [h for h in harfler if not benzer.has_glyph(ord(h))]
    if en_iyi:
        fk, secim, eksik = en_iyi
        eksik = set(eksik)
        parcalar = []
        tur = None
        for h in metin:
            h_tur = tur if (h == ' ' and tur) else ('benzer' if h in eksik else 'asil')
            if h_tur != tur:
                parcalar.append([h_tur, ''])
                tur = h_tur
            parcalar[-1][1] += h
        yazimlar = []
        for tur, parca in parcalar:
            if tur == 'asil':
                yazimlar.append(_AsilYazim(fk, [(secim.get(h), h) for h in parca], bosluk))
            else:
                yazimlar.append(_FontYazim('benzer', benzer).hazirla(parca))
        yazim = _KarmaYazim(yazimlar, fk)
        olmayan = [h for h in olmayan if h in eksik]
    else:
        yazim = _FontYazim('benzer', benzer).hazirla(metin)
    if olmayan:
        uyari.append(f'{benzer.name} yazı tipinde {_harf_listesi(olmayan)} yok, boş görünebilir')
    return yazim, ('; '.join(uyari) or None)


class _KarmaYazim:
    """Asıl yazı tipinde olan harfler asılla, olmayanlar benzer yazı tipiyle; ardışık."""

    def __init__(self, yazimlar, fk):
        self.yazimlar = yazimlar
        self.fk = fk

    def genislik(self, boyut, tc):
        return sum(y.genislik(boyut, tc) for y in self.yazimlar)

    def yaz(self, doc, page, nokta, yon, boyut, renk, tc, sx):
        t = 0.0
        for y in self.yazimlar:
            y.yaz(doc, page, fitz.Point(nokta.x + yon[0] * t, nokta.y + yon[1] * t),
                  yon, boyut, renk, tc, sx)
            t += y.genislik(boyut, tc) * sx


def _tc_tahmini(p, yazim):
    """Asıl metnin harf aralığı (Tc) tahmini; yalnız asıl yazı tipinde."""
    fk = getattr(yazim, 'fk', None)
    if fk is None:
        return 0.0
    fark = []
    for i in range(len(p.harfler) - 1):
        a, b = p.harfler[i], p.harfler[i + 1]
        if a.sentetik or b.sentetik or a.c.isspace() or len(a.g) != 1:
            continue
        kodlar = fk.ters.get(a.g)
        if not kodlar:
            continue
        beklenen = fk.genislik(kodlar[0], a.g) * p.boyut / 1000
        fark.append(p.t(b.o) - p.t(a.o) - beklenen)
    if len(fark) < 3:
        return 0.0
    tc = statistics.median(fark)
    return tc if abs(tc) >= 0.03 * p.boyut else 0.0


# ---------------------------------------------------------------- uygulama planı

class _Plan:
    def __init__(self, p, silinen, metin, nokta, yazim, tc, sx, uyarilar):
        self.p = p
        self.silinen = silinen      # silinecek harf indeksleri
        self.metin = metin          # yazılacak metin ('' ise yalnız silinir)
        self.nokta = nokta          # yazının başlangıç noktası (taban çizgisi)
        self.yazim = yazim
        self.tc = tc
        self.sx = sx
        self.uyarilar = uyarilar


def _ortak_bas(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _dogrula(duzeltmeler, bos_olabilir=False):
    if not isinstance(duzeltmeler, list):
        raise PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
    if not duzeltmeler and not bos_olabilir:
        raise PdfHatasi('Değiştirilecek ya da eklenecek metin yok.', kod='liste')
    if len(duzeltmeler) > AZAMI_DUZELTME:
        raise PdfHatasi(f'Bir seferde en fazla {AZAMI_DUZELTME} değişiklik yapılabilir.', kod='liste')
    sonuc = []
    gorulen = set()
    for d in duzeltmeler:
        if not isinstance(d, dict):
            raise PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
        pid = d.get('id')
        if not isinstance(pid, str) or not re.fullmatch(r'\d{1,4}-\d{1,6}-\d{1,6}-\d{1,6}', pid):
            raise PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
        if pid in gorulen:
            raise PdfHatasi('Aynı metin iki kez değiştirilmiş.', kod='liste')
        gorulen.add(pid)
        eski, yeni = d.get('eski'), d.get('yeni')
        if not isinstance(eski, str) or not isinstance(yeni, str):
            raise PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
        yeni = re.sub(r'[\r\n\t]+', ' ', yeni)
        yeni = ''.join(c for c in yeni if c == ' ' or c.isprintable())
        if len(yeni) > AZAMI_METIN:
            raise PdfHatasi(f'Yeni metin en fazla {AZAMI_METIN} karakter olabilir.', kod='liste')
        hiza = d.get('hiza', 'sol')
        font = d.get('font', 'otomatik')
        if hiza not in ('sol', 'sag', 'orta') or font not in ('otomatik', 'benzer'):
            raise PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
        sonuc.append({'id': pid, 'eski': eski, 'yeni': yeni, 'hiza': hiza, 'font': font,
                      'sigdir': bool(d.get('sigdir'))})
    return sonuc


def _planla(belge, p, d):
    eski, yeni, hiza = p.metin, d['yeni'], d['hiza']
    n = len(p.harfler)
    k = _ortak_bas(eski, yeni)
    m = _ortak_bas(eski[::-1], yeni[::-1])
    m = min(m, len(eski) - k, len(yeni) - k)
    # Gösterim indekslerini harf indekslerine çevir (bağlı harfler bölünmez).
    a = 0
    while a < n and p.bas[a + 1] <= k:
        a += 1
    b = n
    while b > a and p.bas[b - 1] >= len(eski) - m:
        b -= 1
    bas_metin = eski[:p.bas[a]]
    son_metin = eski[p.bas[b]:]
    orta_yeni = yeni[len(bas_metin):len(yeni) - len(son_metin)]
    tol = max(0.25, 0.02 * p.boyut)
    uyarilar = []

    def yazim(metin):
        if not metin:
            return None, 0.0
        y, uyari = _yazim_sec(belge, p, metin, d['font'])
        if uyari:
            uyarilar.append(uyari)
        return y, _tc_tahmini(p, y)

    def t_bas(i):
        """i. harfin başlangıcı; i == n ise son harfin bitişi."""
        return p.t(p.harfler[i].o) if i < n else p.bitis(n - 1)

    sigdirildi = False
    if d['sigdir'] and yeni:
        # Yeni metnin tamamı asıl genişliğe sığmıyorsa parçanın tamamı tek
        # oranla sıkıştırılarak yeniden yazılır (yarısı sıkışık yarısı değil olmasın).
        y, tc = yazim(yeni)
        w = y.genislik(p.boyut, tc)
        sol, sag = t_bas(0), p.bitis(n - 1)
        if w > sag - sol + 0.01:
            sigdirildi = True
            a, b, metin = 0, n, yeni
            sx = max(0.5, (sag - sol) / w)
            if sx * w > sag - sol + 0.01:
                uyarilar.append('asıl genişliğe sığdırmak için yarıdan fazla sıkıştırmak gerekirdi; '
                                'yarıya kadar sıkıştırıldı, metin taşıyor')
            t0 = {'sol': sol, 'sag': sag - w * sx, 'orta': (sol + sag) / 2 - w * sx / 2}[hiza]
        else:
            uyarilar.clear()

    if sigdirildi:
        pass
    elif hiza == 'orta':
        a, b, metin = 0, n, yeni
        y, tc = yazim(metin)
        sol, sag = t_bas(0), p.bitis(n - 1)
        w = y.genislik(p.boyut, tc) if y else 0
        sx = _sx(d, w, sag - sol)
        t0 = (sol + sag) / 2 - w * sx / 2
    else:
        # Önce yalnız ortadaki farkı yazmayı dene: genişlik aynıysa yanlar kımıldamaz.
        # Eski genişlik glif genişliklerinden; konumdan ölçmek sonraki harfe kadarki
        # aralığı (TJ kerning) da katar ve kuyruğu boş yere yeniden yazdırır.
        y, tc = yazim(orta_yeni)
        w = y.genislik(p.boyut, tc) if y else 0.0
        eski_orta = None
        if b < n:
            eski_orta = _dogal_genislik(belge, p, eski[p.bas[a]:p.bas[b]], tc)
            if eski_orta is None:
                eski_orta = t_bas(b) - t_bas(a)
        if eski_orta is not None and abs(w - eski_orta) <= tol:
            metin, sx, t0 = orta_yeni, 1.0, t_bas(a)
        elif hiza == 'sol':
            # Sol kenar yerinde: ortak baştan sonrası yeniden yazılır.
            uyarilar.clear()
            metin = yeni[len(bas_metin):]
            b = n
            y, tc = yazim(metin)
            w = y.genislik(p.boyut, tc) if y else 0
            sx = _sx(d, w, p.bitis(n - 1) - t_bas(a)) if a < n else 1.0
            t0 = t_bas(a)
        else:
            # Sağ kenar yerinde: ortak sondan öncesi yeniden yazılır.
            uyarilar.clear()
            metin = yeni[:len(yeni) - len(son_metin)]
            sag = t_bas(b)
            a = 0
            y, tc = yazim(metin)
            w = y.genislik(p.boyut, tc) if y else 0
            sx = _sx(d, w, sag - t_bas(0))
            t0 = sag - w * sx
    silinen = list(range(a, b))
    plan = _Plan(p, silinen, metin, p.nokta(t0), y, tc, sx, uyarilar)
    plan.t0, plan.t1 = t0, t0 + (y.genislik(p.boyut, tc) * sx if y else 0)
    return plan


def _dogal_genislik(belge, p, metin, tc):
    """Asıl yazı tipinin glif genişlikleriyle metnin genişliği; bilinmiyorsa None."""
    if not metin:
        return 0.0
    for xref in belge.adaylar(p.sayfa, p.font):
        fk = belge.kaynak(xref)
        if fk.gecerli and all(h in fk.ters for h in metin):
            return sum(fk.genislik(fk.ters[h][0], h) * p.boyut / 1000 + tc for h in metin)
    return None


def _sx(d, w, alan):
    if not d['sigdir'] or w <= 0 or alan <= 0 or w <= alan + 0.01:
        return 1.0
    return max(0.5, alan / w)


# ---------------------------------------------------------------- sayfaya uygulama

def _annots_al(doc, page):
    tur, deger = doc.xref_get_key(page.xref, 'Annots')
    if tur == 'xref':
        return doc.xref_object(_ref(deger), compressed=True)
    if tur == 'array':
        return deger
    return None


def _merkez(r, yari=0.2):
    c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    return fitz.Rect(c.x - yari, c.y - yari, c.x + yari, c.y + yari)


def _harf_dizini(parcalar, haric=frozenset()):
    """(harf, yuvarlak konum) → [konum]; boşluk ve sentetik harf hariç."""
    dizin = {}
    for p in parcalar:
        for i, h in enumerate(p.harfler):
            if (p.id, i) in haric or h.sentetik or h.c.isspace():
                continue
            dizin.setdefault((h.c, round(h.o.x), round(h.o.y)), []).append(h.o)
    return dizin


def _var_mi(dizin, h):
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for o in dizin.get((h.c, round(h.o.x) + dx, round(h.o.y) + dy), ()):
                if abs(o.x - h.o.x) <= 0.25 and abs(o.y - h.o.y) <= 0.25:
                    return True
    return False


def _sayfaya_uygula(belge, pno, parcalar, planlar, uyarilar, paragraf_planlari=()):
    doc = belge.doc
    page = doc[pno]
    no = pno + 1
    bul = {p.id: p for p in parcalar}
    silinen = {(pl.p.id, i) for pl in planlar for i in pl.silinen}
    for pp in paragraf_planlari:
        silinen |= set(pp.silinen)
    # Sahte kalın (aynı metin hafif kaydırılıp iki kez basılmış): silinen harfin
    # kopyası da silinir, yoksa silme ona da dokunur ve denetim haklı olarak durur.
    silinen_harfler = [bul[pid].harfler[i] for pid, i in silinen]
    for p in parcalar:
        for i, h in enumerate(p.harfler):
            if (p.id, i) in silinen or h.sentetik or h.c.isspace():
                continue
            if any(s.c == h.c and abs(s.o.x - h.o.x) < 1 and abs(s.o.y - h.o.y) < 1 for s in silinen_harfler):
                silinen.add((p.id, i))
    tutulan = [(p, i, h) for p in parcalar for i, h in enumerate(p.harfler)
               if (p.id, i) not in silinen and not h.sentetik and not h.c.isspace()]

    # Çakışma uyarısı: yeni metin, aynı taban çizgisinde yerinde kalan bir harfin üstüne biniyor mu?
    for pl in planlar:
        p = pl.p
        if not pl.metin or not p.yatay():
            continue
        x0, x1 = pl.nokta.x + 0.3, pl.nokta.x + (pl.t1 - pl.t0) - 0.3
        for q, _i, h in tutulan:
            if abs(h.o.y - pl.nokta.y) < 0.5 * p.boyut and h.k.x1 > x0 and h.k.x0 < x1:
                uyarilar.append(f'Sayfa {no}: “{_kisalt(pl.p.metin)}” yerine yazılan metin yanındaki '
                                f'“{_kisalt(q.metin, 25)}” metniyle çakışıyor.')
                break

    for pp in paragraf_planlari:
        pp.uzama_uyarisi(tutulan, no, uyarilar)

    annots = _annots_al(doc, page)
    dikdortgen = 0
    tutulan_kutular = [h.k for _q, _i, h in tutulan]
    for pid, i in sorted(silinen):
        h = bul[pid].harfler[i]
        if h.sentetik:
            continue
        r = _merkez(h.k)
        if h.c.isspace() and any(k.intersects(r) for k in tutulan_kutular):
            continue
        page.add_redact_annot(r)
        dikdortgen += 1
    if dikdortgen:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        if _annots_al(doc, page) != annots:
            doc.xref_set_key(page.xref, 'Annots', annots if annots is not None else 'null')
        page = doc.reload_page(page)

    if not page.is_wrapped:
        page.wrap_contents()
    for pl in planlar:
        if pl.metin and pl.yazim:
            pl.yazim.yaz(doc, page, pl.nokta, pl.p.yon, pl.p.boyut, pl.p.renk, pl.tc, pl.sx)
    for pp in paragraf_planlari:
        pp.yaz(doc, page)
    page = doc.reload_page(page)

    # Denetim: dokunulmayan her harf yerinde mi?
    sonra = _parcalar(page, pno)
    dizin = _harf_dizini(sonra)
    for p, _i, h in tutulan:
        if not _var_mi(dizin, h):
            raise PdfHatasi(f'Sayfa {no}: “{_kisalt(p.metin)}” metni değişiklikten etkilendi. '
                            'Diğer metinlere dokunmamak için PDF oluşturulmadı.', kod='denetim')
    # Yazılan metin okunabiliyor mu?
    sayfa_metni = ''.join(''.join(h.g for h in q.harfler) for q in sonra).replace(' ', '')
    for metin in [pl.metin for pl in planlar] + [pp.metin for pp in paragraf_planlari]:
        aranan = metin.replace(' ', '')
        if aranan and aranan not in sayfa_metni:
            uyarilar.append(f'Sayfa {no}: “{_kisalt(metin)}” yazıldı ama metin olarak '
                            'okunamıyor; önizlemede denetleyin.')


def uygula(veri, duzeltmeler, eklemeler=None, paragraflar=None, resimler=None):
    pars = _dogrula_paragraf(paragraflar)
    ekler = _dogrula_ekleme(eklemeler)
    res = _dogrula_resim(resimler)
    liste = _dogrula(duzeltmeler, bos_olabilir=bool(ekler or pars or res))
    doc = _ac(veri)
    # Sahip şifreli dosya şifresiz açılınca is_encrypted False dönüyor (ölçüldü);
    # izin bitleri yine doğru. Şifresiz dosyada bütün bitler açık (-1).
    if not (doc.permissions & fitz.PDF_PERM_MODIFY):
        raise PdfHatasi('Bu PDF\'in sahibi değiştirilmesine izin vermiyor.', kod='izin')
    sayfa_sayisi = doc.page_count
    belge = _Belge(doc)
    uyarilar = []

    sayfalara = {}
    for d in liste:
        sayfalara.setdefault(int(d['id'].split('-')[0]), []).append(d)
    par_sayfalara = {}
    for d in pars:
        par_sayfalara.setdefault(int(d['id'][1:].split('-')[0]), []).append(d)

    for pno in sorted(set(sayfalara) | set(par_sayfalara)):
        if pno >= sayfa_sayisi:
            raise PdfHatasi('Değişiklik listesi bu dosyaya ait değil. PDF\'i yeniden açın.', kod='eslesmedi')
        parcalar = _parcalar(doc[pno], pno)
        bul = {p.id: p for p in parcalar}
        par_planlari = []
        if par_sayfalara.get(pno):
            bul_p = {par.id: par for par in _paragraflar(parcalar)}
            satir_idleri = {d['id'] for d in sayfalara.get(pno, [])}
            for d in par_sayfalara[pno]:
                par = bul_p.get(d['id'])
                if par is None or par.metin != d['eski']:
                    raise PdfHatasi(f'Sayfa {pno + 1}: “{_kisalt(d["eski"])}” paragrafı dosyada bulunamadı. '
                                    'PDF\'i yeniden açıp tekrar deneyin.', kod='eslesmedi')
                if satir_idleri & {p.id for p in par.parcalar}:
                    raise PdfHatasi(f'Sayfa {pno + 1}: “{_kisalt(par.metin)}” hem satır hem paragraf olarak '
                                    'değiştirilmiş.', kod='liste')
                if d['yeni'] == par.metin and d['hiza'] in (None, par.hiza):
                    continue
                pp = _paragraf_planla(belge, par, d['yeni'], d['hiza'] or par.hiza)
                for u in pp.uyarilar:
                    uyarilar.append(f'Sayfa {pno + 1}: “{_kisalt(par.metin)}” paragrafı — {u}.')
                par_planlari.append(pp)
        planlar = []
        for d in sayfalara.get(pno, []):
            p = bul.get(d['id'])
            if p is None or p.metin != d['eski']:
                raise PdfHatasi(f'Sayfa {pno + 1}: “{_kisalt(d["eski"])}” metni dosyada bulunamadı. '
                                'PDF\'i yeniden açıp tekrar deneyin.', kod='eslesmedi')
            if p.neden:
                raise PdfHatasi(f'Sayfa {pno + 1}: “{_kisalt(p.metin)}” değiştirilemez ({p.neden}).')
            if d['yeni'] == p.metin:
                continue
            pl = _planla(belge, p, d)
            for u in pl.uyarilar:
                uyarilar.append(f'Sayfa {pno + 1}: “{_kisalt(p.metin)}” — {u}.')
            planlar.append(pl)
        if planlar or par_planlari:
            _sayfaya_uygula(belge, pno, parcalar, planlar, uyarilar, par_planlari)

    # Yeni metinler düzeltmelerden sonra (silinen yere yazılan yeni metin karışmasın).
    ek_sayfalara = {}
    for e in ekler:
        if e['sayfa'] > sayfa_sayisi:
            raise PdfHatasi('Eklenecek metin bu dosyada olmayan bir sayfada. PDF\'i yeniden açın.',
                            kod='eslesmedi')
        ek_sayfalara.setdefault(e['sayfa'] - 1, []).append(e)
    for pno in sorted(ek_sayfalara):
        _sayfaya_ekle(belge, pno, ek_sayfalara[pno], uyarilar)

    # Resimler en son: her şeyin üstünde dururlar (kullanıcı onları metnin üstüne koyabilir).
    res_sayfalara = {}
    for r in res:
        if r['sayfa'] > sayfa_sayisi:
            raise PdfHatasi('Eklenecek resim bu dosyada olmayan bir sayfada. PDF\'i yeniden açın.',
                            kod='eslesmedi')
        res_sayfalara.setdefault(r['sayfa'] - 1, []).append(r)
    for pno in sorted(res_sayfalara):
        _sayfaya_resim_koy(doc, pno, res_sayfalara[pno], uyarilar)

    if doc.page_count != sayfa_sayisi:
        raise PdfHatasi('Beklenmeyen bir hata oldu (sayfa sayısı değişti); PDF oluşturulmadı.')
    sonuc = doc.tobytes(garbage=1, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    return sonuc, uyarilar


# ---------------------------------------------------------------- yeni metin ekleme

class _Taslak:
    """Yazı tipi seçimine (_yazim_sec) giden, parçası olmayan bir metin."""

    def __init__(self, sayfa, font, bayrak):
        self.sayfa = sayfa
        self.font = font
        self.bayrak = bayrak
        self.harfler = []


def _sayi_mi(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _dogrula_ekleme(eklemeler):
    """İstemciden gelen yeni metinler. sayfa 1'den; x, y sayfa görüntüsünde oran
    (0–1, sol üst köşe başlangıç; y taban çizgisi)."""
    if eklemeler is None:
        return []
    hata = PdfHatasi('Eklenecek metin listesi okunamadı.', kod='liste')
    if not isinstance(eklemeler, list):
        raise hata
    if len(eklemeler) > AZAMI_EKLEME:
        raise PdfHatasi(f'Bir seferde en fazla {AZAMI_EKLEME} metin eklenebilir.', kod='liste')
    sonuc = []
    for e in eklemeler:
        if not isinstance(e, dict):
            raise hata
        sayfa, x, y, boyut = e.get('sayfa'), e.get('x'), e.get('y'), e.get('boyut')
        if not isinstance(sayfa, int) or isinstance(sayfa, bool) or sayfa < 1:
            raise hata
        if not (_sayi_mi(x) and _sayi_mi(y) and 0 <= x <= 1 and 0 <= y <= 1):
            raise hata
        if not (_sayi_mi(boyut) and 4 <= boyut <= 200):
            raise hata
        metin = e.get('metin')
        if not isinstance(metin, str):
            raise hata
        metin = metin.replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ')
        metin = ''.join(c for c in metin if c in (' ', '\n') or c.isprintable())
        if not metin.strip():
            raise PdfHatasi('Eklenecek metin boş.', kod='liste')
        if len(metin) > AZAMI_METIN or metin.count('\n') >= 50:
            raise PdfHatasi(f'Eklenecek metin en fazla {AZAMI_METIN} karakter ve 50 satır olabilir.', kod='liste')
        renk = e.get('renk')
        if not isinstance(renk, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', renk):
            raise hata
        font = e.get('font')
        if not isinstance(font, dict) or font.get('tur') not in ('belge', 'standart'):
            raise hata
        ad = font.get('ad')
        if font['tur'] == 'standart' and ad not in STANDART_FONTLAR:
            raise hata
        if not isinstance(ad, str) or not ad or len(ad) > 120:
            raise hata
        bayrak = font.get('bayrak', 0)
        if not isinstance(bayrak, int) or isinstance(bayrak, bool):
            bayrak = 0
        sonuc.append({'sayfa': sayfa, 'x': float(x), 'y': float(y), 'boyut': float(boyut), 'metin': metin,
                      'renk': int(renk[1:], 16), 'font': {'tur': font['tur'], 'ad': ad, 'bayrak': bayrak}})
    return sonuc


def _birim(v):
    n = math.hypot(v.x, v.y) or 1.0
    return fitz.Point(v.x / n, v.y / n)


def _sayfaya_ekle(belge, pno, eklemeler, uyarilar):
    """Yeni metinleri yazar; hiçbir şey silmez. Sonunda var olan bütün harflerin
    yerinde durduğu yine denetlenir."""
    doc = belge.doc
    no = pno + 1
    # Yazı tipi seçimi önce: mürekkep denetimi geçici sayfa açıp siler, sayfa
    # nesneleri geçersizleşir.
    planlar = []
    for e in eklemeler:
        satirlar = e['metin'].split('\n')
        yazimlar = []
        for satir in satirlar:
            if not satir.strip():
                yazimlar.append(None)
                continue
            if e['font']['tur'] == 'standart':
                font = fitz.Font(e['font']['ad'])
                yazim = _FontYazim('benzer', font).hazirla(satir)
                eksik = [h for h in dict.fromkeys(satir) if h != ' ' and not font.has_glyph(ord(h))]
                uyari = (f'{font.name} yazı tipinde {_harf_listesi(eksik)} yok, boş görünebilir'
                         if eksik else None)
            else:
                taslak = _Taslak(pno, e['font']['ad'], e['font']['bayrak'])
                yazim, uyari = _yazim_sec(belge, taslak, satir, 'otomatik')
            if uyari:
                uyarilar.append(f'Sayfa {no}: eklenen “{_kisalt(satir)}” — {uyari}.')
            yazimlar.append(yazim)
        planlar.append((e, satirlar, yazimlar))

    page = doc[pno]
    once = _parcalar(page, pno)
    korunan = [(p, h) for p in once for h in p.harfler if not h.sentetik and not h.c.isspace()]
    if not page.is_wrapped:
        page.wrap_contents()
    ters = page.derotation_matrix
    pr = page.rect
    yazilan = []
    for e, satirlar, yazimlar in planlar:
        # Ekrandaki (döndürülmüş) nokta ve yönler, içeriğin döndürülmemiş düzlemine.
        ekran = fitz.Point(pr.x0 + e['x'] * pr.width, pr.y0 + e['y'] * pr.height)
        bas = ekran * ters
        yon = _birim(fitz.Point(ekran.x + 1, ekran.y) * ters - bas)
        asagi = _birim(fitz.Point(ekran.x, ekran.y + 1) * ters - bas)
        cakisti = False
        for i, (satir, yazim) in enumerate(zip(satirlar, yazimlar)):
            if yazim is None:
                continue
            kay = i * 1.2 * e['boyut']
            nokta = fitz.Point(bas.x + asagi.x * kay, bas.y + asagi.y * kay)
            if not cakisti and abs(yon.y) < 1e-3 and yon.x > 0:
                w = yazim.genislik(e['boyut'], 0.0)
                alan = fitz.Rect(nokta.x + 0.3, nokta.y - 0.75 * e['boyut'],
                                 nokta.x + w - 0.3, nokta.y + 0.2 * e['boyut'])
                for p, h in korunan:
                    if h.k.intersects(alan):
                        uyarilar.append(f'Sayfa {no}: eklenen “{_kisalt(satir)}” mevcut '
                                        f'“{_kisalt(p.metin, 25)}” metninin üstüne biniyor.')
                        cakisti = True
                        break
            yazim.yaz(doc, page, nokta, (yon.x, yon.y), e['boyut'], e['renk'], 0.0, 1.0)
            yazilan.append(satir)
    page = doc.reload_page(page)

    sonra = _parcalar(page, pno)
    dizin = _harf_dizini(sonra)
    for p, h in korunan:
        if not _var_mi(dizin, h):
            raise PdfHatasi(f'Sayfa {no}: “{_kisalt(p.metin)}” metni eklemeden etkilendi. '
                            'Diğer metinlere dokunmamak için PDF oluşturulmadı.', kod='denetim')
    sayfa_metni = ''.join(''.join(h.g for h in q.harfler) for q in sonra).replace(' ', '')
    for satir in yazilan:
        if satir.replace(' ', '') not in sayfa_metni:
            uyarilar.append(f'Sayfa {no}: eklenen “{_kisalt(satir)}” yazıldı ama metin olarak '
                            'okunamıyor; önizlemede denetleyin.')


# ---------------------------------------------------------------- resim ekleme (1.17)
# Döndürme, beyaz zemini kaldırma ve saydamlık tarayıcıda resmin kendisine işlenir;
# buraya son hâli gelir ve verilen kutuya tam oturtulur (oran tarayıcıda korunur ya da
# kullanıcı bilerek değiştirir). Ölçüm (26.11.2026, docs/motor.md): döndürülmüş sayfada
# kutu derotation_matrix ile içerik düzlemine, `rotate=page.rotation` ile resim ekranda dik.

_RESIM_IMZALARI = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')


def _dogrula_resim(resimler):
    """[{sayfa, x, y, w, h, veri: bytes}] — x, y, w, h sayfa görüntüsünde oran (sol üst köşe)."""
    if resimler is None:
        return []
    hata = PdfHatasi('Eklenecek resim listesi okunamadı.', kod='liste')
    if not isinstance(resimler, list):
        raise hata
    if len(resimler) > AZAMI_RESIM:
        raise PdfHatasi(f'Bir seferde en fazla {AZAMI_RESIM} resim eklenebilir.', kod='liste')
    sonuc = []
    for r in resimler:
        if not isinstance(r, dict):
            raise hata
        sayfa = r.get('sayfa')
        if not isinstance(sayfa, int) or isinstance(sayfa, bool) or sayfa < 1:
            raise hata
        x, y, w, h = (r.get(k) for k in ('x', 'y', 'w', 'h'))
        if not all(_sayi_mi(v) for v in (x, y, w, h)):
            raise hata
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0.002 <= w <= 1 and 0.002 <= h <= 1
                and x + w <= 1.0001 and y + h <= 1.0001):
            raise hata
        veri = r.get('veri')
        if not isinstance(veri, (bytes, bytearray)) or not veri.startswith(_RESIM_IMZALARI):
            raise PdfHatasi('Eklenecek resim PNG ya da JPEG olmalı.', kod='resim')
        if len(veri) > AZAMI_RESIM_BAYT:
            raise PdfHatasi('Eklenecek resim çok büyük.', kod='resim')
        try:
            pix = fitz.Pixmap(bytes(veri))
        except Exception:
            raise PdfHatasi('Eklenecek resim okunamadı.', kod='resim')
        if max(pix.width, pix.height) > AZAMI_RESIM_PIKSEL or min(pix.width, pix.height) < 1:
            raise PdfHatasi('Eklenecek resmin boyutu desteklenmiyor.', kod='resim')
        sonuc.append({'sayfa': sayfa, 'x': x, 'y': y, 'w': w, 'h': h, 'veri': bytes(veri)})
    return sonuc


def _sayfaya_resim_koy(doc, pno, resimler, uyarilar):
    no = pno + 1
    page = doc[pno]
    once = _parcalar(page, pno)
    korunan = [(p, h) for p in once for h in p.harfler if not h.sentetik and not h.c.isspace()]
    if not page.is_wrapped:
        page.wrap_contents()
    ters = page.derotation_matrix
    pr = page.rect
    for r in resimler:
        ekran = fitz.Rect(pr.x0 + r['x'] * pr.width, pr.y0 + r['y'] * pr.height,
                          pr.x0 + (r['x'] + r['w']) * pr.width, pr.y0 + (r['y'] + r['h']) * pr.height)
        kutu = ekran * ters
        kutu.normalize()
        for p, h in korunan:
            if h.k.intersects(kutu):
                uyarilar.append(f'Sayfa {no}: eklenen resim “{_kisalt(p.metin, 25)}” metninin üstüne biniyor.')
                break
        page.insert_image(kutu, stream=r['veri'], keep_proportion=False, rotate=page.rotation)
    page = doc.reload_page(page)
    dizin = _harf_dizini(_parcalar(page, pno))
    for p, h in korunan:
        if not _var_mi(dizin, h):
            raise PdfHatasi(f'Sayfa {no}: “{_kisalt(p.metin)}” metni resim eklemeden etkilendi. '
                            'Diğer metinlere dokunmamak için PDF oluşturulmadı.', kod='denetim')


# ---------------------------------------------------------------- paragraf düzenleme
# Paragraf: aynı bloktaki (ortalı paragrafta ardışık bloklardaki) en az iki
# yatay satır. Yeni metin sözcük sözcük, paragrafın genişliğine yeniden dizilir;
# sol kenar, ilk satır girintisi, satır aralığı ve hizalama asıldan ölçülür.
# Değişmeyen sözcükler biçimini (kalın / eğik / renk) korur (kullanıcı kararı).


class _Kelime:
    __slots__ = ('metin', 'stil', 'harfler')

    def __init__(self, metin, stil, harfler):
        self.metin = metin
        self.stil = stil          # _Parca (yazı tipi, boyut, renk, bayrak kaynağı)
        self.harfler = harfler    # [(parça id, harf indeksi)]


class _Paragraf:
    def __init__(self, pid, satirlar):
        self.id = pid
        self.satirlar = satirlar                          # [[_Parca, ...], ...]
        self.parcalar = [p for s in satirlar for p in s]
        self.sayfa = self.parcalar[0].sayfa
        self.kelimeler = []
        self.harfler = []                                 # silinecek bütün (id, i)
        satir_olcu = []                                   # (x0, x1, taban, [(k0, k1)])
        gecerli = None
        for satir in satirlar:
            kutular = []
            ilk_x0 = son_x1 = taban = None
            for p in satir:
                for i, h in enumerate(p.harfler):
                    self.harfler.append((p.id, i))
                    if h.c.isspace() or h.sentetik:
                        if gecerli is not None and gecerli.metin:
                            gecerli = None
                        continue
                    # Boşluk karakteri yerine konumla açılmış aralık da sözcük sonudur (yalnız
                    # boşluktan oluşan parçalar parça listesine girmiyor; ölçüldü).
                    if gecerli is not None and son_x1 is not None and h.k.x0 - son_x1 > 0.2 * p.boyut:
                        gecerli = None
                    if ilk_x0 is None:
                        ilk_x0, taban = h.k.x0, h.o.y
                    son_x1 = h.k.x1
                    if gecerli is None:
                        gecerli = _Kelime('', p, [])
                        self.kelimeler.append(gecerli)
                        kutular.append([h.k.x0, h.k.x1])
                    gecerli.metin += h.g
                    gecerli.harfler.append((p.id, i))
                    kutular[-1][1] = h.k.x1
            # Satır sonunda yumuşak tire: sözcük sonraki satırda sürer.
            if gecerli is not None and gecerli.metin.endswith('-') and self._yumusak_tire(gecerli):
                gecerli.metin = gecerli.metin[:-1]
            else:
                gecerli = None
            satir_olcu.append((ilk_x0, son_x1, taban, kutular))
        self.metin = ' '.join(k.metin for k in self.kelimeler)
        self._olc(satir_olcu)

    def _yumusak_tire(self, kelime):
        pid, i = kelime.harfler[-1]
        p = next(q for q in self.parcalar if q.id == pid)
        return p.harfler[i].c == '­'

    def _olc(self, olcu):
        x0 = [o[0] for o in olcu]
        x1 = [o[1] for o in olcu]
        taban = [o[2] for o in olcu]
        self.tabanlar = taban
        self.taban0 = taban[0]
        self.aralik = statistics.median([b - a for a, b in zip(taban, taban[1:])])
        self.sol = min(x0[1:]) if len(x0) > 1 else x0[0]
        self.sag = max(x1)
        self.girinti = x0[0] - self.sol if x0[0] - self.sol > 1 else 0.0
        say = {}
        for k in self.kelimeler:
            anahtar = (k.stil.font, round(k.stil.boyut, 1), k.stil.renk)
            say[anahtar] = say.get(anahtar, 0) + len(k.metin)
        baskin = max(say, key=say.get)
        self.baskin = next(k.stil for k in self.kelimeler
                           if (k.stil.font, round(k.stil.boyut, 1), k.stil.renk) == baskin)
        self.boyut = self.baskin.boyut
        # Doğal sözcük aralığı: iki yana yaslanmamış satırlardaki en küçük aralıkların ortancası.
        araliklar = []
        for o in olcu:
            k = o[3]
            araliklar += [b[0] - a[1] for a, b in zip(k, k[1:])]
        dogal = min(araliklar) if araliklar else 0.278 * self.boyut
        self.dogal_bosluk = max(dogal, 0.15 * self.boyut)
        merkezler = [(a + b) / 2 for a, b in zip(x0, x1)]
        tam = list(range(len(olcu) - 1))   # son satır hariç
        genis = any(self._satir_araligi(olcu[i]) > self.dogal_bosluk * 1.15 for i in tam)
        if all(abs(x1[i] - self.sag) < 1 for i in tam) and genis:
            self.hiza = 'iki'
        elif max(x0) - min(x0) > 2 and max(merkezler) - min(merkezler) < 1.5:
            self.hiza = 'orta'
        elif max(x0) - min(x0) > 2 and max(x1) - min(x1) < 1:
            self.hiza = 'sag'
        else:
            self.hiza = 'sol'
        self.merkez = statistics.median(merkezler)
        alt = max(o[2] for o in olcu) + 0.3 * self.boyut
        ust = self.taban0 - 0.9 * self.boyut
        self.kutu = fitz.Rect(min(x0), ust, self.sag, alt)

    @staticmethod
    def _satir_araligi(o):
        k = o[3]
        if len(k) < 2:
            return 0.0
        return statistics.mean(b[0] - a[1] for a, b in zip(k, k[1:]))


def _bloklar(parcalar):
    """{blok no: [[satır parçaları], ...]} (sırayla)."""
    bloklar = {}
    for p in parcalar:
        _pno, bno, lno, _sno = (int(x) for x in p.id.split('-'))
        bloklar.setdefault(bno, {}).setdefault(lno, []).append(p)
    return [(bno, [sorted(sat, key=lambda q: q.harfler[0].o.x) for _l, sat in sorted(b.items())])
            for bno, b in sorted(bloklar.items())]


def _paragraflar(parcalar):
    """Sayfadaki düzenlenebilir paragraflar. Ortalı paragrafta MuPDF satırları ayrı
    bloklara bölebiliyor (ölçüldü): ortaları aynı, satır aralığında ardışık bloklar birleştirilir."""
    adaylar = []
    for bno, satirlar in _bloklar(parcalar):
        if not all(p.yatay() and not p.neden for s in satirlar for p in s):
            continue
        satirlar = [s for s in satirlar if ''.join(p.metin for p in s).strip()]
        if satirlar:
            adaylar.append((bno, satirlar))
    def kenarlar(s):
        harf = [h for p in s for h in p.harfler if not h.c.isspace() and not h.sentetik]
        if not harf:
            return None
        return min(h.k.x0 for h in harf), max(h.k.x1 for h in harf), harf[0].o.y

    birlesik = []
    for bno, satirlar in adaylar:
        if birlesik:
            onceki_bno, onceki = birlesik[-1]
            a, b = onceki[-1], satirlar[0]
            ka, kb = kenarlar(a), kenarlar(b)
            boyut = a[0].boyut
            if ka and kb and a[0].font == b[0].font and abs(b[0].boyut - boyut) < 0.1:
                fark = kb[2] - ka[2]
                tabanlar = [kenarlar(x)[2] for x in onceki if kenarlar(x)]
                adim = statistics.median([y - x for x, y in zip(tabanlar, tabanlar[1:])]) if len(tabanlar) > 1 else None
                aralikta = 0.9 * boyut < fark < 1.8 * boyut and (adim is None or abs(fark - adim) < 0.2 * adim)
                # Sol, orta ya da sağ kenar hizalı. MuPDF ortalı satırları ve yazısı küçültülmüş
                # (aralığı görece açılmış) paragrafı ayrı bloklara bölebiliyor (ölçüldü).
                sol_ref = kenarlar(onceki[1])[0] if len(onceki) > 1 and kenarlar(onceki[1]) else ka[0]
                hizali = (abs(kb[0] - sol_ref) < 1 or abs((ka[0] + ka[1]) / 2 - (kb[0] + kb[1]) / 2) < 1.5
                          or abs(ka[1] - kb[1]) < 1)
                if aralikta and hizali:
                    birlesik[-1] = (onceki_bno, onceki + satirlar)
                    continue
        birlesik.append((bno, satirlar))
    sonuc = []
    for bno, satirlar in birlesik:
        if len(satirlar) < 2:
            continue
        pno = satirlar[0][0].sayfa
        try:
            par = _Paragraf(f'P{pno}-{bno}', satirlar)
        except (ValueError, statistics.StatisticsError):
            continue
        if par.kelimeler and par.aralik > 0:
            sonuc.append(par)
    return sonuc


def _dogrula_paragraf(paragraflar):
    if paragraflar is None:
        return []
    hata = PdfHatasi('Paragraf listesi okunamadı.', kod='liste')
    if not isinstance(paragraflar, list):
        raise hata
    if len(paragraflar) > AZAMI_PARAGRAF:
        raise PdfHatasi(f'Bir seferde en fazla {AZAMI_PARAGRAF} paragraf değiştirilebilir.', kod='liste')
    sonuc, gorulen = [], set()
    for d in paragraflar:
        if not isinstance(d, dict):
            raise hata
        pid = d.get('id')
        if not isinstance(pid, str) or not re.fullmatch(r'P\d{1,4}-\d{1,6}', pid) or pid in gorulen:
            raise hata
        gorulen.add(pid)
        eski, yeni, hiza = d.get('eski'), d.get('yeni'), d.get('hiza')
        if not isinstance(eski, str) or not isinstance(yeni, str):
            raise hata
        yeni = ' '.join(''.join(c if c.isprintable() else ' ' for c in yeni).split())
        if len(yeni) > 10 * AZAMI_METIN:
            raise PdfHatasi(f'Paragraf en fazla {10 * AZAMI_METIN} karakter olabilir.', kod='liste')
        if hiza not in (None, 'sol', 'sag', 'orta', 'iki'):
            raise hata
        sonuc.append({'id': pid, 'eski': eski, 'yeni': yeni, 'hiza': hiza})
    return sonuc


def _diz(genislikler, bosluk, genislik, girinti):
    satirlar, simdiki, dolu = [], [], 0.0
    sinir = genislik - girinti
    for i, w in enumerate(genislikler):
        ek = w if not simdiki else bosluk + w
        if simdiki and dolu + ek > sinir + 0.01:
            satirlar.append(simdiki)
            simdiki, dolu, sinir = [i], w, genislik
        else:
            simdiki.append(i)
            dolu += ek
    if simdiki:
        satirlar.append(simdiki)
    return satirlar


class _ParagrafPlani:
    def __init__(self, par, kelimeler, yazimlar, genislikler, satirlar, oran, hiza):
        self.par = par
        self.silinen = list(par.harfler)
        self.kelimeler = kelimeler      # [(metin, stil)]
        self.yazimlar = yazimlar
        self.genislikler = genislikler  # asıl boyutta
        self.satirlar = satirlar        # [[kelime indeksi]]
        self.oran = oran
        self.hiza = hiza
        self.metin = ' '.join(m for m, _ in kelimeler)
        self.uyarilar = []

    def taban(self, n):
        """n. satırın taban çizgisi: asıl satırlarda asıl yer (Chrome satırları piksele
        yuvarlıyor, aralıklar eşit değil; ölçüldü), uzayan satırlarda ortanca aralık."""
        t = self.par.tabanlar
        return t[n] if n < len(t) else t[-1] + (n - len(t) + 1) * self.par.aralik

    def uzama_uyarisi(self, tutulan, no, uyarilar):
        fazla = len(self.satirlar) - len(self.par.satirlar)
        if fazla <= 0:
            return
        eski_son = self.par.tabanlar[-1]
        yeni_son = self.taban(len(self.satirlar) - 1)
        for q, _i, h in tutulan:
            if (eski_son + 0.3 * self.par.aralik < h.o.y <= yeni_son + 0.3 * self.par.aralik
                    and h.k.x1 > self.par.sol and h.k.x0 < self.par.sag):
                uyarilar.append(f'Sayfa {no}: “{_kisalt(self.par.metin)}” paragrafı {fazla} satır uzadı ve '
                                f'alttaki “{_kisalt(q.metin, 25)}” metnine biniyor.')
                return

    def yaz(self, doc, page):
        p, o = self.par, self.oran
        bosluk = p.dogal_bosluk * o
        for n, satir in enumerate(self.satirlar):
            taban = self.taban(n)
            girinti = p.girinti if (n == 0 and self.hiza in ('sol', 'iki')) else 0.0
            genislik = p.sag - p.sol - girinti
            w = [self.genislikler[i] * o for i in satir]
            dolu = sum(w) + bosluk * (len(satir) - 1)
            son = n == len(self.satirlar) - 1
            if self.hiza == 'iki' and not son and len(satir) > 1:
                ara, x = (genislik - sum(w)) / (len(satir) - 1), p.sol + girinti
            elif self.hiza == 'sag':
                ara, x = bosluk, p.sag - dolu
            elif self.hiza == 'orta':
                ara, x = bosluk, p.merkez - dolu / 2
            else:
                ara, x = bosluk, p.sol + girinti
            for j, i in enumerate(satir):
                metin, stil = self.kelimeler[i]
                self.yazimlar[i].yaz(doc, page, fitz.Point(x, taban), (1.0, 0.0), stil.boyut * o, stil.renk, 0.0, 1.0)
                x += w[j] + ara


def _paragraf_planla(belge, par, yeni, hiza):
    yeni_k = yeni.split()
    eski_k = [k.metin for k in par.kelimeler]
    stiller = [None] * len(yeni_k)
    sm = difflib.SequenceMatcher(a=eski_k, b=yeni_k, autojunk=False)
    for etiket, i1, i2, j1, j2 in sm.get_opcodes():
        if etiket == 'equal':
            for k in range(j2 - j1):
                stiller[j1 + k] = par.kelimeler[i1 + k].stil
        elif etiket == 'replace':
            # Değiştirilen sözcük yerine geçtiğinin biçimini alır ("otuz" → "kırk" kalın kalır).
            for k in range(j2 - j1):
                stiller[j1 + k] = par.kelimeler[min(i1 + k, i2 - 1)].stil
    for j in range(len(stiller)):
        if stiller[j] is None:   # araya eklenen: solundakinin biçimi (başta: sağındakinin / baskın)
            stiller[j] = stiller[j - 1] if j > 0 else next((s for s in stiller if s is not None), par.baskin)
    kelimeler = list(zip(yeni_k, stiller))

    yazimlar, genislikler, uyarilar_kume = [], [], []
    onbellek = {}
    for metin, stil in kelimeler:
        anahtar = (id(stil), metin)
        if anahtar not in onbellek:
            y, uyari = _yazim_sec(belge, stil, metin, 'otomatik')
            onbellek[anahtar] = (y, _tc_tahmini(stil, y))
            if uyari and uyari not in uyarilar_kume:
                uyarilar_kume.append(uyari)
        y, _tc = onbellek[anahtar]
        yazimlar.append(y)
        genislikler.append(y.genislik(stil.boyut, 0.0))

    genislik = par.sag - par.sol
    hedef = len(par.satirlar)
    oran, satirlar = 1.0, _diz(genislikler, par.dogal_bosluk, genislik, par.girinti)
    if len(satirlar) > hedef:
        for o in PARAGRAF_KUCULTME[1:]:
            s = _diz([w * o for w in genislikler], par.dogal_bosluk * o, genislik, par.girinti)
            if len(s) <= hedef:
                oran, satirlar = o, s
                break
        else:
            # %15 küçültme de yetmedi: asıl boyutta aşağı uzar (küçük ve uzun olmasın).
            oran, satirlar = 1.0, _diz(genislikler, par.dogal_bosluk, genislik, par.girinti)
    plan = _ParagrafPlani(par, kelimeler, yazimlar, genislikler, satirlar, oran, hiza)
    plan.uyarilar = list(uyarilar_kume)
    if oran < 1:
        plan.uyarilar.append(f'yerine sığması için yazı %{round((1 - oran) * 100)} küçültüldü')
    if len(satirlar) > hedef:
        plan.uyarilar.append(f'%15 küçültme de yetmedi; paragraf {len(satirlar) - hedef} satır aşağı uzadı')
    if any(genislikler[i] * oran > genislik + 0.5 for s in satirlar for i in s if len(s) == 1):
        plan.uyarilar.append('bir sözcük satır genişliğinden uzun; sağa taşıyor')
    return plan
