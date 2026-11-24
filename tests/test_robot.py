"""'Ben robot değilim' (robot.py): Google'a gidilmez, `robot._sor` değiştirilir.
Form testleri yerel PostgreSQL ister (kayıt veritabanı gerektirir)."""
import re
import urllib.error

import pytest
from werkzeug.security import generate_password_hash

import eposta
import giris
import kullanicilar as vt
import robot

SITE = 'site-anahtari-ornek'
GIZLI = 'gizli-anahtar-ornek'


@pytest.fixture
def anahtarlar(monkeypatch):
    monkeypatch.setenv('RECAPTCHA_SITE_KEY', SITE)
    monkeypatch.setenv('RECAPTCHA_SECRET', GIZLI)


@pytest.fixture
def google(monkeypatch):
    """Google'ın cevabını sınama belirler; gönderilen istekler kaydedilir."""
    durum = {'cevap': {'success': True}, 'istekler': []}

    def sor(veri):
        durum['istekler'].append(dict(veri))
        if isinstance(durum['cevap'], Exception):
            raise durum['cevap']
        return durum['cevap']
    monkeypatch.setattr(robot, '_sor', sor)
    return durum


# ---------------------------------------------------------------- doğrulama

def test_anahtar_yoksa_ya_da_kapaliysa_etkin_degil(monkeypatch):
    assert robot.site_anahtari() is None
    monkeypatch.setenv('RECAPTCHA_SITE_KEY', SITE)
    assert robot.site_anahtari() is None              # gizli anahtar da gerekir
    monkeypatch.setenv('RECAPTCHA_SECRET', GIZLI)
    assert robot.site_anahtari() == SITE
    monkeypatch.setenv('ROBOT_DOGRULAMA_KAPALI', '1')
    assert robot.site_anahtari() is None


def test_dogrulama_sonuclari(anahtarlar, google):
    assert robot.dogrula('') == robot.GECMEDI and google['istekler'] == []
    assert robot.dogrula('jeton', '1.2.3.4') == robot.GECTI
    assert google['istekler'][-1] == {'secret': GIZLI, 'response': 'jeton', 'remoteip': '1.2.3.4'}
    for kod in ('missing-input-response', 'invalid-input-response', 'timeout-or-duplicate'):
        google['cevap'] = {'success': False, 'error-codes': [kod]}
        assert robot.dogrula('jeton') == robot.GECMEDI, kod
    google['cevap'] = {'success': False}
    assert robot.dogrula('jeton') == robot.GECMEDI
    # Bizim yapılandırmamız ya da Google'ın kesintisi: kullanıcı durdurulmaz.
    google['cevap'] = {'success': False, 'error-codes': ['invalid-input-secret']}
    assert robot.dogrula('jeton') == robot.ULASILAMADI
    google['cevap'] = urllib.error.URLError('yok')
    assert robot.dogrula('jeton') == robot.ULASILAMADI


# ---------------------------------------------------------------- formlar

@pytest.fixture
def giden(monkeypatch):
    liste = []
    monkeypatch.setattr(eposta, '_ilet', lambda govde: liste.append(govde) or 200)
    return liste


@pytest.fixture
def app(vt_db, monkeypatch, giden):
    import app as uygulama
    giris._denemeler.clear()
    monkeypatch.setenv('RESEND_API_KEY', 'test-anahtari')
    monkeypatch.setenv('EPOSTA_GONDEREN', 'PDF Metin Düzenleyici <pdf@altintas.biz>')
    monkeypatch.setenv('UYGULAMA_ADRESI', 'https://ornek.test')
    monkeypatch.setenv('KAYIT_ACIK', '1')
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _post(ist, yol, **veri):
    sayfa = ist.get(yol).get_data(as_text=True)
    veri['csrf_token'] = re.search(r'name="csrf-token" content="([^"]+)"', sayfa).group(1)
    return ist.post(yol, data=veri)


@pytest.mark.parametrize('yol', ['/kayit', '/sifremi-unuttum'])
def test_kutu_isaretlenmeden_eposta_gitmez(app, anahtarlar, google, giden, yol):
    vt.ekle('ali', '', generate_password_hash('ali-sifre-123'), False, False, 'deneme', eposta='ali@ornek.com')
    ist = app.test_client()
    sayfa = ist.get(yol).get_data(as_text=True)
    assert f'data-sitekey="{SITE}"' in sayfa and 'recaptcha/api.js' in sayfa and GIZLI not in sayfa
    r = _post(ist, yol, eposta='ali@ornek.com')                     # jeton yok
    assert r.status_code == 400 and kayit_hatasi() in r.get_data(as_text=True)
    google['cevap'] = {'success': False, 'error-codes': ['invalid-input-response']}
    r = _post(ist, yol, eposta='ali@ornek.com', **{'g-recaptcha-response': 'sahte'})
    assert r.status_code == 400 and giden == []
    assert vt.sorgu("SELECT count(*) AS n FROM giris_denemesi WHERE anahtar LIKE 'eposta%%'")[0]['n'] == 0
    google['cevap'] = {'success': True}
    r = _post(ist, yol, eposta='ali@ornek.com', **{'g-recaptcha-response': 'gercek'})
    assert r.status_code == 200 and len(giden) == 1


def test_google_ulasilamazsa_istek_islenir(app, anahtarlar, google, giden):
    google['cevap'] = urllib.error.URLError('kesinti')
    r = _post(app.test_client(), '/kayit', eposta='yeni@ornek.com', **{'g-recaptcha-response': 'jeton'})
    assert r.status_code == 200 and len(giden) == 1


def test_anahtar_yokken_kutu_cizilmez_google_a_gidilmez(app, giden):
    ist = app.test_client()
    assert 'g-recaptcha' not in ist.get('/kayit').get_data(as_text=True)
    r = _post(ist, '/kayit', eposta='yeni@ornek.com')                # conftest: _sor çağrılırsa düşer
    assert r.status_code == 200 and len(giden) == 1


def kayit_hatasi():
    import kayit
    return kayit.ROBOT_HATASI
