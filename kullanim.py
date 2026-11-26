"""Kullanım ölçümü: kim, ne zaman, kaç PDF açtı / oluşturdu / indirdi.

KAYDEDİLMEZ: dosya adı, dosya içeriği, sayfa görüntüsü, metin parçası, hata
metni (belgeden alıntı içerebilir). Kaydedilen yalnız sayılar, hata kodu ve
dosyanın parmak izi: SHA-256'nın ilk 16 hanesi — geri çözülemez; yalnız "kaç
farklı dosya" sayısı için.

Kayıt kullanıcının işini hiçbir zaman bozmaz: yazılamazsa log'a uyarı düşer.
Veritabanına ulaşılamadıysa 60 sn yeniden denenmez (her PDF işlemi bağlantı
zaman aşımını beklemesin).
"""
import hashlib
import logging
import time

import kullanicilar as vt

log = logging.getLogger(__name__)

TURLER = {
    'giris': 'Giriş yaptı',
    'kayit': 'Kaydoldu',
    'acildi': 'PDF açtı',
    'onizlendi': 'Önizledi',
    'uretildi': 'PDF oluşturdu',
    'indirildi': 'İndirdi',
    'paylasildi': 'Paylaştı',
    'buyuk_dosya': 'Boyut sınırına takıldı',
    'liste_kaydedildi': 'Değişiklik listesini kaydetti',
    'liste_yuklendi': 'Değişiklik listesi yükledi',
    'toplu_degistirildi': 'Toplu değiştirdi',
}
# Tarayıcının bildirdiği olaylar (indirme, paylaşma, liste sunucuya uğramaz).
ISTEMCI_TURLERI = ('indirildi', 'paylasildi', 'buyuk_dosya', 'liste_kaydedildi', 'liste_yuklendi',
                   'toplu_degistirildi')
HATALAR = {
    'boyut': 'dosya çok büyük',
    'yanit': 'sonuç çok büyük',
    'sifreli': 'şifreli PDF',
    'sayfa_siniri': 'sayfa sınırı',
    'gorunum': 'görüntü sınırı',
    'izin': 'değiştirme izni yok',
    'eslesmedi': 'metin dosyada bulunamadı',
    'resim': 'resim okunamadı',
    'denetim': 'güvenlik denetimi durdurdu',
    'kota': 'aylık belge hakkı doldu',
    'kota_okunamadi': 'belge hakkı denetlenemedi',
    'liste': 'değişiklik listesi',
    'pdf': 'PDF işlenemedi',
    'beklenmeyen': 'beklenmeyen hata',
}
DONEMLER = {7: 'Son 7 gün', 30: 'Son 30 gün', 90: 'Son 90 gün', 0: 'Tümü'}
OLAY_SINIRI = 100
_BEKLEME_SN = 60
_son_hata = 0.0


def parmak_izi(veri: bytes) -> str:
    return hashlib.sha256(veri).hexdigest()[:16]


def _tamsayi(deger, ust):
    return deger if isinstance(deger, int) and not isinstance(deger, bool) and 0 <= deger <= ust else None


def kaydet(kullanici, tur, *, basarili=True, hata=None, sayfa=None, boyut=None, duzeltme=None,
           ekleme=None, uyari=None, sure_ms=None, belge=None, resim=None):
    """Olayı yazar; yazıldıysa True. Hiçbir durumda istisna atmaz."""
    global _son_hata
    if not vt.tanimli() or not kullanici or tur not in TURLER:
        return False
    if time.monotonic() - _son_hata < _BEKLEME_SN:
        return False
    if hata is not None and hata not in HATALAR:
        hata = 'pdf'
    try:
        vt.yazma(
            'INSERT INTO kullanim_olayi (kullanici, tur, basarili, hata, sayfa, boyut, duzeltme, ekleme, '
            'uyari, sure_ms, belge, resim) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (kullanici, tur, bool(basarili), hata, _tamsayi(sayfa, 100_000), _tamsayi(boyut, 2_000_000_000),
             _tamsayi(duzeltme, 100_000), _tamsayi(ekleme, 100_000), _tamsayi(uyari, 100_000),
             _tamsayi(sure_ms, 86_400_000), belge, _tamsayi(resim, 100_000)))
        return True
    except Exception:
        _son_hata = time.monotonic()
        log.warning('kullanım kaydı yazılamadı', exc_info=True)
        return False


def _donem(gun):
    if gun:
        return 'WHERE zaman > now() - make_interval(days => %s)', (gun,)
    return '', ()


def ozet(gun):
    """Kullanıcı başına sayılar (en son kullanan önce)."""
    kosul, params = _donem(gun)
    return vt.sorgu(f"""
        SELECT kullanici,
          count(*) FILTER (WHERE tur = 'giris') AS giris,
          count(*) FILTER (WHERE tur = 'acildi' AND basarili) AS acilan,
          count(DISTINCT belge) FILTER (WHERE tur = 'acildi' AND basarili) AS farkli,
          coalesce(sum(sayfa) FILTER (WHERE tur = 'acildi' AND basarili), 0) AS sayfa,
          count(*) FILTER (WHERE tur = 'uretildi' AND basarili) AS uretilen,
          coalesce(sum(duzeltme) FILTER (WHERE tur = 'uretildi' AND basarili), 0) AS degisen,
          coalesce(sum(ekleme) FILTER (WHERE tur = 'uretildi' AND basarili), 0) AS eklenen,
          count(*) FILTER (WHERE tur IN ('indirildi', 'paylasildi')) AS indirilen,
          count(*) FILTER (WHERE NOT basarili OR tur = 'buyuk_dosya') AS sorun,
          max(zaman) AS son
        FROM kullanim_olayi {kosul}
        GROUP BY kullanici
        ORDER BY max(zaman) DESC""", params)


def toplam(satirlar):
    alanlar = ('giris', 'acilan', 'sayfa', 'uretilen', 'degisen', 'eklenen', 'indirilen', 'sorun')
    return {a: sum(int(r[a] or 0) for r in satirlar) for a in alanlar}


def farkli_belge(gun):
    """Bütün kullanıcılarda farklı dosya sayısı (aynı dosyayı iki kişi açtıysa bir)."""
    kosul, params = _donem(gun)
    ek = 'AND' if kosul else 'WHERE'
    satir = vt.sorgu(f"SELECT count(DISTINCT belge) AS n FROM kullanim_olayi {kosul} "
                     f"{ek} tur = 'acildi' AND basarili", params)
    return int(satir[0]['n']) if satir else 0


def olaylar(gun, sinir=OLAY_SINIRI):
    kosul, params = _donem(gun)
    return vt.sorgu(f'SELECT zaman, kullanici, tur, basarili, hata, sayfa, boyut, duzeltme, ekleme, uyari, sure_ms '
                    f'FROM kullanim_olayi {kosul} ORDER BY id DESC LIMIT %s', (*params, sinir))
