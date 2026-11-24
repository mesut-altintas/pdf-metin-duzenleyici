"""Aylık belge kotası (hesap başına).

Sayılan: ay içinde FARKLI belge, yani `/api/uret`'e gelen PDF'in parmak izi
(`kullanim.parmak_izi`). Aynı belge o ay yeniden üretilirse sayılmaz (kullanıcı
kararı, 22.11.2026). Önizleme de üretimdir: tam PDF tarayıcıya iner ve
önizlemeden indirilebilir.

Akış (`pdf_duzenle/rotalar.py`):
  1. Üretimden ÖNCE `durum()`: kota dolu ve belge bu ay sayılmamışsa iş hiç
     yapılmaz (403, kod 'kota'). Okunamazsa 503: denetlenemeyen kota ne "dolu"
     ne "boş" sayılır.
  2. Üretim BAŞARILIYSA `say()`: tek ifadede önce belge "sayıldı" diye
     işaretlenir, sonra sayaç koşullu artırılır. Aynı anda gelen istekler sınırı
     aşamaz: sayaç satırı kilitlenir, koşul kilitten sonra yeniden denetlenir.
     Sınır o arada dolduysa işaret geri alınır, dosya gönderilmez.
  3. Sayma yazılamazsa (bağlantı) dosya yine gönderilir ve log'a düşer:
     kullanıcı yaptığı işi kaybetmez; yazma yeniden gönderilmez.
Başarısız üretim sayılmaz (sayma üretimden sonra).

Dönem İstanbul saatiyle ay (Türkiye'de yaz saati yok: UTC+3 sabit).
Kotasız: ortam değişkenindeki yöneticiler, sınırsız planlar, `KOTA_KAPALI=1`,
veritabanı tanımsızken (yerel).
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import kullanicilar as vt
import planlar

log = logging.getLogger(__name__)

ISTANBUL = timezone(timedelta(hours=3))
# Bulunma ekiyle (ünlü uyumu ve sertleşme aya göre değişir).
AYLARDA = ("Ocak'ta", "Şubat'ta", "Mart'ta", "Nisan'da", "Mayıs'ta", "Haziran'da", "Temmuz'da",
           "Ağustos'ta", "Eylül'de", "Ekim'de", "Kasım'da", "Aralık'ta")

_DURUM_SQL = """
    SELECT h.id AS hesap_id, h.plan,
      COALESCE((SELECT s.belge FROM kota_sayaci s
                WHERE s.hesap_id = h.id AND s.donem = %(d)s), 0) AS kullanilan,
      EXISTS (SELECT 1 FROM kota_belge b
              WHERE b.hesap_id = h.id AND b.donem = %(d)s AND b.belge = %(b)s) AS sayildi
    FROM kullanicilar k JOIN hesap h ON h.id = k.hesap_id
    WHERE k.ad = %(ad)s"""

# yeni = 0: belge bu ay zaten sayılmış (ya da aynı anda gelen bir istek saydı).
# yeni = 1 ve kullanilan dolu: sayıldı. yeni = 1 ve kullanilan boş: sınır dolu.
_SAY_SQL = """
    WITH b AS (
        INSERT INTO kota_belge (hesap_id, donem, belge) VALUES (%(h)s, %(d)s, %(b)s)
        ON CONFLICT DO NOTHING RETURNING 1
    ), s AS (
        INSERT INTO kota_sayaci AS ks (hesap_id, donem, belge)
        SELECT %(h)s, %(d)s, 1 FROM b
        ON CONFLICT (hesap_id, donem) DO UPDATE SET belge = ks.belge + 1
        WHERE ks.belge < %(s)s
        RETURNING ks.belge
    )
    SELECT (SELECT count(*) FROM b) AS yeni, (SELECT belge FROM s) AS kullanilan"""

# Sınıra takılan isteğin işareti: belge sayılmadı. (Yazılamazsa o belge o ay
# sayılmış görünür, kullanıcı lehine; sayaç doğru kalır.)
_GERI_AL_SQL = ('DELETE FROM kota_belge WHERE hesap_id = %(h)s AND donem = %(d)s AND belge = %(b)s '
                'RETURNING 1')


@dataclass
class Durum:
    hesap_id: int
    plan: planlar.Plan
    kullanilan: int
    sayildi: bool                # bu belge bu ay zaten sayıldı mı (belge verilmediyse False)
    donem: str

    @property
    def sinir(self):
        return self.plan.aylik_belge

    @property
    def izin(self):
        """Bu belge üretilebilir mi?"""
        return self.sayildi or self.kullanilan < self.sinir


def kapali():
    return os.environ.get('KOTA_KAPALI') == '1'


def donem(simdi=None):
    simdi = (simdi or datetime.now(timezone.utc)).astimezone(ISTANBUL)
    return f'{simdi.year:04d}-{simdi.month:02d}'


def yenilenme_metni(donem_):
    """'2026-11' → "1 Aralık'ta" (sonraki ayın ilk günü)."""
    ay = int(donem_.split('-')[1])
    return f'1 {AYLARDA[ay % 12]}'


def durum(kullanici, belge='', simdi=None):
    """Kullanıcının bu ayki kota durumu; kotasızsa None. Okunamazsa DepoHatasi."""
    if kullanici is None or kullanici.kaynak != 'vt' or kapali() or not vt.tanimli():
        return None
    d = donem(simdi)
    satirlar = vt.sorgu(_DURUM_SQL, {'d': d, 'b': belge or '', 'ad': kullanici.ad})
    if not satirlar:
        # Hesapsız kullanıcı: geçişten sonra eski sürümün açtığı kayıt. Geçiş yeniden koşar.
        vt.hesap_gecisi()
        satirlar = vt.sorgu(_DURUM_SQL, {'d': d, 'b': belge or '', 'ad': kullanici.ad})
        if not satirlar:
            raise vt.DepoHatasi('Hesap bilgisi okunamadı. Biraz sonra yeniden deneyin.')
    r = satirlar[0]
    plan = planlar.bul(r['plan'])
    if plan.aylik_belge is None:
        return None
    return Durum(r['hesap_id'], plan, int(r['kullanilan']), bool(r['sayildi']), d)


def say(d, belge, calistir=None):
    """Üretilen belgeyi sayar. Dönen: güncel kullanılan sayısı; belge zaten
    sayılmışsa `d.kullanilan`; sınır bu arada dolduysa None. Yazılamazsa
    DepoHatasi / SonucBilinmiyor (çağıran dosyayı yine gönderir).
    `calistir`: testte ayrı bağlantıyla eşzamanlılık sınamak için."""
    calistir = calistir or vt.yazma_getir
    p = {'h': d.hesap_id, 'd': d.donem, 'b': belge, 's': d.sinir}
    r = calistir(_SAY_SQL, p)
    if not r['yeni']:
        return d.kullanilan
    if r['kullanilan'] is not None:
        return int(r['kullanilan'])
    try:
        calistir(_GERI_AL_SQL, p)
    except vt.DepoHatasi:
        log.error('kota işareti geri alınamadı; belge bu ay sayılmış görünecek')
    return None


def mesaj(d):
    return (f'Bu ayki {d.sinir} belge hakkınız doldu. Hakkınız {yenilenme_metni(d.donem)} '
            f'yenilenir. Bu ay indirdiğiniz belgeleri yeniden düzenleyip indirebilirsiniz.')


def istemci_bilgisi(d):
    """Tarayıcıya giden özet (analiz cevabında)."""
    return {'kullanilan': d.kullanilan, 'sinir': d.sinir, 'izin': d.izin,
            'mesaj': None if d.izin else mesaj(d)}
