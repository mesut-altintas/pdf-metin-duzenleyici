"""Yönetim sayfası, veritabanı kullanıcıları ve oturum denetimi (yerel PostgreSQL)."""
import os
import re

import pytest

import giris
import kota
import kullanicilar as vt
from tests.conftest import TEST_SIFRE


@pytest.fixture
def app(vt_db):
    import app as uygulama
    giris._denemeler.clear()
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _csrf(istemci, yol='/giris'):
    sayfa = istemci.get(yol).get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', sayfa)
    return m.group(1)


def _giris(istemci, ad, sifre):
    return istemci.post('/giris', data={'kullanici': ad, 'sifre': sifre, 'csrf_token': _csrf(istemci)})


def _yonetici(app):
    """Ortam değişkenindeki yönetici ('deneme') ile giriş yapmış istemci."""
    ist = app.test_client()
    assert _giris(ist, 'deneme', TEST_SIFRE).status_code == 302
    return ist


def _post(ist, yol, **veri):
    veri['csrf_token'] = _csrf(ist, '/yonetim/')
    return ist.post(yol, data=veri)


def _ekle(ist, ad, sifre='', yonetici=False, degistirmeli=True, gorunen_ad=''):
    veri = {'ad': ad, 'sifre': sifre, 'gorunen_ad': gorunen_ad}
    if yonetici:
        veri['yonetici'] = '1'
    if degistirmeli:
        veri['degistirmeli'] = '1'
    return _post(ist, '/yonetim/ekle', **veri)


def _uretilen_sifre(yanit):
    m = re.search(r'id="uretilen-sifre">([^<]+)<', yanit.get_data(as_text=True))
    return m.group(1) if m else None


# ---------------------------------------------------------------- ekleme ve giriş

def test_yonetici_kullanici_ekler_uretilen_sifreyle_girilir(app):
    yon = _yonetici(app)
    r = _ekle(yon, 'ayse', gorunen_ad='Ayşe Yılmaz')
    assert r.status_code == 200 and 'Kullanıcı eklendi' in r.get_data(as_text=True)
    sifre = _uretilen_sifre(r)
    assert sifre and len(sifre) >= 10
    assert 'no-store' in r.headers['Cache-Control']
    # Şifre özet olarak saklanır.
    assert vt.bul('ayse')['sifre_ozeti'] != sifre
    # Liste sayfası yeni kullanıcıyı ve yönetim kaydını gösterir.
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    assert 'Ayşe Yılmaz' in sayfa and 'eklendi' in sayfa

    ist = app.test_client()
    r = _giris(ist, 'ayse', sifre)
    assert r.status_code == 302
    assert ist.get('/').headers['Location'].endswith('/sifre')   # ilk girişte şifre değiştirmeli


def test_liste_sayfasinin_formlari_gercek_uclara_gider(app):
    """Şablonun çizdiği form adresleri rotalarla aynı (şablon / rota arası kusur kaybolmasın)."""
    yon = _yonetici(app)
    _ekle(yon, 'ali', degistirmeli=False)
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    adresler = set(re.findall(r'action="([^"]+)"', sayfa))
    assert {'/yonetim/ekle', '/yonetim/ali/sifre', '/yonetim/ali/aktif', '/yonetim/ali/yonetici',
            '/yonetim/ali/sil', '/cikis'} <= adresler
    # Silme ve yönetici atama onay ister.
    assert sayfa.count('data-onay=') >= 3


def test_ilk_giriste_sifre_degistirme_zorunlu_ve_calisir(app):
    yon = _yonetici(app)
    gecici = _uretilen_sifre(_ekle(yon, 'veli'))
    ist = app.test_client()
    _giris(ist, 'veli', gecici)
    # PDF uçları da kapalı.
    r = ist.post('/api/analiz', headers={'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': _csrf(ist, '/sifre')})
    assert r.status_code == 403 and 'şifrenizi' in r.get_json()['hata']

    def degistir(mevcut, yeni, tekrar=None):
        return ist.post('/sifre', data={'mevcut': mevcut, 'yeni': yeni, 'tekrar': tekrar or yeni,
                                        'csrf_token': _csrf(ist, '/sifre')})
    assert 'Mevcut şifre hatalı' in degistir('yanlis-sifre-1', 'yeni-sifre-123').get_data(as_text=True)
    assert 'aynı değil' in degistir(gecici, 'yeni-sifre-123', 'baska-sifre-12').get_data(as_text=True)
    assert 'en az' in degistir(gecici, 'kisa').get_data(as_text=True)
    r = degistir(gecici, 'yeni-sifre-123')
    assert r.status_code == 302
    assert ist.get('/').status_code == 200            # oturum sürdü
    assert _giris(app.test_client(), 'veli', gecici).status_code == 401
    assert _giris(app.test_client(), 'veli', 'yeni-sifre-123').status_code == 302


def test_ortam_yoneticisi_sifresini_sayfadan_degistiremez(app):
    yon = _yonetici(app)
    sayfa = yon.get('/sifre').get_data(as_text=True)
    assert 'ortam değişkeninde' in sayfa
    assert 'name="yeni"' not in sayfa


def test_gecersiz_ad_kisa_sifre_ve_ortam_adi_reddedilir(app):
    yon = _yonetici(app)
    for ad in ('A', 'şule', 'a b', '../x', 'x' * 41):
        r = _ekle(yon, ad)
        assert r.status_code == 400, ad
    assert 'en az' in _ekle(yon, 'kemal', sifre='kisa').get_data(as_text=True)
    assert 'ortam değişkenindeki' in _ekle(yon, 'deneme').get_data(as_text=True)
    _ekle(yon, 'ayni')
    r = _ekle(yon, 'ayni')
    assert r.status_code == 400 and 'zaten var' in r.get_data(as_text=True)
    # Hatalı formda girilen değerler kaybolmaz.
    r = _ekle(yon, 'Gecersiz Ad', gorunen_ad='Gökhan Öz')
    assert 'Gökhan Öz' in r.get_data(as_text=True)
    assert [k['ad'] for k in vt.liste(kota.donem())] == ['ayni']


# ---------------------------------------------------------------- yetki

def test_yonetici_olmayan_yonetim_sayfasina_giremez(app):
    yon = _yonetici(app)
    _ekle(yon, 'normal', sifre='normal-sifre-1', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'normal', 'normal-sifre-1')
    assert ist.get('/').status_code == 200
    assert ist.get('/yonetim/').status_code == 403
    r = ist.post('/yonetim/ekle', data={'ad': 'sizma', 'csrf_token': _csrf(ist, '/')})
    assert r.status_code == 403
    assert vt.bul('sizma') is None
    assert 'Yönetim' not in ist.get('/').get_data(as_text=True)


def test_yonetici_atama_ve_kaldirma_oturumu_hemen_etkiler(app):
    yon = _yonetici(app)
    _ekle(yon, 'zeynep', sifre='zeynep-sifre-1', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'zeynep', 'zeynep-sifre-1')
    assert ist.get('/yonetim/').status_code == 403
    _post(yon, '/yonetim/zeynep/yonetici', deger='1')
    # Yöneticilik değişince oturum sürümü artar: eski oturum düşer, yeniden girince yönetici.
    assert ist.get('/yonetim/').status_code == 302
    _giris(ist, 'zeynep', 'zeynep-sifre-1')
    assert ist.get('/yonetim/').status_code == 200
    _post(yon, '/yonetim/zeynep/yonetici', deger='0')
    assert ist.get('/yonetim/').status_code == 302


def test_veritabani_yoneticisi_kendine_zarar_veremez(app):
    yon = _yonetici(app)
    _ekle(yon, 'mert', sifre='mert-sifre-123', yonetici=True, degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'mert', 'mert-sifre-123')
    belirtec = _csrf(ist, '/yonetim/')
    for yol, veri, mesaj in (('/yonetim/mert/aktif', {'deger': '0'}, 'pasife alamazsınız'),
                             ('/yonetim/mert/yonetici', {'deger': '0'}, 'kaldıramazsınız'),
                             ('/yonetim/mert/sil', {}, 'Kendinizi silemezsiniz'),
                             ('/yonetim/mert/sifre', {}, '“Şifre” sayfasından')):
        r = ist.post(yol, data={**veri, 'csrf_token': belirtec}, follow_redirects=True)
        assert mesaj in r.get_data(as_text=True), yol
    k = vt.bul('mert')
    assert k['aktif'] and k['yonetici']
    assert 'Kendi hesabınız' in ist.get('/yonetim/').get_data(as_text=True)


def test_ortam_yoneticisi_sayfadan_degistirilemez(app):
    yon = _yonetici(app)
    for yol in ('/yonetim/deneme/aktif', '/yonetim/deneme/sil', '/yonetim/deneme/yonetici'):
        _post(yon, yol, deger='0')
    assert yon.get('/').status_code == 200
    assert 'Bu sayfadan değiştirilemez' in yon.get('/yonetim/').get_data(as_text=True)


# ---------------------------------------------------------------- pasife alma, sıfırlama, silme

def test_pasife_alinan_oturumu_duser_girisi_kapanir_geri_acilir(app):
    yon = _yonetici(app)
    _ekle(yon, 'emre', sifre='emre-sifre-123', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'emre', 'emre-sifre-123')
    assert ist.get('/').status_code == 200
    _post(yon, '/yonetim/emre/aktif', deger='0')
    assert ist.get('/').status_code == 302
    assert _giris(app.test_client(), 'emre', 'emre-sifre-123').status_code == 401
    _post(yon, '/yonetim/emre/aktif', deger='1')
    assert _giris(app.test_client(), 'emre', 'emre-sifre-123').status_code == 302
    islemler = [r['islem'] for r in vt.kayitlar()]
    assert 'pasife alındı' in islemler and 'etkinleştirildi' in islemler


def test_sifre_sifirlama_eski_oturumu_ve_sifreyi_gecersiz_kilar(app):
    yon = _yonetici(app)
    _ekle(yon, 'deniz', sifre='deniz-sifre-1', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'deniz', 'deniz-sifre-1')
    r = _post(yon, '/yonetim/deniz/sifre', degistirmeli='1')
    yeni = _uretilen_sifre(r)
    assert yeni
    assert ist.get('/').status_code == 302
    assert _giris(app.test_client(), 'deniz', 'deniz-sifre-1').status_code == 401
    ist2 = app.test_client()
    assert _giris(ist2, 'deniz', yeni).status_code == 302
    assert ist2.get('/').headers['Location'].endswith('/sifre')


def test_kalici_silme(app):
    yon = _yonetici(app)
    _ekle(yon, 'gidecek', sifre='gidecek-sifre', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'gidecek', 'gidecek-sifre')
    _post(yon, '/yonetim/gidecek/sil')
    assert vt.bul('gidecek') is None
    assert ist.get('/').status_code == 302
    r = _post(yon, '/yonetim/gidecek/sil')
    assert 'bulunamadı' in yon.get('/yonetim/').get_data(as_text=True)
    assert r.status_code == 302
    assert [x['islem'] for x in vt.kayitlar()].count('silindi') == 1


def test_yonetim_formu_csrf_ister(app):
    yon = _yonetici(app)
    _ekle(yon, 'kalici', degistirmeli=False)
    r = yon.post('/yonetim/kalici/sil')
    assert r.status_code == 400
    assert vt.bul('kalici') is not None


# ---------------------------------------------------------------- deneme sınırı ve dayanıklılık

def test_deneme_siniri_veritabaninda_tutulur(app):
    for _ in range(5):
        _giris(app.test_client(), 'deneme', 'yanlis-sifre')
    giris._denemeler.clear()      # başka bir Vercel örneği gibi: bellek boş
    r = _giris(app.test_client(), 'deneme', TEST_SIFRE)
    assert r.status_code == 429


def test_veritabani_yokken_ortam_yoneticisi_girer(app, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'host=127.0.0.1 port=1 user=x dbname=x connect_timeout=1')
    vt._kapat()
    vt.onbellek_sil()
    ist = app.test_client()
    assert _giris(ist, 'deneme', TEST_SIFRE).status_code == 302
    sayfa = ist.get('/yonetim/').get_data(as_text=True)
    # Okunamayan liste "kullanıcı yok" diye gösterilmez.
    assert 'okunamadı' in sayfa or 'bağlanılamadı' in sayfa
    assert 'henüz kullanıcı yok' not in sayfa
    r = _giris(app.test_client(), 'baskasi', 'herhangi-sifre')
    assert r.status_code == 503


def test_veritabaninda_dogrudan_pasife_alinan_oturumu_da_duser(app):
    """Neon konsolundan elle pasife alma oturum sürümünü artırmaz; aktif denetimi yine düşürür."""
    import psycopg2
    yon = _yonetici(app)
    _ekle(yon, 'elle', sifre='elle-sifre-123', degistirmeli=False)
    ist = app.test_client()
    _giris(ist, 'elle', 'elle-sifre-123')
    assert ist.get('/').status_code == 200
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE kullanicilar SET aktif = FALSE WHERE ad = 'elle'")
    conn.close()
    vt.onbellek_sil()
    assert ist.get('/').status_code == 302
