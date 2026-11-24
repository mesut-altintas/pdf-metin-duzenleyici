"""PDF Metin Düzenleyici — Flask uygulaması (Vercel giriş noktası: `app`).

PDF hiçbir yere kaydedilmez: istek belleğinde işlenir, sonuç indirilir.
PDF işleri `pdf_duzenle/motor.py`'de; Flask'a bağımlı değildir (ileride
portala modül olarak taşınabilsin diye).
"""
import logging
import os
import secrets
from datetime import timedelta, timezone

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge

import kullanim
from giris import giris_bp, mevcut_kullanici, xhr_mi
from kayit import kayit_bp
from pdf_duzenle.rotalar import AZAMI_DOSYA, AZAMI_ISTEK, mb, pdf_bp
from surum import LISANS, SURUM, TELIF, kaynak_adresi
from yonetim import yonetim_bp

VERCEL = bool(os.environ.get('VERCEL'))

app = Flask(__name__, static_folder='public/static', static_url_path='/static')

_anahtar = os.environ.get('SECRET_KEY')
if not _anahtar:
    if VERCEL:
        # Bilinen bir sabite düşmek oturum çerezini taklit edilebilir yapar.
        raise RuntimeError('SECRET_KEY ortam değişkeni tanımlı değil.')
    _anahtar = secrets.token_hex(32)
    logging.warning('SECRET_KEY yok: yerel geçici anahtar kullanılıyor (yeniden başlatınca oturum düşer).')

app.config.update(
    SECRET_KEY=_anahtar,
    MAX_CONTENT_LENGTH=AZAMI_ISTEK,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=VERCEL or os.environ.get('GUVENLI_CEREZ') == '1',
    WTF_CSRF_TIME_LIMIT=None,  # belirteç oturumla yaşar
)

csrf = CSRFProtect(app)
app.register_blueprint(giris_bp)
app.register_blueprint(kayit_bp)
app.register_blueprint(pdf_bp)
app.register_blueprint(yonetim_bp)

# Kökteki PWA dosyaları. Vercel'de bunları CDN `public/`'ten verir (istek işleve
# hiç gelmez, `public/**` işlev paketinde de yok); bu rotalar yerel geliştirme için.
_PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public')
_KOK_DOSYALARI = {
    'sw.js': 'text/javascript',
    'manifest.webmanifest': 'application/manifest+json',
    'favicon.ico': 'image/x-icon',
    'cevrimdisi.html': 'text/html',
}


def _kok_dosyasi(ad, tur):
    def gorunum():
        return send_from_directory(_PUBLIC, ad, mimetype=tur)
    return gorunum


for _ad, _tur in _KOK_DOSYALARI.items():
    app.add_url_rule('/' + _ad, 'kok_' + _ad.replace('.', '_'), _kok_dosyasi(_ad, _tur))

# İstanbul 2016'dan beri sabit UTC+3 (yaz saati yok); tzdata'ya bağımlı olmasın.
_ISTANBUL = timezone(timedelta(hours=3))


app.add_template_filter(mb, 'mb')


@app.context_processor
def surum_bilgisi():
    return {'surum': SURUM, 'telif': TELIF, 'kaynak_adresi': kaynak_adresi(), 'lisans': LISANS}


@app.template_filter('tarihsaat')
def tarihsaat(deger):
    if deger is None:
        return ''
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(_ISTANBUL).strftime('%d.%m.%Y %H:%M')


@app.after_request
def guvenlik_basliklari(yanit):
    yanit.headers.setdefault('X-Content-Type-Options', 'nosniff')
    yanit.headers.setdefault('X-Frame-Options', 'DENY')
    yanit.headers.setdefault('Referrer-Policy', 'same-origin')
    # PDF ve sayfa görüntüleri kullanıcı verisi: hiçbir yerde önbelleğe alınmaz
    # (send_file kendi 'no-cache'ini yazıyor, o yüzden üstüne yazılır).
    yanit.headers['Cache-Control'] = 'no-store'
    return yanit


@app.errorhandler(RequestEntityTooLarge)
def cok_buyuk(_e):
    mesaj = f'Dosya çok büyük. Üst sınır {mb(AZAMI_DOSYA)} MB.'
    ben = mevcut_kullanici()
    if ben is not None:
        kullanim.kaydet(ben.ad, 'uretildi' if request.path == '/api/uret' else 'acildi',
                        basarili=False, hata='boyut', boyut=request.content_length)
    if xhr_mi():
        return jsonify(hata=mesaj), 413
    return mesaj, 413


@app.errorhandler(403)
def yasak(_e):
    mesaj = 'Bu işlem için yetkiniz yok.'
    if xhr_mi():
        return jsonify(hata=mesaj), 403
    return render_template('hata.html', baslik='Yetki yok', mesaj=mesaj), 403


@app.errorhandler(CSRFError)
def csrf_hatasi(_e):
    mesaj = 'Sayfanın süresi dolmuş. Sayfayı yenileyip yeniden deneyin.'
    if xhr_mi():
        return jsonify(hata=mesaj), 400
    return render_template('giris.html', hata=mesaj), 400


if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5050)))
