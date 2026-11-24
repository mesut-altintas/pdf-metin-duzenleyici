"""E-postayla kayıt, şifremi unuttum, e-postayla giriş (kayit.py, eposta.py). Yerel PostgreSQL ister.
Gerçek e-posta gönderilmez: `eposta._ilet` kaydediciyle değiştirilir."""
import re
import urllib.error
from urllib.parse import urlsplit

import pytest
from werkzeug.security import generate_password_hash

import eposta
import giris
import kayit
import kullanicilar as vt
from tests.conftest import TEST_SIFRE

ADRES = 'https://ornek.test'
SIFRE = 'yeni-sifre-123'


@pytest.fixture
def giden(monkeypatch):
    liste = []

    def kaydet(govde):
        liste.append(govde)
        return 200
    monkeypatch.setattr(eposta, '_ilet', kaydet)
    return liste


@pytest.fixture
def app(vt_db, monkeypatch, giden):
    import app as uygulama
    giris._denemeler.clear()
    monkeypatch.setenv('RESEND_API_KEY', 'test-anahtari')
    monkeypatch.setenv('EPOSTA_GONDEREN', 'PDF Metin Düzenleyici <pdf@altintas.biz>')
    monkeypatch.setenv('UYGULAMA_ADRESI', ADRES)
    monkeypatch.setenv('KAYIT_ACIK', '1')
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _csrf(ist, yol='/giris'):
    sayfa = ist.get(yol).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', sayfa).group(1)


def _post(ist, yol, **veri):
    veri['csrf_token'] = _csrf(ist, '/giris')
    return ist.post(yol, data=veri)


def _baglanti(govde):
    """E-postadaki bağlantının yolu (alan adı UYGULAMA_ADRESI olmalı)."""
    m = re.search(r'(https?://\S+)', govde['text'])
    url = urlsplit(m.group(1))
    assert f'{url.scheme}://{url.netloc}' == ADRES
    return url.path + ('?' + url.query if url.query else '')


def _kaydol(app, giden, adres='Ayse.Yilmaz@Ornek.com', ad='Ayşe Yılmaz'):
    ist = app.test_client()
    r = _post(ist, '/kayit', eposta=adres)
    assert r.status_code == 200 and 'bağlantı gönderdik' in r.get_data(as_text=True)
    yol = _baglanti(giden[-1])
    assert ist.get(yol).status_code == 200
    t = re.search(r't=([^&]+)', yol).group(1)
    r = _post(ist, '/kayit/tamamla', t=t, gorunen_ad=ad, sifre=SIFRE, tekrar=SIFRE, kosul='1')
    return ist, r


def _giris(app, kim, sifre):
    ist = app.test_client()
    return ist, _post(ist, '/giris', kullanici=kim, sifre=sifre)


# ---------------------------------------------------------------- açık / kapalı

def test_kayit_kapaliyken_404_ve_baglanti_cizilmez(app, monkeypatch):
    monkeypatch.delenv('KAYIT_ACIK')
    ist = app.test_client()
    assert ist.get('/kayit').status_code == 404
    assert ist.get('/kayit/tamamla?t=x').status_code == 404
    sayfa = ist.get('/giris').get_data(as_text=True)
    assert 'Hesap oluştur' not in sayfa and 'Şifremi unuttum' in sayfa
    monkeypatch.delenv('RESEND_API_KEY')         # e-posta tanımsız: şifremi unuttum da kapanır
    assert ist.get('/sifremi-unuttum').status_code == 404
    assert 'Şifremi unuttum' not in ist.get('/giris').get_data(as_text=True)


def test_yasal_metinler_girissiz_acilir(app):
    ist = app.test_client()
    for yol, metin in (('/kosullar', 'Kullanım koşulları'), ('/gizlilik', 'aydınlatma metni')):
        r = ist.get(yol)
        assert r.status_code == 200 and metin in r.get_data(as_text=True) and 'TASLAK' in r.get_data(as_text=True)


# ---------------------------------------------------------------- kayıt

def test_kayit_uctan_uca_ucretsiz_hesap_ve_eposta_ile_giris(app, giden):
    ist, r = _kaydol(app, giden)
    assert r.status_code == 302 and r.headers['Location'] == '/'
    assert giden[0]['to'] == ['ayse.yilmaz@ornek.com'] and 'hesabınızı oluşturun' in giden[0]['subject']
    sayfa = ist.get('/').get_data(as_text=True)
    assert 'Hesabınız açıldı' in sayfa and 'bu ay <strong id="kota-kullanilan">0</strong> / 3' in sayfa
    k = vt.bul_eposta('ayse.yilmaz@ornek.com')
    assert k['ad'] == 'ayse.yilmaz' and k['eposta_dogrulandi'] and k['kosul_surum'] == kayit.KOSUL_SURUMU
    assert k['olusturan'] == vt.KAYIT_YAPAN and not k['sifre_degistirmeli'] and not k['yonetici']
    assert 'kaydoldu, plan: Ücretsiz' in [r['islem'] for r in vt.kayitlar()]
    for kim in ('ayse.yilmaz@ornek.com', 'AYSE.YILMAZ@ORNEK.COM', 'ayse.yilmaz'):
        _, r = _giris(app, kim, SIFRE)
        assert r.status_code == 302, kim


def test_ayni_baglanti_ikinci_kez_hesap_acmaz(app, giden):
    ist, _ = _kaydol(app, giden)
    t = re.search(r't=([^&\s]+)', giden[0]['text']).group(1)
    ikinci = app.test_client()
    assert 'Hesap zaten açık' in ikinci.get(f'/kayit/tamamla?t={t}').get_data(as_text=True)
    r = _post(ikinci, '/kayit/tamamla', t=t, gorunen_ad='Başkası', sifre='baska-sifre-1', tekrar='baska-sifre-1',
              kosul='1')
    assert 'Hesap zaten açık' in r.get_data(as_text=True)
    assert vt.sorgu('SELECT count(*) AS n FROM kullanicilar')[0]['n'] == 1


def test_kayitli_adres_icin_ekranda_ayni_cevap_epostada_zaten_var(app, giden):
    _kaydol(app, giden)
    ist = app.test_client()
    yeni = _post(ist, '/kayit', eposta='yeni@ornek.com').get_data(as_text=True)
    var = _post(ist, '/kayit', eposta='ayse.yilmaz@ornek.com').get_data(as_text=True)
    temizle = lambda s: re.sub(r'name="csrf-token" content="[^"]+"', '', s)   # noqa: E731
    assert temizle(yeni.replace('yeni@ornek.com', 'X')) == temizle(var.replace('ayse.yilmaz@ornek.com', 'X'))
    son = giden[-1]
    assert 'hesabınız var' in son['subject'] and '/kayit/tamamla' not in son['text']
    assert '/sifremi-unuttum' in son['text']


def test_tamamlama_kurallari_kosul_sifre_ad(app, giden):
    ist = app.test_client()
    _post(ist, '/kayit', eposta='ali@ornek.com')
    t = re.search(r't=([^&\s]+)', giden[0]['text']).group(1)
    durumlar = [
        ({'gorunen_ad': 'Ali', 'sifre': SIFRE, 'tekrar': SIFRE}, 'koşullarını kabul'),
        ({'gorunen_ad': 'Ali', 'sifre': 'kisa', 'tekrar': 'kisa', 'kosul': '1'}, 'en az'),
        ({'gorunen_ad': 'Ali', 'sifre': SIFRE, 'tekrar': SIFRE + 'x', 'kosul': '1'}, 'aynı değil'),
        ({'gorunen_ad': '  ', 'sifre': SIFRE, 'tekrar': SIFRE, 'kosul': '1'}, 'Adınızı'),
    ]
    for veri, beklenen in durumlar:
        r = _post(ist, '/kayit/tamamla', t=t, **veri)
        assert r.status_code == 400 and beklenen in r.get_data(as_text=True), beklenen
    assert vt.sorgu('SELECT count(*) AS n FROM kullanicilar')[0]['n'] == 0


def test_bozuk_ve_suresi_dolmus_baglanti(app, giden, monkeypatch):
    ist = app.test_client()
    _post(ist, '/kayit', eposta='ali@ornek.com')
    t = re.search(r't=([^&\s]+)', giden[0]['text']).group(1)
    assert 'geçersiz' in ist.get(f'/kayit/tamamla?t={t}x').get_data(as_text=True)
    monkeypatch.setattr(kayit, 'KAYIT_SURESI', -1)
    r = ist.get(f'/kayit/tamamla?t={t}')
    assert r.status_code == 400 and 'süresi dolmuş' in r.get_data(as_text=True)


def test_baglanti_host_basligindan_kurulmaz(app, giden):
    ist = app.test_client()
    kotu = 'http://kotu.example.com'      # istek bu adrese geliyormuş gibi (Host başlığı)
    sayfa = ist.get('/giris', base_url=kotu).get_data(as_text=True)
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', sayfa).group(1)
    r = ist.post('/kayit', data={'eposta': 'ali@ornek.com', 'csrf_token': csrf}, base_url=kotu)
    assert r.status_code == 200
    assert giden and 'kotu.example.com' not in giden[-1]['text']
    _baglanti(giden[-1])


def test_bot_alani_doluysa_gonderilmez_ekranda_ayni(app, giden):
    r = _post(app.test_client(), '/kayit', eposta='ali@ornek.com', web_sitesi='http://spam')
    assert r.status_code == 200 and 'bağlantı gönderdik' in r.get_data(as_text=True)
    assert giden == []


def test_istek_siniri_ve_tabloda_acik_adres_yok(app, giden):
    ist = app.test_client()
    for _ in range(kayit.EPOSTA_SINIRI):
        assert _post(ist, '/kayit', eposta='ali@ornek.com').status_code == 200
    r = _post(ist, '/kayit', eposta='ali@ornek.com')
    assert r.status_code == 429 and len(giden) == kayit.EPOSTA_SINIRI
    _giris(app, 'ali@ornek.com', 'yanlis-sifre')
    anahtarlar = ' '.join(r['anahtar'] for r in vt.sorgu('SELECT anahtar FROM giris_denemesi'))
    assert 'ali@ornek.com' not in anahtarlar and 'ornek.com' not in anahtarlar


def test_eposta_gonderilemezse_503_ve_hesap_yok(app, monkeypatch):
    def bozuk(govde):
        raise urllib.error.URLError('bağlantı yok')
    monkeypatch.setattr(eposta, '_ilet', bozuk)
    r = _post(app.test_client(), '/kayit', eposta='ali@ornek.com')
    assert r.status_code == 503 and 'gönderilemedi' in r.get_data(as_text=True)


# ---------------------------------------------------------------- şifremi unuttum

def test_sifremi_unuttum_uctan_uca_ve_baglanti_tek_kullanimlik(app, giden):
    _kaydol(app, giden)
    ist = app.test_client()
    r = _post(ist, '/sifremi-unuttum', eposta='ayse.yilmaz@ornek.com')
    assert r.status_code == 200 and 'kayıtlıysa' in r.get_data(as_text=True)
    yol = _baglanti(giden[-1])
    assert yol.startswith('/sifre-yenile?t=') and 'şifre yenileme' in giden[-1]['subject']
    t = yol.split('t=')[1]
    r = _post(ist, '/sifre-yenile', t=t, sifre='en-yeni-sifre-9', tekrar='en-yeni-sifre-9')
    assert r.status_code == 302 and r.headers['Location'] == '/giris'
    assert _giris(app, 'ayse.yilmaz@ornek.com', 'en-yeni-sifre-9')[1].status_code == 302
    assert _giris(app, 'ayse.yilmaz@ornek.com', SIFRE)[1].status_code == 401
    r = _post(ist, '/sifre-yenile', t=t, sifre='ucuncu-sifre-1', tekrar='ucuncu-sifre-1')
    assert r.status_code == 400 and 'kullanılmış' in r.get_data(as_text=True)


def test_sifre_yenilenince_acik_oturum_duser(app, giden):
    ist, _ = _kaydol(app, giden)
    assert ist.get('/').status_code == 200
    _post(app.test_client(), '/sifremi-unuttum', eposta='ayse.yilmaz@ornek.com')
    t = _baglanti(giden[-1]).split('t=')[1]
    _post(app.test_client(), '/sifre-yenile', t=t, sifre='en-yeni-sifre-9', tekrar='en-yeni-sifre-9')
    vt.onbellek_sil()
    assert ist.get('/').status_code == 302


def test_sifre_yenile_yazimi_eski_surumu_ve_baska_adresi_reddeder(vt_db):
    """Ekrandaki ön denetimden bağımsız: aynı bağlantıyla eşzamanlı iki gönderimde
    ikincisini koşullu UPDATE durdurur."""
    vt.ekle('veli', '', 'x', False, False, 'deneme', eposta='veli@ornek.com')
    surum = vt.bul('veli')['oturum_surum']
    assert not vt.sifre_yenile('veli', 'baska@ornek.com', surum, 'y')
    assert vt.sifre_yenile('veli', 'veli@ornek.com', surum, 'y')
    assert not vt.sifre_yenile('veli', 'veli@ornek.com', surum, 'z')
    assert vt.bul('veli')['sifre_ozeti'] == 'y'


def test_kayitsiz_adres_ayni_cevap_eposta_yok(app, giden):
    r = _post(app.test_client(), '/sifremi-unuttum', eposta='kimse@ornek.com')
    assert r.status_code == 200 and 'kayıtlıysa' in r.get_data(as_text=True)
    assert giden == []


def test_yonetici_adresi_degistirince_eski_baglanti_gecersiz(app, giden):
    vt.ekle('veli', '', generate_password_hash('veli-sifre-123'), False, False, 'deneme', eposta='veli@ornek.com')
    _post(app.test_client(), '/sifremi-unuttum', eposta='veli@ornek.com')
    t = _baglanti(giden[-1]).split('t=')[1]
    vt.eposta_ata('veli', 'veli@baska.com', 'deneme')
    r = app.test_client().get(f'/sifre-yenile?t={t}')
    assert r.status_code == 400 and 'kullanılmış' in r.get_data(as_text=True)


# ---------------------------------------------------------------- yönetim

def test_yonetici_eposta_atar_cakismayi_reddeder_kullanici_eposta_ile_girer(app, giden):
    yon, _ = _giris(app, 'deneme', TEST_SIFRE)
    r = _post(yon, '/yonetim/ekle', ad='zeynep', sifre='zeynep-sifre-1', gorunen_ad='',
              eposta='Zeynep@Ornek.com')
    assert r.status_code == 200 and 'Kullanıcı eklendi' in r.get_data(as_text=True)
    assert vt.bul('zeynep')['eposta'] == 'zeynep@ornek.com'
    assert _giris(app, 'zeynep@ornek.com', 'zeynep-sifre-1')[1].status_code == 302

    vt.ekle('kerem', '', generate_password_hash('kerem-sifre-1'), False, False, 'deneme')
    sayfa = yon.get('/yonetim/').get_data(as_text=True)
    eylem = re.search(r'action="(/yonetim/kerem/eposta)"', sayfa).group(1)
    yon.post(eylem, data={'eposta': 'zeynep@ornek.com', 'csrf_token': _csrf(yon, '/yonetim/')})
    assert 'başka bir kullanıcıda' in yon.get('/yonetim/').get_data(as_text=True)
    yon.post(eylem, data={'eposta': 'kerem@ornek.com', 'csrf_token': _csrf(yon, '/yonetim/')})
    assert vt.bul('kerem')['eposta'] == 'kerem@ornek.com' and vt.bul('kerem')['eposta_dogrulandi'] is None
    r = _post(yon, '/yonetim/ekle', ad='kerem2', sifre='kerem-sifre-1', gorunen_ad='', eposta='kerem@ornek.com')
    assert r.status_code == 400 and 'başka bir kullanıcıda' in r.get_data(as_text=True)
    assert vt.bul('kerem2') is None
