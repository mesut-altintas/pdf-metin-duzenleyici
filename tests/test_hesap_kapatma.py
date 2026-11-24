"""Hesabı kapatma ve anonimleştirme; kullanım kayıtlarının saklama sınırı (aydınlatma metni md. 7).
Yerel PostgreSQL ister."""
import re

import psycopg2
import pytest
from werkzeug.security import generate_password_hash

import giris
import kullanicilar as vt
from tests.conftest import TEST_SIFRE

SIFRE = 'ayse-sifre-123'


@pytest.fixture
def app(vt_db):
    import app as uygulama
    giris._denemeler.clear()
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _csrf(ist, yol='/giris'):
    sayfa = ist.get(yol).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', sayfa).group(1)


def _giris(app, kim, sifre):
    ist = app.test_client()
    r = ist.post('/giris', data={'kullanici': kim, 'sifre': sifre, 'csrf_token': _csrf(ist)})
    return ist, r


def _hepsi(vt_db, sql, params=()):
    conn = psycopg2.connect(vt_db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def test_kapatma_adi_e_postayi_sifreyi_siler_kayitlardaki_adi_degistirir(app, vt_db):
    vt.ekle('ayse', 'Ayşe Yılmaz', generate_password_hash(SIFRE), False, False, 'deneme', eposta='ayse@ornek.com')
    ist, r = _giris(app, 'ayse@ornek.com', SIFRE)          # kullanım kaydı: giriş
    assert r.status_code == 302 and ist.get('/').status_code == 200
    vt.sifre_ata('ayse', generate_password_hash('yeni-sifre-12'), False, 'ayse', 'kendi şifresini değiştirdi')
    onceki_olay = _hepsi(vt_db, 'SELECT count(*) FROM kullanim_olayi')[0][0]

    anon = vt.kapat_anonimlestir('ayse', 'deneme')
    assert anon and anon.startswith(vt.ANONIM_ONEK) and giris.AD_DESENI.fullmatch(anon)

    k = vt.bul(anon)
    assert vt.bul('ayse') is None and vt.bul_eposta('ayse@ornek.com') is None
    assert (k['gorunen_ad'], k['eposta'], k['sifre_ozeti'], k['aktif'], k['yonetici'], k['son_giris']) == \
        ('', None, '!', False, False, None)
    # Adı hiçbir tabloda kalmaz; sayılar kalır.
    assert _hepsi(vt_db, "SELECT count(*) FROM kullanim_olayi WHERE kullanici = 'ayse'")[0][0] == 0
    assert _hepsi(vt_db, 'SELECT count(*) FROM kullanim_olayi')[0][0] == onceki_olay
    assert _hepsi(vt_db, "SELECT count(*) FROM yonetim_kaydi WHERE yapan = 'ayse' OR hedef = 'ayse'")[0][0] == 0
    assert _hepsi(vt_db, "SELECT count(*) FROM hesap WHERE ad = 'ayse'")[0][0] == 0
    assert _hepsi(vt_db, 'SELECT ad FROM hesap WHERE id = %s', (k['hesap_id'],))[0][0] == anon
    tum = ' '.join(str(x) for satir in _hepsi(vt_db, 'SELECT * FROM kullanicilar') for x in satir)
    assert 'ayse@ornek.com' not in tum and 'Ayşe' not in tum
    assert 'hesabı kapatıldı, kişisel verileri anonimleştirildi' in [x['islem'] for x in vt.kayitlar()]
    # Oturum düşer, eski bilgilerle girilemez; adres yeniden kullanılabilir.
    vt.onbellek_sil()
    assert ist.get('/').status_code == 302
    assert _giris(app, 'ayse@ornek.com', 'yeni-sifre-12')[1].status_code == 401
    assert vt.ekle('ayse2', '', 'x', False, False, 'deneme', eposta='ayse@ornek.com')
    # İkinci kez: yok.
    assert vt.kapat_anonimlestir('ayse', 'deneme') is None


def test_yonetim_kapatilmis_hesabi_degistiremez(app):
    vt.ekle('veli', '', generate_password_hash(SIFRE), False, False, 'deneme')
    anon = vt.kapat_anonimlestir('veli', 'deneme')
    yon, _ = _giris(app, 'deneme', TEST_SIFRE)
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    satir = sayfa[sayfa.index(anon):sayfa.index('</tr>', sayfa.index(anon))]
    assert 'Kapatıldı (anonim)' in satir and '<form' not in satir
    for uc, veri in (('aktif', {'deger': '1'}), ('sifre', {}), ('plan', {'plan': 'hediye'}), ('sil', {}),
                     ('eposta', {'eposta': 'x@ornek.com'}), ('yonetici', {'deger': '1'})):
        yon.post(f'/yonetim/{anon}/{uc}', data={**veri, 'csrf_token': _csrf(yon, '/yonetim/')})
        assert 'Kapatılmış hesap değiştirilemez' in yon.get('/yonetim/').get_data(as_text=True), uc
    k = vt.bul(anon)
    assert not k['aktif'] and k['eposta'] is None


def test_kullanim_kayitlari_iki_yildan_eskisi_silinir(vt_db):
    vt.liste('2026-11')                                   # şema kurulsun
    conn = psycopg2.connect(vt_db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO kullanim_olayi (kullanici, tur, zaman) VALUES "
                    "('eski', 'giris', now() - interval '2 years 1 day'), "
                    "('sinirda', 'giris', now() - interval '2 years' + interval '1 day'), "
                    "('yeni', 'giris', now())")
    conn.close()
    vt._kapat()
    vt._sema_kuruldu = False
    vt.liste('2026-11')                                   # yeni bağlantı: sınır uygulanır
    kalan = sorted(r[0] for r in _hepsi(vt_db, 'SELECT kullanici FROM kullanim_olayi'))
    assert kalan == ['sinirda', 'yeni']
