"""Web katmanı: giriş, CSRF, sınırlar ve iki uç."""
import base64
import io
import json
import re

import pytest

from tests.conftest import TEST_SIFRE
from tests.test_motor import _metin


@pytest.fixture
def app():
    import app as uygulama
    import giris
    giris._denemeler.clear()
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


@pytest.fixture
def istemci(app):
    return app.test_client()


def _csrf(istemci, yol='/giris'):
    sayfa = istemci.get(yol).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', sayfa) or re.search(
        r'name="csrf-token" content="([^"]+)"', sayfa)
    return m.group(1)


def _giris(istemci, ad='deneme', sifre=TEST_SIFRE, next_=None):
    yol = '/giris' + (f'?next={next_}' if next_ else '')
    return istemci.post(yol, data={'kullanici': ad, 'sifre': sifre, 'csrf_token': _csrf(istemci)})


def _xhr(istemci):
    return {'X-CSRFToken': _csrf(istemci, '/'), 'X-Requested-With': 'XMLHttpRequest'}


def test_giris_gerekli(istemci):
    r = istemci.get('/')
    assert r.status_code == 302 and '/giris' in r.headers['Location']
    r = istemci.post('/api/analiz', headers={'X-Requested-With': 'XMLHttpRequest'})
    # Oturumsuz XHR: CSRF'ten önce oturum değil CSRF devreye girer; ikisi de reddeder.
    assert r.status_code in (400, 401)


def test_yanlis_ve_dogru_giris(istemci):
    r = _giris(istemci, sifre='yanlis')
    assert r.status_code == 401
    assert 'hatalı' in r.get_data(as_text=True)
    r = _giris(istemci, ad='yok')
    assert r.status_code == 401
    r = _giris(istemci)
    assert r.status_code == 302 and r.headers['Location'] == '/'
    sayfa = istemci.get('/').get_data(as_text=True)
    assert 'PDF seçin' in sayfa and 'editor.js' in sayfa


def test_editor_js_in_aradigi_her_kimlik_sayfada_var(istemci):
    """Sayfa yeniden tasarlanırken betiğin bağlandığı bir öğe düşmesin (1.8)."""
    import pathlib
    import re
    js = (pathlib.Path(__file__).parent.parent / 'public/static/editor.js').read_text()
    kimlikler = set(re.findall(r"\$\('#([\w-]+)'\)", js))
    assert {'birak', 'dosya', 'yukle', 'yukle-durum'} <= kimlikler
    _giris(istemci)
    sayfa = istemci.get('/').get_data(as_text=True)
    eksik = sorted(k for k in kimlikler if f'id="{k}"' not in sayfa)
    assert eksik == []


def test_kullanici_adi_buyuk_kucuk_harf_duyarsiz(istemci):
    assert _giris(istemci, ad='DENEME').status_code == 302


def test_acik_yonlendirme_yok(istemci):
    r = _giris(istemci, next_='//kotu.example.com/x')
    assert r.headers['Location'] == '/'
    istemci.post('/cikis', data={'csrf_token': _csrf(istemci, '/')})
    r = _giris(istemci, next_='https://kotu.example.com')
    assert r.headers['Location'] == '/'


def test_deneme_siniri(istemci):
    for _ in range(5):
        _giris(istemci, sifre='yanlis')
    r = _giris(istemci)   # doğru şifre de 15 dk reddedilir
    assert r.status_code == 429 and '15 dakika' in r.get_data(as_text=True)


def test_listeden_cikarilan_kullanicinin_oturumu_duser(istemci, monkeypatch):
    _giris(istemci)
    assert istemci.get('/').status_code == 200
    monkeypatch.setenv('KULLANICILAR', '')
    assert istemci.get('/').status_code == 302


def test_cikis(istemci):
    _giris(istemci)
    istemci.post('/cikis', data={'csrf_token': _csrf(istemci, '/')})
    assert istemci.get('/').status_code == 302


def test_csrf_olmadan_reddedilir(istemci, ornek):
    _giris(istemci)
    r = istemci.post('/api/analiz', headers={'X-Requested-With': 'XMLHttpRequest'},
                     data={'pdf': (io.BytesIO(ornek('chrome-fatura.pdf')), 'a.pdf')})
    assert r.status_code == 400 and 'süresi' in r.get_json()['hata']


def test_analiz_ve_uret(istemci, ornek):
    _giris(istemci)
    veri = ornek('chrome-fatura.pdf')
    r = istemci.post('/api/analiz', headers=_xhr(istemci),
                     data={'pdf': (io.BytesIO(veri), 'Fatura Ağustos.pdf')})
    assert r.status_code == 200
    parcalar = {p['t']: p for p in r.get_json()['sayfalar'][0]['parcalar']}
    p = parcalar['bağlantıya tıklayın']
    q = parcalar['Not: Ödeme vadesi 30 gündür.']
    duz = [{'id': p['id'], 'eski': p['t'], 'yeni': 'bağlantıya tıklayınız', 'hiza': 'sol',
            'font': 'otomatik', 'sigdir': False},
           {'id': q['id'], 'eski': q['t'], 'yeni': 'Not: Ödeme vadesi 45 gündür.', 'hiza': 'sol',
            'font': 'otomatik', 'sigdir': False}]
    r = istemci.post('/api/uret', headers=_xhr(istemci),
                     data={'pdf': (io.BytesIO(veri), 'Fatura Ağustos.pdf'), 'duzeltmeler': json.dumps(duz)})
    assert r.status_code == 200
    assert r.mimetype == 'application/pdf'
    assert "Fatura%20A%C4%9Fustos-duzenlenmis.pdf" in r.headers['Content-Disposition']
    uyarilar = json.loads(base64.b64decode(r.headers['X-Uyarilar']).decode('utf-8'))
    assert any('çakışıyor' in u for u in uyarilar)
    assert 'Not: Ödeme vadesi 45 gündür.' in _metin(r.data)
    assert r.headers['Cache-Control'] == 'no-store'


def test_motor_hatasi_json_doner(istemci):
    _giris(istemci)
    r = istemci.post('/api/analiz', headers=_xhr(istemci),
                     data={'pdf': (io.BytesIO(b'PDF degil'), 'x.pdf')})
    assert r.status_code == 400 and r.get_json()['hata']
    r = istemci.post('/api/uret', headers=_xhr(istemci),
                     data={'pdf': (io.BytesIO(b'%PDF'), 'x.pdf'), 'duzeltmeler': '{bozuk'})
    assert r.status_code == 400 and 'okunamadı' in r.get_json()['hata']


def test_buyuk_dosya_413(istemci, app):
    _giris(istemci)
    buyuk = b'%PDF-1.4\n' + b'0' * (app.config['MAX_CONTENT_LENGTH'] + 10)
    r = istemci.post('/api/analiz', headers=_xhr(istemci),
                     data={'pdf': (io.BytesIO(buyuk), 'b.pdf')})
    assert r.status_code == 413 and 'MB' in r.get_json()['hata']


def test_vercelde_secret_key_zorunlu(monkeypatch):
    import importlib
    import sys
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('SECRET_KEY', raising=False)
    eski = sys.modules.pop('app')
    try:
        with pytest.raises(RuntimeError, match='SECRET_KEY'):
            importlib.import_module('app')
    finally:
        sys.modules['app'] = eski


def test_boyut_sinirlari_vercelin_olculen_sinirinin_altinda():
    """Vercel sınırını aşan istek uygulamaya hiç ulaşmaz; kendi sınırımız ondan küçük olmalı,
    dosya sınırı da form zarfına ve değişiklik listesine pay bırakmalı."""
    from pdf_duzenle import rotalar
    assert rotalar.AZAMI_ISTEK < rotalar.VERCEL_OLCULEN_SINIR
    assert rotalar.AZAMI_DOSYA + 50_000 <= rotalar.AZAMI_ISTEK
    assert rotalar.AZAMI_YANIT < rotalar.VERCEL_OLCULEN_SINIR
    assert rotalar.mb(rotalar.AZAMI_DOSYA) == '4,4'


def test_uret_resim_ekler_form_alanlarindan(istemci, ornek):
    """Arayüzün gönderdiği biçim: `resimler` JSON + her resim `resim_<id>` dosya alanında."""
    import pymupdf as fitz
    from tests.test_motor import _dort_renk_png, _renkler
    _giris(istemci)
    veri = ornek('chrome-fatura.pdf')
    kutu = {'id': 'r1', 'sayfa': 1, 'x': 0.55, 'y': 0.05, 'w': 0.3, 'h': 0.15}
    r = istemci.post('/api/uret', headers=_xhr(istemci), data={
        'pdf': (io.BytesIO(veri), 'f.pdf'), 'duzeltmeler': '[]',
        'resimler': json.dumps([kutu]), 'resim_r1': (io.BytesIO(_dort_renk_png()), 'r1.png')})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert len(fitz.open(stream=r.data)[0].get_images()) > len(fitz.open(stream=veri)[0].get_images())
    assert _renkler(r.data, (0.55, 0.05, 0.3, 0.15)) == 'KYMS'
    # Dosyası gelmeyen resim ve bozuk kimlik reddedilir.
    for alanlar in ({'resimler': json.dumps([kutu])},
                    {'resimler': json.dumps([dict(kutu, id='../x')]), 'resim_../x': (io.BytesIO(b'x'), 'x.png')}):
        r = istemci.post('/api/uret', headers=_xhr(istemci), data={
            'pdf': (io.BytesIO(veri), 'f.pdf'), 'duzeltmeler': '[]', **alanlar})
        assert r.status_code == 400
