"""Kullanım ölçümü (yerel PostgreSQL) ve sürüm alt bilgisi."""
import io
import json
import os
import re
import time

import psycopg2
import pytest

import giris
import kullanim
import kullanicilar as vt
from tests.conftest import TEST_SIFRE

GIZLI_AD = 'Ahmet Yılmaz Maaş Bordrosu.pdf'


@pytest.fixture
def app(vt_db):
    import app as uygulama
    giris._denemeler.clear()
    kullanim._son_hata = 0.0
    uygulama.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    return uygulama.app


def _csrf(ist, yol='/giris'):
    return re.search(r'name="csrf-token" content="([^"]+)"', ist.get(yol).get_data(as_text=True)).group(1)


def _giris(app, ad='deneme', sifre=TEST_SIFRE):
    ist = app.test_client()
    ist.post('/giris', data={'kullanici': ad, 'sifre': sifre, 'csrf_token': _csrf(ist)})
    return ist


def _xhr(ist):
    return {'X-CSRFToken': _csrf(ist, '/'), 'X-Requested-With': 'XMLHttpRequest'}


def _olaylar():
    """Uygulamanın okuma yolundan (şemayı o kurar)."""
    return [dict(r) for r in vt.sorgu(
        'SELECT kullanici, tur, basarili, hata, sayfa, boyut, duzeltme, ekleme, uyari, sure_ms, belge '
        'FROM kullanim_olayi ORDER BY id')]


def _analiz(ist, veri, ad=GIZLI_AD):
    return ist.post('/api/analiz', headers=_xhr(ist), data={'pdf': (io.BytesIO(veri), ad)})


def test_giris_acma_ve_uretme_sayilir(app, ornek):
    ist = _giris(app)
    veri = ornek('chrome-fatura.pdf')
    r = _analiz(ist, veri)
    p = {x['t']: x for x in r.get_json()['sayfalar'][0]['parcalar']}['Genel toplam']
    duz = [{'id': p['id'], 'eski': p['t'], 'yeni': 'Genel tutar'}]
    r = ist.post('/api/uret', headers=_xhr(ist),
                 data={'pdf': (io.BytesIO(veri), GIZLI_AD), 'duzeltmeler': json.dumps(duz)})
    assert r.status_code == 200
    olaylar = _olaylar()
    assert [o['tur'] for o in olaylar] == ['giris', 'acildi', 'uretildi']
    acildi, uretildi = olaylar[1], olaylar[2]
    assert acildi['kullanici'] == 'deneme' and acildi['basarili']
    assert acildi['sayfa'] == 1 and acildi['boyut'] == len(veri)
    assert acildi['belge'] == kullanim.parmak_izi(veri) and len(acildi['belge']) == 16
    assert acildi['sure_ms'] is not None and acildi['sure_ms'] >= 0
    assert uretildi['duzeltme'] == 1 and uretildi['uyari'] == 0 and uretildi['belge'] == acildi['belge']


def test_dosya_adi_ve_hata_metni_kaydedilmez(app, ornek):
    """Hata metni belgeden alıntı içerir (“… metni dosyada bulunamadı”); kayda yalnız kod gider."""
    ist = _giris(app)
    veri = ornek('chrome-fatura.pdf')
    p = _analiz(ist, veri).get_json()['sayfalar'][0]['parcalar'][0]
    duz = [{'id': p['id'], 'eski': 'Örnek Şirketi gizli metin', 'yeni': 'x'}]
    r = ist.post('/api/uret', headers=_xhr(ist),
                 data={'pdf': (io.BytesIO(veri), GIZLI_AD), 'duzeltmeler': json.dumps(duz)})
    assert r.status_code == 400 and 'gizli metin' in r.get_json()['hata']
    hata = _olaylar()[-1]
    assert hata['tur'] == 'uretildi' and not hata['basarili'] and hata['hata'] == 'eslesmedi'
    hepsi = json.dumps(_olaylar(), default=str)
    for parca in ('Ahmet', 'Bordro', 'gizli metin', 'Örnek', 'SATIŞ'):
        assert parca not in hepsi, parca
    # Tabloda metin taşıyabilecek bir sütun yok.
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'kullanim_olayi'")
        kolonlar = {r[0] for r in cur.fetchall()}
    conn.close()
    assert kolonlar == {'id', 'zaman', 'kullanici', 'tur', 'basarili', 'hata', 'sayfa', 'boyut',
                        'duzeltme', 'ekleme', 'uyari', 'sure_ms', 'belge'}


def test_istemci_olaylari_ve_dogrulama(app):
    ist = _giris(app)
    h = {**_xhr(ist), 'Content-Type': 'application/json'}
    for tur, ek in (('indirildi', {'duzeltme': 3}), ('paylasildi', {'duzeltme': True}),
                    ('buyuk_dosya', {'boyut': 9_000_000})):
        assert ist.post('/api/olay', headers=h, data=json.dumps({'tur': tur, **ek})).status_code == 204
    assert ist.post('/api/olay', headers=h, data=json.dumps({'tur': 'acildi'})).status_code == 400
    assert ist.post('/api/olay', headers=h, data=json.dumps({'tur': 'giris'})).status_code == 400
    o = _olaylar()[1:]
    assert [(x['tur'], x['duzeltme'], x['boyut'], x['basarili'], x['hata']) for x in o] == [
        ('indirildi', 3, None, True, None),
        ('paylasildi', None, None, True, None),        # True bir sayı değil
        ('buyuk_dosya', None, 9_000_000, False, 'boyut'),
    ]


def test_olay_ucu_giris_ve_csrf_ister(app):
    ist = app.test_client()
    r = ist.post('/api/olay', headers={'X-Requested-With': 'XMLHttpRequest'}, json={'tur': 'indirildi'})
    assert r.status_code in (400, 401)
    assert _olaylar() == []


def test_flaskin_413_yaniti_boyut_olarak_sayilir(app):
    ist = _giris(app)
    buyuk = b'%PDF' + b'0' * (app.config['MAX_CONTENT_LENGTH'] + 10)
    r = ist.post('/api/analiz', headers=_xhr(ist), data={'pdf': (io.BytesIO(buyuk), 'b.pdf')})
    assert r.status_code == 413
    son = _olaylar()[-1]
    assert (son['tur'], son['basarili'], son['hata']) == ('acildi', False, 'boyut')
    assert son['boyut'] > app.config['MAX_CONTENT_LENGTH']


def test_kullanim_sayfasi_ozet_ve_yetki(app, ornek):
    ist = _giris(app)
    veri = ornek('chrome-fatura.pdf')
    _analiz(ist, veri)
    _analiz(ist, veri)                                   # aynı dosya iki kez: farklı dosya 1
    _analiz(ist, ornek('reportlab-teklif.pdf'))
    sayfa = ist.get('/yonetim/kullanim?gun=7').get_data(as_text=True)
    assert 'Açılan PDF' in sayfa and 'PDF açtı' in sayfa
    deger = dict(re.findall(r'<span class="deger">(\d+)</span><span class="etiket">([^<]+)</span>', sayfa)[i][::-1]
                 for i in range(7))
    assert deger['Açılan PDF'] == '3' and deger['Farklı dosya'] == '2'
    assert 'Ahmet' not in sayfa
    # Geçersiz dönem 30 güne düşer.
    assert 'Son 30 gün' in ist.get('/yonetim/kullanim?gun=abc').get_data(as_text=True)

    yon = _giris(app)
    _post = {'ad': 'normal', 'sifre': 'normal-sifre-1', 'csrf_token': _csrf(yon, '/yonetim/')}
    yon.post('/yonetim/ekle', data=_post)
    normal = _giris(app, 'normal', 'normal-sifre-1')
    assert normal.get('/yonetim/kullanim').status_code == 403


def test_veritabani_yokken_kayit_beklemez(app, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'host=127.0.0.1 port=1 user=x dbname=x connect_timeout=1')
    vt._kapat()
    vt.onbellek_sil()
    bas = time.monotonic()
    assert kullanim.kaydet('deneme', 'acildi') is False
    assert time.monotonic() - bas < 5
    denemeler = []
    gercek = vt.yazma
    monkeypatch.setattr(vt, 'yazma', lambda *a, **k: denemeler.append(1) or gercek(*a, **k))
    assert kullanim.kaydet('deneme', 'acildi') is False      # bekleme süresinde denemez
    assert denemeler == []


def test_veritabani_tanimsizken_kayit_yapilmaz(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert kullanim.kaydet('deneme', 'acildi') is False


def test_liste_olaylari_sayilir(app):
    ist = _giris(app)
    h = {**_xhr(ist), 'Content-Type': 'application/json'}
    for tur in ('liste_kaydedildi', 'liste_yuklendi', 'toplu_degistirildi'):
        assert ist.post('/api/olay', headers=h, data=json.dumps({'tur': tur, 'duzeltme': 4})).status_code == 204
    assert [(o['tur'], o['duzeltme']) for o in _olaylar()[1:]] == [
        ('liste_kaydedildi', 4), ('liste_yuklendi', 4), ('toplu_degistirildi', 4)]
    assert 'Toplu değiştirdi' in ist.get('/yonetim/kullanim').get_data(as_text=True)
    assert 'Değişiklik listesi yükledi' in ist.get('/yonetim/kullanim').get_data(as_text=True)


def test_ekleme_sayilir(app, ornek):
    ist = _giris(app)
    veri = ornek('chrome-fatura.pdf')
    ek = [{'sayfa': 1, 'x': 0.1, 'y': 0.9, 'metin': 'Not', 'boyut': 10, 'renk': '#000000',
           'font': {'tur': 'standart', 'ad': 'helv'}}]
    r = ist.post('/api/uret', headers=_xhr(ist),
                 data={'pdf': (io.BytesIO(veri), 'a.pdf'), 'duzeltmeler': '[]', 'eklemeler': json.dumps(ek)})
    assert r.status_code == 200
    son = _olaylar()[-1]
    assert (son['tur'], son['duzeltme'], son.get('ekleme')) == ('uretildi', 0, 1)
    sayfa = ist.get('/yonetim/kullanim').get_data(as_text=True)
    assert 'Eklenen metin' in sayfa and '1 ekleme' in sayfa
