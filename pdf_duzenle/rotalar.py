"""Düzenleyicinin sayfası ve iki ucu. PDF istek belleğinde işlenir, saklanmaz."""
import base64
import io
import json
import logging
import time
from pathlib import PurePath

from flask import Blueprint, g, jsonify, render_template, request, send_file

import kota
import kullanicilar as vt
import kullanim
from giris import login_required
from pdf_duzenle import motor

pdf_bp = Blueprint('pdf', __name__)

# Vercel işlevinin gövde sınırı ondalık 4,5 MB (1024 tabanlı değil). Ölçüm, 22.11.2026,
# canlı: 4.492.812 bayt uygulamaya ulaştı, 4.494.359 bayt ulaşmadan 413
# FUNCTION_PAYLOAD_TOO_LARGE döndü (başlıklar da sayılıyor olmalı). Yanıt için aynı
# sınır varsayılır (ölçülmedi). Form zarfına ve değişiklik listesine pay bırakılır.
VERCEL_OLCULEN_SINIR = 4_492_812
AZAMI_ISTEK = 4_480_000
AZAMI_DOSYA = 4_400_000
AZAMI_YANIT = 4_400_000

log = logging.getLogger(__name__)


def _hata(mesaj, kod=400):
    return jsonify(hata=mesaj), kod


def _pdf_oku():
    f = request.files.get('pdf')
    if f is None:
        raise motor.PdfHatasi('PDF dosyası gelmedi.')
    veri = f.read(AZAMI_DOSYA + 1)
    if len(veri) > AZAMI_DOSYA:
        raise motor.PdfHatasi(f'Dosya çok büyük. Üst sınır {mb(AZAMI_DOSYA)} MB.', kod='boyut')
    if not veri:
        raise motor.PdfHatasi('Dosya boş.')
    return f.filename or 'belge.pdf', veri


def mb(n):
    """Ondalık MB (Finder'ın ve Vercel'in birimi), Türkçe ondalık virgülüyle."""
    return f'{n / 1_000_000:.1f}'.replace('.', ',')


@pdf_bp.route('/')
@login_required
def editor():
    kd, okunamadi = None, False
    try:
        kd = kota.durum(g.kullanici_nesnesi)
    except vt.DepoHatasi:
        log.warning('kota durumu okunamadı (sayfa)')
        okunamadi = True
    return render_template('editor.html', azami_dosya=AZAMI_DOSYA, azami_mb=mb(AZAMI_DOSYA),
                           azami_sayfa=motor.AZAMI_SAYFA, kota=kota.istemci_bilgisi(kd) if kd else None,
                           kota_plan=kd.plan.ad if kd else None, kota_okunamadi=okunamadi)


def _kota_bilgisi(veri):
    """Analiz cevabına eklenecek kota özeti; kotasızsa None."""
    try:
        kd = kota.durum(g.kullanici_nesnesi, kullanim.parmak_izi(veri))
    except vt.DepoHatasi:
        log.warning('kota durumu okunamadı (analiz)')
        return {'okunamadi': True}
    return kota.istemci_bilgisi(kd) if kd else None


def _kota_dolu(kd):
    return jsonify(hata=kota.mesaj(kd), kod='kota', kota=kota.istemci_bilgisi(kd)), 403


def _olcum(tur, bas, veri, **ek):
    """Kullanım kaydı: dosya adı, içerik ve hata metni yazılmaz (kullanim.py)."""
    kullanim.kaydet(
        g.kullanici_nesnesi.ad, tur, sure_ms=int((time.monotonic() - bas) * 1000),
        boyut=len(veri) if veri else request.content_length,
        belge=kullanim.parmak_izi(veri) if veri else None, **ek)


@pdf_bp.route('/api/analiz', methods=['POST'])
@login_required
def analiz():
    bas = time.monotonic()
    yalniz = request.form.get('yalniz_resim') == '1'
    tur = 'onizlendi' if yalniz else 'acildi'
    veri = None
    try:
        _ad, veri = _pdf_oku()
        sonuc = motor.analiz(veri, yalniz_resim=yalniz, resim_butcesi=AZAMI_YANIT - 300_000)
    except motor.PdfHatasi as e:
        _olcum(tur, bas, veri, basarili=False, hata=e.kod)
        return _hata(str(e))
    except Exception:
        log.exception('analiz başarısız')
        _olcum(tur, bas, veri, basarili=False, hata='beklenmeyen')
        return _hata('PDF okunamadı. Dosya bozuk ya da desteklenmeyen bir biçimde olabilir.', 500)
    _olcum(tur, bas, veri, sayfa=len(sonuc['sayfalar']))
    if not yalniz:
        bilgi = _kota_bilgisi(veri)
        if bilgi:
            sonuc['kota'] = bilgi
    return jsonify(sonuc)


@pdf_bp.route('/api/uret', methods=['POST'])
@login_required
def uret():
    bas = time.monotonic()
    veri = None
    duzeltmeler = eklemeler = paragraflar = None

    def olc(**ek):
        _olcum('uretildi', bas, veri, duzeltme=_toplam(duzeltmeler, paragraflar), ekleme=_say(eklemeler), **ek)

    try:
        ad, veri = _pdf_oku()
        try:
            duzeltmeler = json.loads(request.form.get('duzeltmeler') or '[]')
            eklemeler = json.loads(request.form.get('eklemeler') or '[]')
            paragraflar = json.loads(request.form.get('paragraflar') or '[]')
        except ValueError:
            raise motor.PdfHatasi('Değişiklik listesi okunamadı.', kod='liste')
    except motor.PdfHatasi as e:
        olc(basarili=False, hata=e.kod)
        return _hata(str(e))

    # Kota üretimden ÖNCE okunur (dolu kotada iş yapılmaz), üretim başarılıysa sayılır (kota.py).
    izi = kullanim.parmak_izi(veri)
    try:
        kd = kota.durum(g.kullanici_nesnesi, izi)
    except vt.DepoHatasi:
        log.warning('kota durumu okunamadı (üretim)')
        olc(basarili=False, hata='kota_okunamadi')
        return _hata('Belge hakkınız denetlenemedi. Biraz sonra yeniden deneyin.', 503)
    if kd and not kd.izin:
        olc(basarili=False, hata='kota')
        return _kota_dolu(kd)

    try:
        sonuc, uyarilar = motor.uygula(veri, duzeltmeler, eklemeler, paragraflar)
    except motor.PdfHatasi as e:
        olc(basarili=False, hata=e.kod)
        return _hata(str(e))
    except Exception:
        log.exception('üretim başarısız')
        olc(basarili=False, hata='beklenmeyen')
        return _hata('PDF oluşturulamadı. Beklenmeyen bir hata oldu.', 500)

    if len(sonuc) > AZAMI_YANIT:
        olc(basarili=False, hata='yanit')
        return _hata(f'Oluşan PDF {mb(len(sonuc))} MB; sunucunun gönderebileceği sınırı '
                     f'({mb(AZAMI_YANIT)} MB) aşıyor.', 413)

    kullanilan = None
    if kd:
        try:
            kullanilan = kota.say(kd, izi)
        except vt.DepoHatasi:
            # Yazılamadı ya da sonucu bilinmiyor: kullanıcı işini kaybetmesin, dosya gider.
            log.error('kota sayılamadı; dosya yine gönderildi')
        else:
            if kullanilan is None:      # sınır bu istekle aynı anda doldu
                kd.kullanilan = kd.sinir
                olc(basarili=False, hata='kota')
                return _kota_dolu(kd)
    olc(uyari=len(uyarilar))

    kok = PurePath(ad).stem or 'belge'
    yanit = send_file(io.BytesIO(sonuc), mimetype='application/pdf', as_attachment=True,
                      download_name=f'{kok}-duzenlenmis.pdf')
    if uyarilar:
        yanit.headers['X-Uyarilar'] = base64.b64encode(
            json.dumps(uyarilar, ensure_ascii=False).encode('utf-8')).decode('ascii')
    if kullanilan is not None:
        yanit.headers['X-Kota'] = f'{kullanilan}/{kd.sinir}'
    return yanit


def _say(liste):
    return len(liste) if isinstance(liste, list) else None


def _toplam(*listeler):
    sayilar = [_say(x) for x in listeler if _say(x) is not None]
    return sum(sayilar) if sayilar else None


@pdf_bp.route('/api/olay', methods=['POST'])
@login_required
def olay():
    """Tarayıcıdaki olaylar: indirme, paylaşma, sınırı aşan dosyanın reddi."""
    v = request.get_json(silent=True) or {}
    tur = v.get('tur')
    if tur not in kullanim.ISTEMCI_TURLERI:
        return _hata('Geçersiz olay.')
    buyuk = tur == 'buyuk_dosya'
    kullanim.kaydet(g.kullanici_nesnesi.ad, tur, basarili=not buyuk, hata='boyut' if buyuk else None,
                    duzeltme=v.get('duzeltme'), boyut=v.get('boyut'))
    return '', 204
