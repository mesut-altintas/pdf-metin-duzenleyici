"""Yüklenebilir uygulama (PWA) dosyaları ve şablon ile betik arasındaki bağ."""
import json
import re
from pathlib import Path

import pytest

from tests.conftest import KOK, TEST_SIFRE

PUBLIC = KOK / 'public'


@pytest.fixture
def istemci():
    import app as uygulama
    import giris
    giris._denemeler.clear()
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return uygulama.app.test_client()


def _dosya(url_yolu):
    """'/static/x' ya da '/x' → public altındaki dosya."""
    return PUBLIC / url_yolu.lstrip('/')


def test_manifest_gecerli_ve_simgeleri_var(istemci):
    r = istemci.get('/manifest.webmanifest')
    assert r.status_code == 200
    assert r.mimetype == 'application/manifest+json'
    m = json.loads(r.data)
    assert m['start_url'] == '/' and m['display'] == 'standalone'
    boyutlar = {(i['sizes'], i['purpose']) for i in m['icons']}
    assert ('192x192', 'any') in boyutlar and ('512x512', 'any') in boyutlar
    assert ('512x512', 'maskable') in boyutlar
    for i in m['icons']:
        assert _dosya(i['src']).is_file(), i['src']


def test_servis_calisani_on_yukleme_dosyalari_var(istemci):
    r = istemci.get('/sw.js')
    assert r.status_code == 200 and r.mimetype == 'text/javascript'
    metin = r.get_data(as_text=True)
    liste = re.search(r'ON_YUKLEME = \[(.*?)\];', metin, re.S).group(1)
    yollar = re.findall(r"'([^']+)'", liste)
    assert yollar
    for y in yollar:
        assert _dosya(y).is_file(), y          # biri eksikse kurulum düşer


def test_servis_calisani_kullanici_verisini_onbellege_almaz():
    metin = (PUBLIC / 'sw.js').read_text(encoding='utf-8')
    # cache.put yalnız /static/ dalında.
    put = metin.index('c.put(')
    assert metin.rfind("startsWith('/static/')", 0, put) != -1
    assert metin.count('c.put(') == 1


def test_kok_dosyalari_girissiz_acilir(istemci):
    for yol in ('/favicon.ico', '/cevrimdisi.html', '/sw.js', '/manifest.webmanifest'):
        assert istemci.get(yol).status_code == 200, yol


def test_giris_sayfasinda_simge_ve_pwa_etiketleri(istemci):
    sayfa = istemci.get('/giris').get_data(as_text=True)
    assert 'simge/simge.svg' in sayfa
    assert 'rel="manifest" href="/manifest.webmanifest"' in sayfa
    assert 'apple-touch-icon' in sayfa and 'pwa.js' in sayfa
    for yol in re.findall(r'(?:src|href)="(/static/[^"]+)"', sayfa):
        assert _dosya(yol).is_file(), yol


def _kimlikler(js_yolu):
    metin = Path(js_yolu).read_text(encoding='utf-8')
    return set(re.findall(r"\$\('#([\w-]+)'\)", metin)) | set(re.findall(r"getElementById\('([\w-]+)'\)", metin))


# Yalnız bazı kullanıcılarda çizilen öğeler (betik yokluğunu denetler). Her biri,
# çizildiği durumda ayrıca sınanır: kota-kullanilan → test_kota.py (kotalı kullanıcı).
ISTEGE_BAGLI = {'kota-kullanilan'}


def test_betiklerin_aradigi_ogeler_sablonda_var(istemci):
    """Şablonda adı değişen bir öğe betikte sessizce null olmasın."""
    istemci.post('/giris', data={'kullanici': 'deneme', 'sifre': TEST_SIFRE})
    sayfa = istemci.get('/').get_data(as_text=True)
    var = set(re.findall(r'id="([\w-]+)"', sayfa))
    for js in ('editor.js', 'pwa.js'):
        eksik = _kimlikler(PUBLIC / 'static' / js) - var - ISTEGE_BAGLI
        assert not eksik, (js, eksik)
    assert ISTEGE_BAGLI <= _kimlikler(PUBLIC / 'static' / 'editor.js')   # liste bayatlamasın


def test_surum_ve_telif_alt_bilgide(istemci):
    from surum import SURUM, TELIF
    for yol in ('/giris',):
        sayfa = istemci.get(yol).get_data(as_text=True)
        assert f'Sürüm {SURUM}' in sayfa and TELIF in sayfa and 'Mesut Altıntaş' in sayfa
    istemci.post('/giris', data={'kullanici': 'deneme', 'sifre': TEST_SIFRE})
    assert f'Sürüm {SURUM}' in istemci.get('/').get_data(as_text=True)


def test_her_sayfada_calisan_surumun_kaynak_baglantisi(istemci):
    """AGPL-3.0: ağ üzerinden kullanana çalışan sürümün kaynağı sunulur (surum.py)."""
    from surum import KAYNAK_DEPO, SURUM
    baglanti = f'href="{KAYNAK_DEPO}/tree/v{SURUM}"'
    assert KAYNAK_DEPO.startswith('https://github.com/')
    for yol in ('/giris', '/kosullar', '/gizlilik'):
        sayfa = istemci.get(yol).get_data(as_text=True)
        assert baglanti in sayfa and 'Kaynak kod (AGPL-3.0)' in sayfa, yol
    istemci.post('/giris', data={'kullanici': 'deneme', 'sifre': TEST_SIFRE})
    assert baglanti in istemci.get('/').get_data(as_text=True)
    kok = Path(__file__).resolve().parent.parent
    assert 'AGPL-3.0' in (kok / 'README.md').read_text(encoding='utf-8')
