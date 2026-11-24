"""Kayıt ve şifremi unuttum formlarında "Ben robot değilim" (Google reCAPTCHA v2).

Neden: iki form da yazılan adrese e-posta gönderiyor; bir bot bunları başkalarının
adresine toplu e-posta göndertmek için kullanabilir. İstek sınırı ve gizli tuzak alan
bunu yavaşlatır, durdurmaz (`docs/kayit.md`). Düzen portalın LFA kayıt formundan
(`eks-portal/lfa/robot.py`) alındı.

Anahtarlar ortamda (`RECAPTCHA_SITE_KEY`, `RECAPTCHA_SECRET`); **anahtar yoksa kutu
çizilmez ve doğrulama atlanır** (yerel geliştirme, testler, anahtar girilene kadar canlı).
Acil kapatma `ROBOT_DOGRULAMA_KAPALI=1`. Gizli anahtar hiçbir log satırına ve sayfaya
yazılmaz. Anahtarın alan adı listesinde uygulamanın adresi olmalı (Google reCAPTCHA
yönetim sayfası).

Sonuç üç türlü:
    'gecti'        Google "başarılı" dedi
    'gecmedi'      jeton yok / geçersiz / süresi dolmuş / tekrar kullanılmış → form
                   "kutuyu işaretleyin" diye geri döner, e-posta gitmez
    'ulasilamadi'  Google'a ulaşılamadı ya da bizim yapılandırmamız hatalı (gizli anahtar
                   geçersiz) → **istek işlenir, ERROR loglanır**: Google'ın kesintisi ya da
                   bizim hatamız gerçek kullanıcıyı durdurmasın; istek sınırı yine geçerli.
"""
import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

DOGRULAMA_ADRESI = 'https://www.google.com/recaptcha/api/siteverify'
ZAMAN_ASIMI = 5            # sn; e-posta gönderimine de süre kalsın
GECTI, GECMEDI, ULASILAMADI = 'gecti', 'gecmedi', 'ulasilamadi'

# Kişiden kaynaklanan hata kodları reddedilir; gerisi (gizli anahtar eksik / geçersiz,
# bad-request) bizim yapılandırmamız: istek işlenir.
_KISI_HATALARI = {'missing-input-response', 'invalid-input-response', 'timeout-or-duplicate'}


def site_anahtari():
    """Etkinse site anahtarı (sayfaya basılan, gizli değil); değilse None."""
    if os.environ.get('ROBOT_DOGRULAMA_KAPALI') == '1':
        return None
    site = (os.environ.get('RECAPTCHA_SITE_KEY') or '').strip()
    gizli = (os.environ.get('RECAPTCHA_SECRET') or '').strip()
    return site if site and gizli else None


def _sor(veri):
    """Google'a doğrulama isteği; cevap sözlük. Testler bunu değiştirir (conftest)."""
    istek = urllib.request.Request(DOGRULAMA_ADRESI, data=urllib.parse.urlencode(veri).encode('ascii'),
                                   method='POST', headers={'User-Agent': 'pdf-editor'})
    with urllib.request.urlopen(istek, timeout=ZAMAN_ASIMI) as yanit:
        return json.loads(yanit.read().decode('utf-8'))


def dogrula(jeton, ip=None):
    """Formdan gelen `g-recaptcha-response` jetonunu Google'a sorar."""
    jeton = (jeton or '').strip()
    if not jeton:
        return GECMEDI
    veri = {'secret': (os.environ.get('RECAPTCHA_SECRET') or '').strip(), 'response': jeton}
    if ip:
        veri['remoteip'] = ip
    try:
        sonuc = _sor(veri)
    except Exception as e:
        log.error('robot doğrulaması: Google\'a ulaşılamadı, istek işlendi: %s', type(e).__name__)
        return ULASILAMADI
    if sonuc.get('success'):
        return GECTI
    kodlar = set(sonuc.get('error-codes') or [])
    if not kodlar or kodlar & _KISI_HATALARI:
        return GECMEDI
    log.error('robot doğrulaması: yapılandırma hatası %s, istek işlendi', sorted(kodlar))
    return ULASILAMADI
