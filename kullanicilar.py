"""Kullanıcı deposu: Neon Postgres (DATABASE_URL).

Ortam değişkenindeki KULLANICILAR kalıcı yöneticilerdir ve burada değil
`giris.py`'de okunur; sayfadan değiştirilemezler.

Kurallar:
  * Her yazma, yönetim kaydıyla birlikte tek ifadede (CTE) yapılır: biri olup
    öbürü olmaz.
  * Okuma bağlantı koparsa bir kez yeniden denenir; yazma denenmez (cevabı
    alınamayan yazmanın sonucu bilinmez) — `SonucBilinmiyor` atılır.
  * Oturumu geçersiz kılan her değişiklik (şifre, pasife alma, yöneticilik)
    `oturum_surum`'u artırır; açık oturumlar bir sonraki denetimde düşer.
"""
import logging
import os
import secrets
import threading
import time

import psycopg2
import psycopg2.extras

import planlar

log = logging.getLogger(__name__)

ONBELLEK_SN = 30          # oturum denetiminin süreç içi önbelleği
DENEME_PENCERESI = 15 * 60

_SEMA = [
    """CREATE TABLE IF NOT EXISTS kullanicilar (
        ad TEXT PRIMARY KEY,
        gorunen_ad TEXT NOT NULL DEFAULT '',
        sifre_ozeti TEXT NOT NULL,
        yonetici BOOLEAN NOT NULL DEFAULT FALSE,
        aktif BOOLEAN NOT NULL DEFAULT TRUE,
        sifre_degistirmeli BOOLEAN NOT NULL DEFAULT TRUE,
        oturum_surum INTEGER NOT NULL DEFAULT 1,
        olusturan TEXT NOT NULL,
        olusturma TIMESTAMPTZ NOT NULL DEFAULT now(),
        son_giris TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS yonetim_kaydi (
        id BIGSERIAL PRIMARY KEY,
        zaman TIMESTAMPTZ NOT NULL DEFAULT now(),
        yapan TEXT NOT NULL,
        islem TEXT NOT NULL,
        hedef TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS giris_denemesi (
        anahtar TEXT NOT NULL,
        zaman TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS giris_denemesi_anahtar_zaman ON giris_denemesi (anahtar, zaman)",
    # Kullanım ölçümü (kullanim.py). Dosya adı / içerik / metin sütunu yok, olmayacak.
    """CREATE TABLE IF NOT EXISTS kullanim_olayi (
        id BIGSERIAL PRIMARY KEY,
        zaman TIMESTAMPTZ NOT NULL DEFAULT now(),
        kullanici TEXT NOT NULL,
        tur TEXT NOT NULL,
        basarili BOOLEAN NOT NULL DEFAULT TRUE,
        hata TEXT,
        sayfa INTEGER,
        boyut INTEGER,
        duzeltme INTEGER,
        uyari INTEGER,
        sure_ms INTEGER,
        belge TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS kullanim_olayi_zaman ON kullanim_olayi (zaman)",
    # 1.6: eklenen metin sayısı (var olan satırlara dokunmaz).
    "ALTER TABLE kullanim_olayi ADD COLUMN IF NOT EXISTS ekleme INTEGER",
    # 1.17: eklenen resim sayısı.
    "ALTER TABLE kullanim_olayi ADD COLUMN IF NOT EXISTS resim INTEGER",
    # 1.9: hesap (ödeyen birim) ve aylık belge kotası (planlar.py, kota.py).
    # Hesap kullanıcıdan ayrı: ekip planında birden çok kullanıcı aynı hesaba bağlanır.
    """CREATE TABLE IF NOT EXISTS hesap (
        id BIGSERIAL PRIMARY KEY,
        ad TEXT NOT NULL DEFAULT '',
        plan TEXT NOT NULL,
        olusturma TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS hesap_id BIGINT REFERENCES hesap (id)",
    # Ay (İstanbul) başına sayılan farklı belge sayısı; sınır denetimi bu satırın kilidiyle.
    """CREATE TABLE IF NOT EXISTS kota_sayaci (
        hesap_id BIGINT NOT NULL REFERENCES hesap (id),
        donem TEXT NOT NULL,
        belge INTEGER NOT NULL,
        PRIMARY KEY (hesap_id, donem)
    )""",
    # Bu ay sayılmış belgeler: yalnız parmak izi (kullanim.parmak_izi), dosya adı / içerik yok.
    """CREATE TABLE IF NOT EXISTS kota_belge (
        hesap_id BIGINT NOT NULL REFERENCES hesap (id),
        donem TEXT NOT NULL,
        belge TEXT NOT NULL,
        zaman TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (hesap_id, donem, belge)
    )""",
    # 1.10: e-postayla kayıt ve şifre yenileme (kayit.py). Adres küçük harfle saklanır.
    "ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS eposta TEXT",
    "ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS eposta_dogrulandi TIMESTAMPTZ",
    "ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS kosul_kabul TIMESTAMPTZ",
    "ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS kosul_surum TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS kullanicilar_eposta ON kullanicilar (eposta)",
]

# Hesabı olmayan her kullanıcıya kendi hesabı (1.9 geçişi). Her soğuk başlangıçta
# koşar; hesapsız kullanıcı yoksa hiçbir şey yazmaz. Eşzamanlı iki başlangıçta
# ikincisi satır kilidini bekler, `hesap_id IS NULL` artık tutmaz: çift hesap
# açılmaz. Yabancı anahtar ifadenin sonunda denetlenir (önce kullanıcıya numara,
# sonra o numarayla hesap — tek ifadede).
_GECIS_SQL = """
    WITH k AS (
        UPDATE kullanicilar SET hesap_id = nextval(pg_get_serial_sequence('hesap', 'id'))
        WHERE hesap_id IS NULL RETURNING ad, hesap_id
    )
    INSERT INTO hesap (id, ad, plan) SELECT hesap_id, ad, %s FROM k"""


# Kullanım kayıtları en çok bu kadar yıl saklanır (aydınlatma metni md. 7). Silme her
# soğuk başlangıçta, şema kurulduktan sonra koşar (zaman dizini var; ucuz).
KULLANIM_SAKLAMA_YIL = 2


def kullanim_saklama_siniri():
    """Süresi dolan kullanım kayıtlarını siler; silinen sayısı (hatada None). Bağlantı
    kurulurken çağrılır: hata kullanıcının işini durdurmaz, log'a düşer."""
    try:
        with _baglanti.cursor() as cur:
            cur.execute('DELETE FROM kullanim_olayi WHERE zaman < now() - make_interval(years => %s)',
                        (KULLANIM_SAKLAMA_YIL,))
            return cur.rowcount
    except psycopg2.Error:
        log.error('kullanım kayıtlarının saklama sınırı uygulanamadı', exc_info=True)
        return None


class DepoHatasi(Exception):
    """Kullanıcıya gösterilecek depo hatası."""


class SonucBilinmiyor(DepoHatasi):
    pass


class EpostaKullaniliyor(DepoHatasi):
    """Adres başka bir kullanıcıda (benzersiz dizin)."""


_kilit = threading.RLock()
_baglanti = None
_sema_kuruldu = False
_onbellek = {}


def tanimli():
    return bool(os.environ.get('DATABASE_URL'))


def _kapat():
    global _baglanti
    try:
        if _baglanti is not None:
            _baglanti.close()
    except Exception:
        pass
    _baglanti = None


def _baglan():
    global _baglanti, _sema_kuruldu
    if not tanimli():
        raise DepoHatasi('Veritabanı tanımlı değil (DATABASE_URL).')
    if _baglanti is None or _baglanti.closed:
        _baglanti = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=10,
                                     application_name='pdf-editor')
        _baglanti.autocommit = True
    if not _sema_kuruldu:
        for ddl in _SEMA:
            try:
                with _baglanti.cursor() as cur:
                    cur.execute(ddl)
            except psycopg2.Error:
                # Eşzamanlı iki soğuk başlangıç aynı tabloyu kurarsa biri düşer; tablo vardır.
                log.warning('şema adımı atlandı', exc_info=True)
        try:
            with _baglanti.cursor() as cur:
                cur.execute(_GECIS_SQL, (planlar.GECIS,))
        except psycopg2.Error:
            log.error('hesap geçişi yapılamadı', exc_info=True)
        kullanim_saklama_siniri()
        _sema_kuruldu = True
    return _baglanti


def _oku(sql, params=(), tek=False):
    with _kilit:
        for deneme in (1, 2):
            try:
                with _baglan().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() if tek else cur.fetchall()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                _kapat()
                if deneme == 2:
                    log.exception('okuma başarısız')
                    raise DepoHatasi('Kullanıcı bilgisi okunamadı. Biraz sonra yeniden deneyin.')


def _yaz(sql, params=(), getir=False):
    """Etkilenen satır sayısı (getir=True: dönen ilk satır, sözlük). Bağlantı
    yazmadan önce kurulur; yazma sırasında koparsa yeniden gönderilmez."""
    with _kilit:
        try:
            conn = _baglan()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _kapat()
            log.exception('bağlantı kurulamadı')
            raise DepoHatasi('Veritabanına bağlanılamadı. Biraz sonra yeniden deneyin.')
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone() if getir else cur.rowcount
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _kapat()
            log.exception('yazma sırasında bağlantı koptu')
            raise SonucBilinmiyor('İşlemin sonucu alınamadı. Listeyi yenileyip değişikliğin olup '
                                  'olmadığına bakın.')


def sorgu(sql, params=()):
    """Başka modüller için okuma (kullanim.py)."""
    return _oku(sql, params)


def yazma(sql, params=()):
    """Başka modüller için yazma (kullanim.py)."""
    return _yaz(sql, params)


def yazma_getir(sql, params=()):
    """Başka modüller için, satır döndüren yazma (kota.py). Kurallar `_yaz`'la aynı."""
    return _yaz(sql, params, getir=True)


def onbellek_sil(ad=None):
    if ad is None:
        _onbellek.clear()
    else:
        _onbellek.pop(ad, None)


# ---------------------------------------------------------------- okuma

def bul(ad):
    return _oku('SELECT * FROM kullanicilar WHERE ad = %s', (ad,), tek=True)


def bul_eposta(eposta):
    return _oku('SELECT * FROM kullanicilar WHERE eposta = %s', (eposta,), tek=True)


def oturum_kaydi(ad):
    """Oturum denetimi için kayıt; süreç içinde kısa süre önbellekli."""
    simdi = time.monotonic()
    zaman, kayit = _onbellek.get(ad, (0.0, None))
    if simdi - zaman < ONBELLEK_SN and zaman:
        return kayit
    kayit = bul(ad)
    _onbellek[ad] = (simdi, kayit)
    return kayit


def liste(donem):
    """Kullanıcılar, hesaplarının planı ve `donem` ayında sayılan belge sayısıyla."""
    return _oku('SELECT k.ad, k.gorunen_ad, k.yonetici, k.aktif, k.sifre_degistirmeli, k.olusturan, '
                'k.olusturma, k.son_giris, k.eposta, h.plan, s.belge AS bu_ay '
                'FROM kullanicilar k LEFT JOIN hesap h ON h.id = k.hesap_id '
                'LEFT JOIN kota_sayaci s ON s.hesap_id = h.id AND s.donem = %s '
                'ORDER BY k.aktif DESC, k.ad', (donem,))


def kayitlar(sinir=50):
    return _oku('SELECT zaman, yapan, islem, hedef FROM yonetim_kaydi ORDER BY id DESC LIMIT %s',
                (sinir,))


# ---------------------------------------------------------------- yazma (kayıtlı)

def _kayitli(guncelleme, params, yapan, islem):
    """guncelleme: 'ad' döndüren UPDATE/INSERT/DELETE ... RETURNING ad.
    Yönetim kaydı yalnız etkilenen satır için yazılır; dönen: etkilenen sayı."""
    sql = (f'WITH g AS ({guncelleme}) '
           'INSERT INTO yonetim_kaydi (yapan, islem, hedef) SELECT %s, %s, ad FROM g')
    return _yaz(sql, (*params, yapan, islem))


def ekle(ad, gorunen_ad, sifre_ozeti, yonetici, sifre_degistirmeli, yapan, plan=planlar.VARSAYILAN,
         eposta=None):
    """Kullanıcıyı kendi hesabıyla ekler. Eklendiyse True; aynı ad ya da aynı e-posta
    varsa False (hesap da açılmaz; hangisi olduğunu çağıran `bul` ile ayırır).
    Kullanıcı, hesap ve yönetim kaydı tek ifadede."""
    islem = 'eklendi' + (' (yönetici)' if yonetici else '') + f', plan: {planlar.bul(plan).ad}'
    n = _yaz(
        'WITH g AS ('
        ' INSERT INTO kullanicilar (ad, gorunen_ad, sifre_ozeti, yonetici, sifre_degistirmeli, olusturan,'
        '  hesap_id, eposta)'
        " VALUES (%s, %s, %s, %s, %s, %s, nextval(pg_get_serial_sequence('hesap', 'id')), %s)"
        ' ON CONFLICT DO NOTHING RETURNING ad, hesap_id'
        '), h AS (INSERT INTO hesap (id, ad, plan) SELECT hesap_id, ad, %s FROM g) '
        'INSERT INTO yonetim_kaydi (yapan, islem, hedef) SELECT %s, %s, ad FROM g',
        (ad, gorunen_ad, sifre_ozeti, yonetici, sifre_degistirmeli, yapan, eposta, plan, yapan, islem))
    return n == 1


KAYIT_YAPAN = '(kayıt)'      # kendi kaydolanların "ekleyen"i; kullanıcı adı desenine uymaz


def kayit_ekle(ad, eposta, gorunen_ad, sifre_ozeti, kosul_surum):
    """E-postadaki bağlantıyla kaydolan kullanıcı (adres sahipliği kanıtlı). Dönen:
    'eklendi' | 'eposta' (adres zaten kayıtlı) | 'ad' (ad dolu, başka ad denenmeli)."""
    plan = planlar.VARSAYILAN
    n = _yaz(
        'WITH g AS ('
        ' INSERT INTO kullanicilar (ad, gorunen_ad, sifre_ozeti, sifre_degistirmeli, olusturan, hesap_id,'
        '  eposta, eposta_dogrulandi, kosul_kabul, kosul_surum)'
        " VALUES (%s, %s, %s, FALSE, %s, nextval(pg_get_serial_sequence('hesap', 'id')),"
        '  %s, now(), now(), %s)'
        ' ON CONFLICT DO NOTHING RETURNING ad, hesap_id'
        '), h AS (INSERT INTO hesap (id, ad, plan) SELECT hesap_id, ad, %s FROM g) '
        'INSERT INTO yonetim_kaydi (yapan, islem, hedef) SELECT %s, %s, ad FROM g',
        (ad, gorunen_ad, sifre_ozeti, KAYIT_YAPAN, eposta, kosul_surum, plan,
         KAYIT_YAPAN, f'kaydoldu, plan: {planlar.bul(plan).ad}'))
    if n == 1:
        return 'eklendi'
    return 'eposta' if bul_eposta(eposta) else 'ad'


def eposta_ata(ad, eposta, yapan):
    """Yönetici adresi atar / değiştirir (None: kaldırır). Değiştiyse True; kullanıcı
    yoksa ya da adres aynıysa False. Adres başkasındaysa EpostaKullaniliyor.
    Yöneticinin yazdığı adres doğrulanmış sayılmaz."""
    try:
        n = _kayitli(
            'UPDATE kullanicilar SET eposta = %s, eposta_dogrulandi = NULL '
            'WHERE ad = %s AND eposta IS DISTINCT FROM %s RETURNING ad',
            (eposta, ad, eposta), yapan, 'e-postası değiştirildi' if eposta else 'e-postası kaldırıldı')
    except psycopg2.IntegrityError:
        raise EpostaKullaniliyor('Bu e-posta başka bir kullanıcıda.')
    return n == 1


def sifre_yenile(ad, eposta, surum, sifre_ozeti):
    """E-postadaki bağlantıyla yeni şifre. Bağlantı tek kullanımlık: kullanıcının oturum
    sürümü ve adresi bağlantıdakiyle aynıysa yazar; yazınca sürüm artar, bağlantı ve açık
    oturumlar düşer. Yenilendiyse True."""
    n = _kayitli(
        'UPDATE kullanicilar SET sifre_ozeti = %s, sifre_degistirmeli = FALSE, '
        'eposta_dogrulandi = COALESCE(eposta_dogrulandi, now()), oturum_surum = oturum_surum + 1 '
        'WHERE ad = %s AND eposta = %s AND oturum_surum = %s AND aktif RETURNING ad',
        (sifre_ozeti, ad, eposta, surum), ad, 'şifresini e-postayla yeniledi')
    onbellek_sil(ad)
    return n == 1


def plan_ata(ad, plan, yapan):
    """Kullanıcının hesabının planını değiştirir. Değiştiyse True; kullanıcı yoksa
    ya da plan zaten buysa False."""
    return _kayitli(
        'UPDATE hesap h SET plan = %s FROM kullanicilar k '
        'WHERE k.ad = %s AND h.id = k.hesap_id AND h.plan <> %s RETURNING k.ad AS ad',
        (plan, ad, plan), yapan, f'planı “{planlar.bul(plan).ad}” yapıldı') == 1


def sifre_ata(ad, sifre_ozeti, degistirmeli, yapan, islem):
    n = _kayitli(
        'UPDATE kullanicilar SET sifre_ozeti = %s, sifre_degistirmeli = %s, '
        'oturum_surum = oturum_surum + 1 WHERE ad = %s RETURNING ad',
        (sifre_ozeti, degistirmeli, ad), yapan, islem)
    onbellek_sil(ad)
    return n == 1


def aktiflik(ad, aktif, yapan):
    n = _kayitli(
        'UPDATE kullanicilar SET aktif = %s, oturum_surum = oturum_surum + 1 '
        'WHERE ad = %s AND aktif <> %s RETURNING ad',
        (aktif, ad, aktif), yapan, 'etkinleştirildi' if aktif else 'pasife alındı')
    onbellek_sil(ad)
    return n == 1


def yoneticilik(ad, yonetici, yapan):
    n = _kayitli(
        'UPDATE kullanicilar SET yonetici = %s, oturum_surum = oturum_surum + 1 '
        'WHERE ad = %s AND yonetici <> %s RETURNING ad',
        (yonetici, ad, yonetici), yapan, 'yönetici yapıldı' if yonetici else 'yöneticiliği kaldırıldı')
    onbellek_sil(ad)
    return n == 1


def sil(ad, yapan):
    n = _kayitli('DELETE FROM kullanicilar WHERE ad = %s RETURNING ad', (ad,), yapan, 'silindi')
    onbellek_sil(ad)
    return n == 1


ANONIM_ONEK = 'silinmis-'      # kapatılmış hesabın yeni kimliği: silinmis-<12 hane>


def anonim_mi(ad):
    return bool(ad) and ad.startswith(ANONIM_ONEK)


def kapat_anonimlestir(ad, yapan):
    """Hesabı kapatır ve kişisel verileri anonimleştirir (aydınlatma metni md. 7;
    KVKK m. 7). Tek ifadede: kullanıcı adı rastgele bir kimlikle değişir; ad soyad,
    e-posta, şifre özeti ve son giriş silinir, giriş kapanır; aynı ad hesap tablosunda,
    kullanım kayıtlarında ve yönetim kaydında da yeni kimlikle değiştirilir. Sayılar
    (kullanım, kota) kalır, kişiyle ilişkisi kalmaz. Dönen: yeni kimlik; kullanıcı
    yoksa ya da zaten kapatılmışsa None."""
    anon = ANONIM_ONEK + secrets.token_hex(6)
    p = {'ad': ad, 'anon': anon, 'yapan': yapan,
         'islem': 'hesabı kapatıldı, kişisel verileri anonimleştirildi'}
    satir = _yaz("""
        WITH k AS (
            UPDATE kullanicilar SET ad = %(anon)s, gorunen_ad = '', eposta = NULL,
                eposta_dogrulandi = NULL, sifre_ozeti = '!', aktif = FALSE, yonetici = FALSE,
                sifre_degistirmeli = FALSE, son_giris = NULL, oturum_surum = oturum_surum + 1
            WHERE ad = %(ad)s RETURNING hesap_id
        ), h AS (
            UPDATE hesap SET ad = %(anon)s WHERE id IN (SELECT hesap_id FROM k) RETURNING 1
        ), o AS (
            UPDATE kullanim_olayi SET kullanici = %(anon)s
            WHERE kullanici = %(ad)s AND EXISTS (SELECT 1 FROM k) RETURNING 1
        ), y AS (
            UPDATE yonetim_kaydi SET
                yapan = CASE WHEN yapan = %(ad)s THEN %(anon)s ELSE yapan END,
                hedef = CASE WHEN hedef = %(ad)s THEN %(anon)s ELSE hedef END
            WHERE (yapan = %(ad)s OR hedef = %(ad)s) AND EXISTS (SELECT 1 FROM k) RETURNING 1
        ), g AS (
            INSERT INTO yonetim_kaydi (yapan, islem, hedef)
            SELECT %(yapan)s, %(islem)s, %(anon)s FROM k RETURNING 1
        )
        SELECT (SELECT count(*) FROM k) AS n""", p, getir=True)
    onbellek_sil(ad)
    return anon if satir and satir['n'] == 1 else None


def hesap_gecisi():
    """Hesapsız kullanıcılara hesap açar (`_GECIS_SQL`); açılan sayısı."""
    return _yaz(_GECIS_SQL, (planlar.GECIS,))


def son_giris_yaz(ad):
    _yaz('UPDATE kullanicilar SET son_giris = now() WHERE ad = %s', (ad,))


# ---------------------------------------------------------------- giriş denemeleri

def deneme_ekle(*anahtarlar):
    if not anahtarlar:
        return
    degerler = ', '.join(['(%s)'] * len(anahtarlar))
    _yaz(f'INSERT INTO giris_denemesi (anahtar) VALUES {degerler}', anahtarlar)
    _yaz('DELETE FROM giris_denemesi WHERE zaman < now() - make_interval(secs => %s)',
         (DENEME_PENCERESI * 4,))


def deneme_sayisi(anahtar):
    satir = _oku('SELECT count(*) AS n FROM giris_denemesi WHERE anahtar = %s '
                 'AND zaman > now() - make_interval(secs => %s)', (anahtar, DENEME_PENCERESI), tek=True)
    return int(satir['n']) if satir else 0


def denemeleri_sil(*anahtarlar):
    if anahtarlar:
        _yaz('DELETE FROM giris_denemesi WHERE anahtar = ANY(%s)', (list(anahtarlar),))
