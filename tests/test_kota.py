"""Plan, hesap ve aylık belge kotası (kota.py, planlar.py). Veritabanı testleri yerel
PostgreSQL ister (TEST_PG_URL); eşzamanlılık testleri ayrı bağlantılarla koşar."""
import re
import threading
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import pytest
from werkzeug.security import generate_password_hash

import giris
import kota
import kullanicilar as vt
import planlar
from tests.conftest import TEST_SIFRE

SIMDI = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
SIFRE = 'ayse-sifre-123'


def _k(ad):
    return giris.Kullanici(ad, 'vt', False)


def _ayri_baglanti(url):
    """Başka bir Vercel örneği gibi: kendi bağlantısı, kendi işlemi."""
    conn = psycopg2.connect(url)
    conn.autocommit = True

    def calistir(sql, p):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, p)
            return cur.fetchone()
    return conn, calistir


# ---------------------------------------------------------------- veritabanısız

def test_donem_istanbul_saatiyle_ve_yenilenme_metni():
    assert kota.donem(datetime(2026, 11, 30, 20, 59, tzinfo=timezone.utc)) == '2026-11'
    assert kota.donem(datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc)) == '2026-12'   # İstanbul 00:00
    assert kota.yenilenme_metni('2026-11') == "1 Aralık'ta"
    assert kota.yenilenme_metni('2026-12') == "1 Ocak'ta"
    assert kota.yenilenme_metni('2027-08') == "1 Eylül'de"


def test_tanimsiz_plan_sinirsiz_sayilir():
    assert planlar.bul('ucretsiz').aylik_belge == 3
    assert planlar.bul('yok-boyle-plan').aylik_belge is None


def test_ortam_yoneticisi_ve_veritabanisiz_ortam_kotasiz():
    assert kota.durum(giris.Kullanici('deneme', 'ortam', True), 'x') is None
    assert kota.durum(_k('ayse'), 'x') is None          # DATABASE_URL yok (conftest)


# ---------------------------------------------------------------- hesap ve geçiş

def test_gecis_hesapsiz_kullanicilara_kendi_hediye_hesabini_bir_kez_acar(vt_db):
    conn = psycopg2.connect(vt_db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(vt._SEMA[0])          # 1.8'deki tablo: hesap_id sütunu yok
        cur.execute("INSERT INTO kullanicilar (ad, sifre_ozeti, olusturan) "
                    "VALUES ('ali', 'x', 't'), ('veli', 'x', 't')")
    vt.liste(kota.donem())                # ilk bağlantı: şema + geçiş
    with conn.cursor() as cur:
        cur.execute('SELECT k.ad, h.ad, h.plan FROM kullanicilar k JOIN hesap h ON h.id = k.hesap_id '
                    'ORDER BY k.ad')
        assert cur.fetchall() == [('ali', 'ali', 'hediye'), ('veli', 'veli', 'hediye')]
    assert vt.hesap_gecisi() == 0
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM hesap')
        assert cur.fetchone()[0] == 2
    conn.close()


def test_eszamanli_gecisler_cift_hesap_acmaz(vt_db):
    vt.liste(kota.donem())                # şema kurulsun
    conn = psycopg2.connect(vt_db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO kullanicilar (ad, sifre_ozeti, olusturan) "
                    "SELECT 'k' || i, 'x', 't' FROM generate_series(1, 200) i")
    baglantilar = [_ayri_baglanti(vt_db)[0] for _ in range(6)]
    engel = threading.Barrier(len(baglantilar))

    def gec(c):
        engel.wait()
        with c.cursor() as cur:
            cur.execute(vt._GECIS_SQL, (planlar.GECIS,))

    isler = [threading.Thread(target=gec, args=(c,)) for c in baglantilar]
    [t.start() for t in isler]
    [t.join() for t in isler]
    with conn.cursor() as cur:
        cur.execute('SELECT count(*), count(DISTINCT hesap_id), count(hesap_id) FROM kullanicilar')
        assert cur.fetchone() == (200, 200, 200)
        cur.execute('SELECT count(*) FROM hesap')
        assert cur.fetchone()[0] == 200
    for c in baglantilar + [conn]:
        c.close()


def test_ekle_kendi_hesabini_acar_ayni_adda_hesap_acilmaz(vt_db):
    assert vt.ekle('ayse', '', 'x', False, False, 'deneme')
    assert vt.ekle('mehmet', '', 'x', False, False, 'deneme', 'hediye')
    assert not vt.ekle('ayse', '', 'x', False, False, 'deneme')
    assert {r['ad']: r['plan'] for r in vt.liste(kota.donem())} == {'ayse': 'ucretsiz', 'mehmet': 'hediye'}
    assert vt.sorgu('SELECT count(*) AS n FROM hesap')[0]['n'] == 2
    islemler = [r['islem'] for r in vt.kayitlar()]
    assert 'eklendi, plan: Ücretsiz' in islemler and 'eklendi, plan: Sınırsız (hediye)' in islemler


# ---------------------------------------------------------------- sayma

def test_ayni_belge_ayda_bir_kez_sayilir_sinirda_yeni_belge_reddedilir(vt_db):
    vt.ekle('ayse', '', 'x', False, False, 'deneme')
    k = _k('ayse')
    for i, belge in enumerate(['a', 'b', 'c'], 1):
        d = kota.durum(k, belge, SIMDI)
        assert d.izin and not d.sayildi
        assert kota.say(d, belge) == i
    d = kota.durum(k, 'a', SIMDI)
    assert d.sayildi and d.izin
    assert kota.say(d, 'a') == 3          # aynı belge yeniden: sayılmaz
    d = kota.durum(k, 'd', SIMDI)
    assert not d.izin and d.kullanilan == 3
    assert "1 Aralık'ta yenilenir" in kota.mesaj(d)
    # Yeni ay: sayaç sıfırdan.
    d = kota.durum(k, 'd', datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
    assert d.izin and d.kullanilan == 0


def test_sinir_arada_dolarsa_sayilmaz_ve_isaret_kalmaz(vt_db):
    vt.ekle('ayse', '', 'x', False, False, 'deneme')
    k = _k('ayse')
    for belge in ('a', 'b'):
        kota.say(kota.durum(k, belge, SIMDI), belge)
    eski = kota.durum(k, 'z', SIMDI)       # 2/3 iken okundu
    kota.say(kota.durum(k, 'c', SIMDI), 'c')
    assert kota.say(eski, 'z') is None
    d = kota.durum(k, 'z', SIMDI)
    assert not d.sayildi and not d.izin and d.kullanilan == 3


def test_sinirsiz_plan_ve_kota_kapali_kotasiz(vt_db, monkeypatch):
    vt.ekle('mehmet', '', 'x', False, False, 'deneme', 'hediye')
    vt.ekle('ayse', '', 'x', False, False, 'deneme')
    assert kota.durum(_k('mehmet'), 'a', SIMDI) is None
    assert kota.durum(_k('ayse'), 'a', SIMDI) is not None
    monkeypatch.setenv('KOTA_KAPALI', '1')
    assert kota.durum(_k('ayse'), 'a', SIMDI) is None


def test_eszamanli_farkli_belgeler_siniri_asamaz(vt_db):
    vt.ekle('ayse', '', 'x', False, False, 'deneme')
    d0 = kota.durum(_k('ayse'), '', SIMDI)
    baglantilar = [_ayri_baglanti(vt_db) for _ in range(8)]
    engel = threading.Barrier(len(baglantilar))
    sonuclar = []

    def is_(i):
        _, calistir = baglantilar[i]
        d = kota.Durum(d0.hesap_id, d0.plan, 0, False, d0.donem)
        engel.wait()
        sonuclar.append(kota.say(d, f'belge-{i}', calistir))

    isler = [threading.Thread(target=is_, args=(i,)) for i in range(len(baglantilar))]
    [t.start() for t in isler]
    [t.join() for t in isler]
    assert sorted(x for x in sonuclar if x is not None) == [1, 2, 3]
    assert sonuclar.count(None) == 5
    assert vt.sorgu('SELECT belge FROM kota_sayaci')[0]['belge'] == 3
    assert vt.sorgu('SELECT count(*) AS n FROM kota_belge')[0]['n'] == 3
    for c, _ in baglantilar:
        c.close()


def test_eszamanli_ayni_belge_bir_kez_sayilir(vt_db):
    vt.ekle('ayse', '', 'x', False, False, 'deneme')
    d0 = kota.durum(_k('ayse'), '', SIMDI)
    baglantilar = [_ayri_baglanti(vt_db) for _ in range(8)]
    engel = threading.Barrier(len(baglantilar))
    sonuclar = []

    def is_(i):
        _, calistir = baglantilar[i]
        d = kota.Durum(d0.hesap_id, d0.plan, 0, False, d0.donem)
        engel.wait()
        sonuclar.append(kota.say(d, 'ayni-belge', calistir))

    isler = [threading.Thread(target=is_, args=(i,)) for i in range(len(baglantilar))]
    [t.start() for t in isler]
    [t.join() for t in isler]
    assert None not in sonuclar           # hepsi izinli: aynı belge
    assert vt.sorgu('SELECT belge FROM kota_sayaci')[0]['belge'] == 1
    for c, _ in baglantilar:
        c.close()


# ---------------------------------------------------------------- uçlar

@pytest.fixture
def app(vt_db, monkeypatch):
    import app as uygulama
    from pdf_duzenle import motor
    giris._denemeler.clear()
    monkeypatch.delenv('KOTA_KAPALI', raising=False)
    # Motor sahte: burada sınanan kota; motorun kendisi test_motor.py'de.
    uygulama.app.config['URETIM_SAYISI'] = 0

    def sahte_uygula(veri, *a, **k):
        uygulama.app.config['URETIM_SAYISI'] += 1
        return b'%PDF-sonuc ' + veri, []
    monkeypatch.setattr(motor, 'uygula', sahte_uygula)
    monkeypatch.setattr(motor, 'analiz', lambda veri, **k: {'sayfalar': [{'no': 1}]})
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _csrf(ist, yol='/giris'):
    sayfa = ist.get(yol).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', sayfa).group(1)


def _giris(app, ad, sifre):
    ist = app.test_client()
    r = ist.post('/giris', data={'kullanici': ad, 'sifre': sifre, 'csrf_token': _csrf(ist)})
    assert r.status_code == 302
    return ist


def _xhr(ist):
    return {'X-CSRFToken': _csrf(ist, '/'), 'X-Requested-With': 'XMLHttpRequest'}


def _uret(ist, veri):
    import io
    return ist.post('/api/uret', headers=_xhr(ist),
                    data={'pdf': (io.BytesIO(veri), 'a.pdf'), 'duzeltmeler': '[]'})


def _analiz(ist, veri):
    import io
    return ist.post('/api/analiz', headers=_xhr(ist), data={'pdf': (io.BytesIO(veri), 'a.pdf')})


def test_ucretsiz_kullanici_dorduncu_farkli_belgede_durur(app):
    vt.ekle('ayse', '', generate_password_hash(SIFRE), False, False, 'deneme')
    ist = _giris(app, 'ayse', SIFRE)
    assert 'bu ay <strong id="kota-kullanilan">0</strong> / 3 belge' in ist.get('/').get_data(as_text=True)
    for i in range(3):
        r = _uret(ist, f'%PDF belge {i}'.encode())
        assert r.status_code == 200 and r.headers['X-Kota'] == f'{i + 1}/3'
    r = _uret(ist, b'%PDF belge 0')               # aynı belge yeniden
    assert r.status_code == 200 and r.headers['X-Kota'] == '3/3'
    uretim = app.config['URETIM_SAYISI']
    r = _uret(ist, b'%PDF belge 9')
    assert r.status_code == 403
    assert app.config['URETIM_SAYISI'] == uretim      # dolu kotada motor hiç çalışmaz
    j = r.get_json()
    assert j['kod'] == 'kota' and 'hakkınız doldu' in j['hata'] and j['kota']['izin'] is False
    sayfa = ist.get('/').get_data(as_text=True)
    assert 'bu ay <strong id="kota-kullanilan">3</strong> / 3 belge' in sayfa and 'hakkınız doldu' in sayfa
    assert _analiz(ist, b'%PDF belge 9').get_json()['kota']['izin'] is False
    assert _analiz(ist, b'%PDF belge 1').get_json()['kota']['izin'] is True
    hatalar = [r['hata'] for r in vt.sorgu("SELECT hata FROM kullanim_olayi WHERE tur = 'uretildi'")]
    assert hatalar.count('kota') == 1


def test_basarisiz_uretim_sayilmaz(app, monkeypatch):
    from pdf_duzenle import motor

    def bozuk(*a, **k):
        raise motor.PdfHatasi('olmadı', kod='pdf')
    monkeypatch.setattr(motor, 'uygula', bozuk)
    vt.ekle('ayse', '', generate_password_hash(SIFRE), False, False, 'deneme')
    ist = _giris(app, 'ayse', SIFRE)
    assert _uret(ist, b'%PDF x').status_code == 400
    assert vt.sorgu('SELECT count(*) AS n FROM kota_sayaci')[0]['n'] == 0


def _cevir(ist, veri, bicim):
    import io
    return ist.post('/api/cevir', headers=_xhr(ist),
                    data={'pdf': (io.BytesIO(veri), 'a.pdf'), 'bicim': bicim, 'duzeltmeler': '[]'})


def test_cevirme_ayni_haktan_sayilir_ayni_belge_bir_kez(app, monkeypatch):
    """Kullanıcı kararı (26.11.2026): çevirme de belge hakkından; aynı belgeyi düzenleyip
    Word'e ve Excel'e çevirmek tek hak. Başarısız çevirme sayılmaz; dolu kotada çevrilmez."""
    from pdf_duzenle import cevir, motor
    cevrilen = []

    def sahte(bicim):
        def islev(veri):
            if b'bozuk' in veri:
                raise motor.PdfHatasi('tablo yok', kod='tablo_yok')
            cevrilen.append(bicim)
            return b'PK sahte', []
        return islev
    monkeypatch.setattr(cevir, 'word', sahte('word'))
    monkeypatch.setattr(cevir, 'excel', sahte('excel'))
    vt.ekle('ayse', '', generate_password_hash(SIFRE), False, False, 'deneme')
    ist = _giris(app, 'ayse', SIFRE)
    assert _uret(ist, b'%PDF A').headers['X-Kota'] == '1/3'
    assert _cevir(ist, b'%PDF A', 'word').headers['X-Kota'] == '1/3'
    assert _cevir(ist, b'%PDF A', 'excel').headers['X-Kota'] == '1/3'
    assert _cevir(ist, b'%PDF bozuk', 'excel').status_code == 400
    assert _cevir(ist, b'%PDF B', 'excel').headers['X-Kota'] == '2/3'
    assert _cevir(ist, b'%PDF C', 'word').headers['X-Kota'] == '3/3'
    r = _cevir(ist, b'%PDF D', 'word')
    assert r.status_code == 403 and r.get_json()['kod'] == 'kota'
    assert cevrilen == ['word', 'excel', 'excel', 'word']       # dolu kotada çevirme hiç çalışmaz
    turler = [(r['tur'], r['basarili'], r['hata']) for r in
              vt.sorgu("SELECT tur, basarili, hata FROM kullanim_olayi WHERE tur IN ('word', 'excel') ORDER BY id")]
    assert turler == [('word', True, None), ('excel', True, None), ('excel', False, 'tablo_yok'),
                      ('excel', True, None), ('word', True, None), ('word', False, 'kota')]
    # Değişiklik yoksa motor çalışmaz (yüklenen dosya olduğu gibi çevrilir).
    assert app.config['URETIM_SAYISI'] == 1


def test_ortam_yoneticisi_ve_hediye_plan_kotaya_takilmaz(app):
    vt.ekle('mehmet', '', generate_password_hash(SIFRE), False, False, 'deneme', 'hediye')
    for ist in (_giris(app, 'deneme', TEST_SIFRE), _giris(app, 'mehmet', SIFRE)):
        assert 'kota-kullanilan' not in ist.get('/').get_data(as_text=True)
        for i in range(5):
            r = _uret(ist, f'%PDF {i}'.encode())
            assert r.status_code == 200 and 'X-Kota' not in r.headers
        assert 'kota' not in _analiz(ist, b'%PDF 9').get_json()


def test_yonetici_plani_sayfadaki_formla_degistirir(app):
    vt.ekle('ayse', '', generate_password_hash(SIFRE), False, False, 'deneme')
    kullanici = _giris(app, 'ayse', SIFRE)
    for i in range(3):
        _uret(kullanici, f'%PDF {i}'.encode())
    assert _uret(kullanici, b'%PDF 9').status_code == 403

    yon = _giris(app, 'deneme', TEST_SIFRE)
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    assert 'Bu ay 3 / 3 belge' in sayfa
    eylem = re.search(r'<form method="post" action="([^"]+)" class="plan-form">', sayfa).group(1)
    assert eylem == '/yonetim/ayse/plan'
    r = yon.post(eylem, data={'plan': 'hediye', 'csrf_token': _csrf(yon, '/yonetim/')})
    assert r.status_code == 302
    assert 'planı “Sınırsız (hediye)” yapıldı' in [r['islem'] for r in vt.kayitlar()]
    assert _uret(kullanici, b'%PDF 9').status_code == 200

    r = yon.post(eylem, data={'plan': 'altin', 'csrf_token': _csrf(yon, '/yonetim/')})
    assert 'Geçersiz plan' in yon.get('/yonetim/').get_data(as_text=True)


def test_yeni_kullanici_formdan_secilen_planla_eklenir(app):
    yon = _giris(app, 'deneme', TEST_SIFRE)
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    assert '<option value="ucretsiz" selected>' in sayfa
    r = yon.post('/yonetim/ekle', data={'ad': 'zeynep', 'sifre': '', 'gorunen_ad': '', 'plan': 'hediye',
                                        'csrf_token': _csrf(yon, '/yonetim/')})
    assert r.status_code == 200 and 'Kullanıcı eklendi' in r.get_data(as_text=True)
    yon.post('/yonetim/ekle', data={'ad': 'kerem', 'sifre': '', 'gorunen_ad': '',
                                    'csrf_token': _csrf(yon, '/yonetim/')})
    assert {r['ad']: r['plan'] for r in vt.liste(kota.donem())} == {'zeynep': 'hediye', 'kerem': 'ucretsiz'}
