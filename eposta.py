"""E-posta gönderimi: Resend HTTP API (yeni bağımlılık yok, urllib).

Ortam değişkenleri (üçü de doluysa e-posta "tanımlı"):
  RESEND_API_KEY   Resend anahtarı (yalnız gönderme izni yeter).
  EPOSTA_GONDEREN  "PDF Metin Düzenleyici <pdf@altintas.biz>" — alan adı Resend'de doğrulanmış olmalı.
  UYGULAMA_ADRESI  https://pdf.altintas.biz — e-postadaki bağlantılar bundan kurulur.

Bağlantılar istekteki Host'tan KURULMAZ: saldırgan Host başlığını değiştirip birinin
şifre yenileme bağlantısını kendi sunucusuna yönlendiremesin.

Log'a alıcı adresi, konu ya da metin yazılmaz; yalnız hata türü.
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API = 'https://api.resend.com/emails'
ZAMAN_ASIMI = 8          # sn; Vercel işlevinin süresine pay bırakır


class EpostaHatasi(Exception):
    """Gönderilemedi (kullanıcıya gösterilecek metin)."""


def tanimli():
    return all(os.environ.get(k) for k in ('RESEND_API_KEY', 'EPOSTA_GONDEREN', 'UYGULAMA_ADRESI'))


def adres(yol):
    return os.environ['UYGULAMA_ADRESI'].rstrip('/') + yol


def _ilet(govde):
    """HTTP isteği. Testler bunu değiştirir (conftest: gerçek gönderim kapalı)."""
    istek = urllib.request.Request(
        API, data=json.dumps(govde).encode('utf-8'), method='POST',
        headers={'Authorization': f'Bearer {os.environ["RESEND_API_KEY"]}',
                 'Content-Type': 'application/json', 'User-Agent': 'pdf-editor'})
    with urllib.request.urlopen(istek, timeout=ZAMAN_ASIMI) as yanit:
        return yanit.status


def gonder(alici, konu, metin):
    """Düz metin e-posta. Gönderilemezse EpostaHatasi."""
    if not tanimli():
        raise EpostaHatasi('E-posta gönderimi tanımlı değil.')
    govde = {'from': os.environ['EPOSTA_GONDEREN'], 'to': [alici], 'subject': konu, 'text': metin}
    try:
        durum = _ilet(govde)
    except urllib.error.HTTPError as e:
        log.error('e-posta gönderilemedi: HTTP %s', e.code)
        raise EpostaHatasi('E-posta gönderilemedi. Biraz sonra yeniden deneyin.')
    except (urllib.error.URLError, OSError):
        log.error('e-posta gönderilemedi: bağlantı ya da zaman aşımı')
        raise EpostaHatasi('E-posta gönderilemedi. Biraz sonra yeniden deneyin.')
    if not 200 <= durum < 300:
        log.error('e-posta gönderilemedi: HTTP %s', durum)
        raise EpostaHatasi('E-posta gönderilemedi. Biraz sonra yeniden deneyin.')
