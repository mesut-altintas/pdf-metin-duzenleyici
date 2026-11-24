import os
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

TEST_SIFRE = 'dogru-sifre-123'
os.environ.pop('VERCEL', None)
os.environ.pop('DATABASE_URL', None)   # testler hiçbir zaman gerçek veritabanına bağlanmaz
os.environ['SECRET_KEY'] = 'test-anahtari'
os.environ['KULLANICILAR'] = f'deneme={generate_password_hash(TEST_SIFRE, method="pbkdf2:sha256:1000")}'
for _ad in ('RESEND_API_KEY', 'EPOSTA_GONDEREN', 'UYGULAMA_ADRESI', 'KAYIT_ACIK', 'KOTA_KAPALI'):
    os.environ.pop(_ad, None)


@pytest.fixture(autouse=True)
def _gercek_eposta_yok(monkeypatch):
    """Hiçbir test gerçek e-posta göndermez: HTTP adımı kapalı. E-postayı sınayan test
    `eposta._ilet`'i kendi kaydedicisiyle değiştirir."""
    import eposta

    def kapali(govde):
        raise AssertionError('testte gerçek e-posta gönderilmeye çalışıldı')
    monkeypatch.setattr(eposta, '_ilet', kapali)

ORNEKLER = Path(__file__).resolve().parent / 'ornekler'


@pytest.fixture
def ornek():
    def oku(ad):
        return (ORNEKLER / ad).read_bytes()
    return oku


@pytest.fixture
def vt_db(monkeypatch):
    """Yerel PostgreSQL'de boş şema. TEST_PG_URL yoksa test atlanır.
    Tabloları sildiği için yalnız yerel adreste çalışır."""
    import psycopg2

    import kullanicilar
    url = os.environ.get('TEST_PG_URL')
    if not url:
        pytest.skip('TEST_PG_URL tanımlı değil (yerel PostgreSQL gerekir; CLAUDE.md)')
    if not any(h in url for h in ('host=127.0.0.1', 'host=localhost', '@127.0.0.1', '@localhost')):
        pytest.fail('TEST_PG_URL yerel değil; tablolar silineceği için durduruldu.')
    kullanicilar._kapat()
    kullanicilar._sema_kuruldu = False
    kullanicilar.onbellek_sil()
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS kullanicilar, yonetim_kaydi, giris_denemesi, kullanim_olayi, '
                    'kota_belge, kota_sayaci, hesap')
    conn.close()
    monkeypatch.setenv('DATABASE_URL', url)
    yield url
    kullanicilar._kapat()
    kullanicilar._sema_kuruldu = False
    kullanicilar.onbellek_sil()
