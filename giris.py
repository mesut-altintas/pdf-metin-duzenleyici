"""Giriş, oturum ve kendi şifresini değiştirme.

İki kaynak:
  * ortam  — KULLANICILAR = "ad1=<özet>,ad2=<özet>" (python sifre_ozeti.py).
             Kalıcı yöneticiler; sayfadan değiştirilemez, silinemez.
  * vt     — Neon'daki kullanıcılar (yönetim sayfasından).
Aynı ad iki yerde varsa ortam geçerlidir (yönetim sayfası bu adla kayıt açmaz).

Oturum her istekte denetlenir: kullanıcı listeden çıkmış, pasife alınmış ya da
oturum sürümü değişmişse (şifre / yöneticilik değişti) oturum düşer.
"""
import hashlib
import logging
import os
import re
import secrets
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import kullanicilar as vt
import kullanim

giris_bp = Blueprint('giris', __name__)
log = logging.getLogger(__name__)

# Kullanıcı yokken de aynı süre harcansın (ad tahmini zamanlamadan anlaşılmasın).
_SAHTE_OZET = generate_password_hash('-yok-')

AD_DESENI = re.compile(r'[a-z0-9][a-z0-9._-]{1,39}')
# Bilerek dar: ASCII yerel kısım ve alan adı (Türkçe harfli adres göndericide sorun çıkarır).
EPOSTA_DESENI = re.compile(r'[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')
EN_AZ_SIFRE = 10
_AD_SINIRI = 5        # aynı adres + aynı ad: 15 dakikada
_ADRES_SINIRI = 30    # aynı adres, bütün adlar: 15 dakikada

# Veritabanı yokken (yerel) süreç belleğinde sayaç.
_denemeler: dict[str, list[float]] = {}


def ortam_kullanicilari() -> dict[str, str]:
    sonuc = {}
    for parca in os.environ.get('KULLANICILAR', '').split(','):
        ad, _, ozet = parca.strip().partition('=')
        ad = ad.strip().lower()
        if ad and ozet.strip():
            sonuc[ad] = ozet.strip()
    return sonuc


def sifre_uret(uzunluk=14):
    alfabe = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alfabe) for _ in range(uzunluk))


def sifre_kurali(sifre):
    """Kurala uymuyorsa hata metni, uyuyorsa None."""
    if len(sifre) < EN_AZ_SIFRE:
        return f'Şifre en az {EN_AZ_SIFRE} karakter olmalı.'
    if len(sifre) > 200:
        return 'Şifre çok uzun.'
    return None


# ---------------------------------------------------------------- deneme sınırı

def istemci_adresi() -> str:
    # Vercel gerçek istemci adresini X-Forwarded-For'un başına koyar.
    ileti = request.headers.get('X-Forwarded-For', '')
    return ileti.split(',')[0].strip() or (request.remote_addr or '-')


_istemci = istemci_adresi


def _anahtarlar(ad):
    """Yazılan ad / e-posta tabloya açık yazılmaz, parmak iziyle (aydınlatma metni)."""
    adres = _istemci()
    iz = hashlib.sha256(ad.encode('utf-8')).hexdigest()[:16]
    return f'adres:{adres}', f'ad:{adres}:{iz}'


def _bellek_kilitli(anahtarlar):
    simdi = time.time()
    for k in anahtarlar:
        _denemeler[k] = [t for t in _denemeler.get(k, []) if simdi - t < vt.DENEME_PENCERESI]
    adres_k, ad_k = anahtarlar
    return len(_denemeler[ad_k]) >= _AD_SINIRI or len(_denemeler[adres_k]) >= _ADRES_SINIRI


def _kilitli(ad) -> bool:
    """Sayaç veritabanında (bütün Vercel örnekleri ortak). Veritabanına
    ulaşılamazsa süreç belleği: ortam yöneticileri o durumda da girebilsin."""
    anahtarlar = _anahtarlar(ad)
    if vt.tanimli():
        try:
            adres_k, ad_k = anahtarlar
            return vt.deneme_sayisi(ad_k) >= _AD_SINIRI or vt.deneme_sayisi(adres_k) >= _ADRES_SINIRI
        except vt.DepoHatasi:
            log.warning('deneme sayacı okunamadı; bellekteki sayaç kullanılıyor')
    return _bellek_kilitli(anahtarlar)


def _hatali_deneme(ad):
    anahtarlar = _anahtarlar(ad)
    for k in anahtarlar:
        _denemeler.setdefault(k, []).append(time.time())
    if vt.tanimli():
        try:
            vt.deneme_ekle(*anahtarlar)
        except vt.DepoHatasi:
            log.warning('hatalı deneme yazılamadı')


def _basarili(ad):
    _, ad_k = _anahtarlar(ad)
    _denemeler.pop(ad_k, None)
    if vt.tanimli():
        try:
            vt.denemeleri_sil(ad_k)
        except vt.DepoHatasi:
            log.warning('deneme sayacı silinemedi')


# ---------------------------------------------------------------- oturumdaki kullanıcı

class Kullanici:
    def __init__(self, ad, kaynak, yonetici, gorunen_ad='', sifre_degistirmeli=False):
        self.ad = ad
        self.kaynak = kaynak            # 'ortam' | 'vt'
        self.yonetici = yonetici
        self.gorunen_ad = gorunen_ad or ad
        self.sifre_degistirmeli = sifre_degistirmeli


def _cozumle():
    """Oturumdaki geçerli kullanıcı; geçersizse None. DepoHatasi geçirir."""
    ad = session.get('kullanici')
    kaynak = session.get('kaynak')
    if not ad:
        return None
    if kaynak == 'ortam':
        return Kullanici(ad, 'ortam', True) if ad in ortam_kullanicilari() else None
    if kaynak != 'vt' or not vt.tanimli() or ad in ortam_kullanicilari():
        return None
    k = vt.oturum_kaydi(ad)
    if not k or not k['aktif'] or k['oturum_surum'] != session.get('surum'):
        return None
    return Kullanici(ad, 'vt', k['yonetici'], k['gorunen_ad'], k['sifre_degistirmeli'])


def mevcut_kullanici():
    if 'kullanici_nesnesi' not in g:
        try:
            g.kullanici_nesnesi = _cozumle()
        except vt.DepoHatasi:
            g.kullanici_nesnesi = None
    return g.kullanici_nesnesi


def xhr_mi() -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _oturum_yok():
    session.clear()
    if xhr_mi():
        return jsonify(hata='Oturum kapandı. Sayfayı yenileyip yeniden giriş yapın.'), 401
    return redirect(url_for('giris.giris', next=request.full_path.rstrip('?')))


def login_required(f):
    @wraps(f)
    def sarmal(*args, **kwargs):
        try:
            kullanici = _cozumle()
        except vt.DepoHatasi as e:
            # Kullanıcı bilgisi okunamadı: oturum düşürülmez, istek yapılmaz.
            if xhr_mi():
                return jsonify(hata=str(e)), 503
            return render_template('hata.html', mesaj=str(e)), 503
        g.kullanici_nesnesi = kullanici
        if kullanici is None:
            return _oturum_yok()
        if kullanici.sifre_degistirmeli and request.endpoint != 'giris.sifre':
            if xhr_mi():
                return jsonify(hata='Önce şifrenizi değiştirin.'), 403
            return redirect(url_for('giris.sifre'))
        return f(*args, **kwargs)
    return sarmal


def yonetici_gerekli(f):
    @login_required
    @wraps(f)
    def sarmal(*args, **kwargs):
        if not g.kullanici_nesnesi.yonetici:
            abort(403)
        return f(*args, **kwargs)
    return sarmal


@giris_bp.app_context_processor
def sablona_kullanici():
    return {'ben': g.get('kullanici_nesnesi')}


# ---------------------------------------------------------------- uçlar

def _guvenli_donus(hedef: str | None) -> str:
    if not hedef:
        return url_for('pdf.editor')
    parca = urlsplit(hedef)
    if parca.scheme or parca.netloc or not hedef.startswith('/') or hedef.startswith('//'):
        return url_for('pdf.editor')
    return hedef


def _dogrula(ad, sifre):
    """(kaynak, kayıt) ya da None. `ad` kullanıcı adı ya da e-posta. Süre, kullanıcı
    olsun olmasın aynı."""
    ortam = ortam_kullanicilari()
    if ad in ortam:
        return ('ortam', None) if check_password_hash(ortam[ad], sifre) else None
    kayit = None
    if vt.tanimli():
        if EPOSTA_DESENI.fullmatch(ad):
            kayit = vt.bul_eposta(ad)
        elif AD_DESENI.fullmatch(ad):
            kayit = vt.bul(ad)
    ozet = kayit['sifre_ozeti'] if kayit else _SAHTE_OZET
    dogru = check_password_hash(ozet, sifre)
    if kayit and dogru and kayit['aktif']:
        return 'vt', kayit
    return None


@giris_bp.route('/giris', methods=['GET', 'POST'])
def giris():
    hata = None
    kod = 200
    if request.method == 'POST':
        ad = (request.form.get('kullanici') or '').strip().lower()[:254]
        sifre = request.form.get('sifre') or ''
        try:
            if _kilitli(ad):
                hata, kod = 'Çok fazla hatalı deneme. 15 dakika sonra yeniden deneyin.', 429
            else:
                sonuc = _dogrula(ad, sifre)
                if sonuc:
                    kaynak, kayit = sonuc
                    _basarili(ad)
                    gercek_ad = kayit['ad'] if kayit else ad     # e-postayla girende kullanıcı adı
                    session.clear()
                    session.permanent = True
                    session['kullanici'] = gercek_ad
                    session['kaynak'] = kaynak
                    if kayit:
                        session['surum'] = kayit['oturum_surum']
                        try:
                            vt.son_giris_yaz(gercek_ad)
                        except vt.DepoHatasi:
                            log.warning('son giriş yazılamadı')
                    kullanim.kaydet(gercek_ad, 'giris')
                    return redirect(_guvenli_donus(request.args.get('next')))
                _hatali_deneme(ad)
                time.sleep(0.5)
                hata, kod = 'Kullanıcı adı, e-posta ya da şifre hatalı.', 401
        except vt.DepoHatasi as e:
            hata, kod = str(e), 503
    if not ortam_kullanicilari() and not vt.tanimli():
        hata = hata or 'Tanımlı kullanıcı yok (KULLANICILAR ortam değişkeni boş).'
    return render_template('giris.html', hata=hata), kod


@giris_bp.route('/cikis', methods=['POST'])
def cikis():
    session.clear()
    return redirect(url_for('giris.giris'))


@giris_bp.route('/sifre', methods=['GET', 'POST'])
@login_required
def sifre():
    ben = g.kullanici_nesnesi
    if ben.kaynak != 'vt':
        return render_template('sifre.html', ortam=True)
    hata = None
    if request.method == 'POST':
        mevcut = request.form.get('mevcut') or ''
        yeni = request.form.get('yeni') or ''
        tekrar = request.form.get('tekrar') or ''
        try:
            kayit = vt.bul(ben.ad)
            if not kayit or not check_password_hash(kayit['sifre_ozeti'], mevcut):
                hata = 'Mevcut şifre hatalı.'
            elif yeni != tekrar:
                hata = 'Yeni şifreler aynı değil.'
            elif yeni == mevcut:
                hata = 'Yeni şifre eskisiyle aynı olamaz.'
            else:
                hata = sifre_kurali(yeni)
            if not hata:
                vt.sifre_ata(ben.ad, generate_password_hash(yeni), False, ben.ad,
                             'kendi şifresini değiştirdi')
                kayit = vt.bul(ben.ad)
                session['surum'] = kayit['oturum_surum']
                flash('Şifreniz değiştirildi.', 'basari')
                return redirect(url_for('pdf.editor'))
        except vt.DepoHatasi as e:
            hata = str(e)
    return render_template('sifre.html', ortam=False, hata=hata, zorunlu=ben.sifre_degistirmeli,
                           en_az=EN_AZ_SIFRE), (400 if hata else 200)
