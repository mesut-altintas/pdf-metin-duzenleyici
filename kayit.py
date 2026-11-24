"""Kendi kendine kayıt (önce e-posta), şifremi unuttum, yasal metinler.

Kayıt iki adımdır (kullanıcı kararı 22.11.2026: doğrulamadan giriş yok):
  1. /kayit: yalnız e-posta → adrese 24 saat geçerli bağlantı.
  2. /kayit/tamamla: bağlantıyı açan ad, şifre ve koşul onayını verir → hesap
     ücretsiz planla açılır, oturum başlar.
Hesap ancak e-postadaki bağlantıyla açıldığı için "doğrulanmamış hesap" durumu
yoktur ve şifreyi başkası önceden belirleyemez (adrese sahip olan belirler).

Ekranda adresin kayıtlı olup olmadığı söylenmez: her durumda aynı cevap. Kayıtlı
adrese "zaten hesabınız var" e-postası gider. Şifremi unuttum da aynı.

Kapatma: kayıt uçları `KAYIT_ACIK=1` değilse 404 (lisans netleşene kadar kapalı,
kullanıcı kararı). Şifremi unuttum e-posta tanımlıysa açık. İkisi de veritabanı ve
e-posta (eposta.tanimli) ister; yoksa 404 ve giriş sayfasındaki bağlantı çizilmez.

Sınır (giris_denemesi tablosu, 15 dk): istek başına adres 10, e-posta 3. Adres
tabloya parmak iziyle yazılır, açık yazılmaz.
"""
import hashlib
import logging
import os
import re
import secrets

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, session,
                   url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

import eposta
import kullanicilar as vt
import kullanim
from giris import (AD_DESENI, EN_AZ_SIFRE, EPOSTA_DESENI, istemci_adresi, mevcut_kullanici,
                   ortam_kullanicilari, sifre_kurali)

kayit_bp = Blueprint('kayit', __name__)
log = logging.getLogger(__name__)

KAYIT_SURESI = 24 * 3600
SIFIRLAMA_SURESI = 3600
ADRES_SINIRI = 10            # aynı istemci adresinden 15 dakikada e-posta isteği
EPOSTA_SINIRI = 3            # aynı e-postaya 15 dakikada
# Kabul edilen koşulların sürümü; metin değişince artar (kullanicilar.kosul_surum).
KOSUL_SURUMU = 'taslak-2026-11-24'
UYGULAMA = 'PDF Metin Düzenleyici'


def kayit_acik():
    return (os.environ.get('KAYIT_ACIK') == '1' and vt.tanimli() and eposta.tanimli())


def sifirlama_acik():
    return vt.tanimli() and eposta.tanimli()


@kayit_bp.app_context_processor
def sablona_bayraklar():
    return {'kayit_acik': kayit_acik(), 'sifirlama_acik': sifirlama_acik()}


# ---------------------------------------------------------------- yardımcılar

def _imzaci(amac):
    return URLSafeTimedSerializer(current_app.secret_key, salt=f'pdf-editor-{amac}')


def _coz(amac, belirtec, sure):
    """(veri, hata metni)."""
    try:
        return _imzaci(amac).loads(belirtec or '', max_age=sure), None
    except SignatureExpired:
        return None, 'Bağlantının süresi dolmuş. Yeni bağlantı isteyin.'
    except BadSignature:
        return None, 'Bağlantı geçersiz. E-postadaki bağlantıyı eksiksiz açtığınızdan emin olun.'


def _eposta_al():
    deger = (request.form.get('eposta') or '').strip().lower()
    if len(deger) > 254 or not EPOSTA_DESENI.fullmatch(deger):
        return deger, 'Geçerli bir e-posta adresi yazın.'
    return deger, None


def _iz(deger):
    return hashlib.sha256(deger.encode('utf-8')).hexdigest()[:16]


def _sinirda(adres_):
    """İstek sınırı aşıldıysa True; aşılmadıysa isteği sayar."""
    ak, ek = f'eposta-adres:{istemci_adresi()}', f'eposta:{_iz(adres_)}'
    if vt.deneme_sayisi(ak) >= ADRES_SINIRI or vt.deneme_sayisi(ek) >= EPOSTA_SINIRI:
        return True
    vt.deneme_ekle(ak, ek)
    return False


def _bot_mu():
    """Gizli alan doluysa bot: cevap aynı, hiçbir şey yapılmaz."""
    return bool(request.form.get('web_sitesi'))


def _bilgi(baslik, mesaj, kod=200, hata=False):
    return render_template('bilgi.html', baslik=baslik, mesaj=mesaj, hata=hata), kod


def _ad_uret(adres_, deneme):
    """E-postanın yerel kısmından kullanıcı adı (iç kimlik; kullanıcı e-postayla girer)."""
    yerel = adres_.split('@')[0]
    kok = re.sub(r'[^a-z0-9._-]', '', yerel).lstrip('._-')[:30] or 'kullanici'
    if len(kok) < 2:
        kok = 'kullanici'
    if deneme:
        kok = f'{kok[:25]}-{secrets.token_hex(2)}'
    return kok


# ---------------------------------------------------------------- e-posta metinleri

def _kayit_epostasi(adres_):
    bag = eposta.adres(url_for('kayit.tamamla', t=_imzaci('kayit').dumps({'e': adres_})))
    eposta.gonder(adres_, f'{UYGULAMA}: hesabınızı oluşturun', (
        f'Merhaba,\n\n{UYGULAMA} için hesap oluşturmak üzere aşağıdaki bağlantıyı açın. '
        f'Bağlantı 24 saat geçerlidir.\n\n{bag}\n\n'
        'Bu isteği siz yapmadıysanız bu e-postayı yok sayın; bağlantı açılmadıkça hesap oluşmaz.\n'))


def _zaten_var_epostasi(adres_):
    eposta.gonder(adres_, f'{UYGULAMA}: bu adresle hesabınız var', (
        f'Merhaba,\n\nBu adresle {UYGULAMA}\'de zaten bir hesabınız var. Giriş için e-posta '
        f'adresinizi ve şifrenizi kullanın:\n\n{eposta.adres(url_for("giris.giris"))}\n\n'
        f'Şifrenizi unuttuysanız:\n\n{eposta.adres(url_for("kayit.sifremi_unuttum"))}\n\n'
        'Bu isteği siz yapmadıysanız bu e-postayı yok sayın.\n'))


def _sifirlama_epostasi(k):
    t = _imzaci('sifirla').dumps({'a': k['ad'], 'e': k['eposta'], 'v': k['oturum_surum']})
    eposta.gonder(k['eposta'], f'{UYGULAMA}: şifre yenileme', (
        f'Merhaba,\n\nŞifrenizi yenilemek için aşağıdaki bağlantıyı açın. Bağlantı 1 saat '
        f'geçerlidir ve bir kez kullanılabilir.\n\n{eposta.adres(url_for("kayit.sifre_yenile", t=t))}\n\n'
        'Bu isteği siz yapmadıysanız bu e-postayı yok sayın; şifreniz değişmez.\n'))


# ---------------------------------------------------------------- kayıt

@kayit_bp.route('/kayit', methods=['GET', 'POST'])
def kayit():
    if not kayit_acik():
        abort(404)
    if mevcut_kullanici() is not None:
        return redirect(url_for('pdf.editor'))
    hata, deger = None, ''
    if request.method == 'POST':
        deger, hata = _eposta_al()
        if not hata and not _bot_mu():
            try:
                if _sinirda(deger):
                    return _bilgi('Çok fazla istek', 'Çok fazla istek yapıldı. 15 dakika sonra yeniden '
                                  'deneyin.', 429, hata=True)
                if vt.bul_eposta(deger):
                    _zaten_var_epostasi(deger)
                else:
                    _kayit_epostasi(deger)
            except (vt.DepoHatasi, eposta.EpostaHatasi) as e:
                return _bilgi('Gönderilemedi', str(e), 503, hata=True)
        if not hata:
            return _bilgi('E-postanızı açın', (
                f'{deger} adresine bir bağlantı gönderdik. Hesabınızı bağlantıyı açarak oluşturun; '
                '24 saat geçerlidir. Birkaç dakikada gelmezse istenmeyen e-posta klasörüne bakın.'))
    return render_template('kayit.html', hata=hata, eposta=deger), (400 if hata else 200)


@kayit_bp.route('/kayit/tamamla', methods=['GET', 'POST'])
def tamamla():
    if not kayit_acik():
        abort(404)
    belirtec = request.values.get('t', '')
    veri, hata = _coz('kayit', belirtec, KAYIT_SURESI)
    if hata:
        return _bilgi('Bağlantı açılamadı', hata, 400, hata=True)
    adres_ = veri['e']
    try:
        if vt.bul_eposta(adres_):
            return _bilgi('Hesap zaten açık', 'Bu adresle hesap açılmış. E-posta adresiniz ve '
                          'şifrenizle giriş yapın.')
    except vt.DepoHatasi as e:
        return _bilgi('Bir sorun var', str(e), 503, hata=True)

    form = {'gorunen_ad': ''}
    hata = None
    if request.method == 'POST':
        gorunen = ' '.join((request.form.get('gorunen_ad') or '').split())[:80]
        sifre = request.form.get('sifre') or ''
        form['gorunen_ad'] = gorunen
        if not gorunen:
            hata = 'Adınızı yazın.'
        elif sifre != (request.form.get('tekrar') or ''):
            hata = 'Şifreler aynı değil.'
        elif request.form.get('kosul') != '1':
            hata = 'Devam etmek için kullanım koşullarını kabul edin.'
        else:
            hata = sifre_kurali(sifre)
        if not hata:
            try:
                return _hesap_ac(adres_, gorunen, generate_password_hash(sifre))
            except vt.DepoHatasi as e:
                hata = str(e)
    return render_template('kayit_tamamla.html', eposta=adres_, t=belirtec, form=form, hata=hata,
                           en_az=EN_AZ_SIFRE), (400 if hata else 200)


def _hesap_ac(adres_, gorunen, ozet):
    ortam = ortam_kullanicilari()
    for deneme in range(6):
        ad = _ad_uret(adres_, deneme)
        if ad in ortam or not AD_DESENI.fullmatch(ad):
            continue
        sonuc = vt.kayit_ekle(ad, adres_, gorunen, ozet, KOSUL_SURUMU)
        if sonuc == 'eposta':
            return _bilgi('Hesap zaten açık', 'Bu adresle hesap açılmış. E-posta adresiniz ve '
                          'şifrenizle giriş yapın.')
        if sonuc == 'eklendi':
            k = vt.bul(ad)
            session.clear()
            session.permanent = True
            session['kullanici'] = ad
            session['kaynak'] = 'vt'
            session['surum'] = k['oturum_surum']
            kullanim.kaydet(ad, 'kayit')
            flash('Hesabınız açıldı. Ücretsiz planda ayda 3 belge düzenleyip indirebilirsiniz.', 'basari')
            return redirect(url_for('pdf.editor'))
    log.error('kayıt: uygun kullanıcı adı bulunamadı')
    raise vt.DepoHatasi('Hesap açılamadı. Biraz sonra yeniden deneyin.')


# ---------------------------------------------------------------- şifremi unuttum

@kayit_bp.route('/sifremi-unuttum', methods=['GET', 'POST'])
def sifremi_unuttum():
    if not sifirlama_acik():
        abort(404)
    hata, deger = None, ''
    if request.method == 'POST':
        deger, hata = _eposta_al()
        if not hata and not _bot_mu():
            try:
                if _sinirda(deger):
                    return _bilgi('Çok fazla istek', 'Çok fazla istek yapıldı. 15 dakika sonra yeniden '
                                  'deneyin.', 429, hata=True)
                k = vt.bul_eposta(deger)
                if k and k['aktif'] and k['ad'] not in ortam_kullanicilari():
                    _sifirlama_epostasi(k)
            except (vt.DepoHatasi, eposta.EpostaHatasi) as e:
                return _bilgi('Gönderilemedi', str(e), 503, hata=True)
        if not hata:
            return _bilgi('E-postanızı açın', (
                f'{deger} bir hesaba kayıtlıysa şifre yenileme bağlantısını gönderdik; 1 saat geçerlidir. '
                'Birkaç dakikada gelmezse istenmeyen e-posta klasörüne bakın. Hesabınızı yönetici '
                'açtıysa ve e-posta adresi tanımlı değilse yöneticinize başvurun.'))
    return render_template('sifremi_unuttum.html', hata=hata, eposta=deger), (400 if hata else 200)


@kayit_bp.route('/sifre-yenile', methods=['GET', 'POST'])
def sifre_yenile():
    if not sifirlama_acik():
        abort(404)
    belirtec = request.values.get('t', '')
    veri, hata = _coz('sifirla', belirtec, SIFIRLAMA_SURESI)
    if hata:
        return _bilgi('Bağlantı açılamadı', hata, 400, hata=True)
    gecersiz = ('Bağlantı kullanılmış ya da artık geçerli değil. Yeni bağlantı isteyin.')
    try:
        k = vt.bul(veri['a'])
    except vt.DepoHatasi as e:
        return _bilgi('Bir sorun var', str(e), 503, hata=True)
    if not k or not k['aktif'] or k['eposta'] != veri['e'] or k['oturum_surum'] != veri['v']:
        return _bilgi('Bağlantı açılamadı', gecersiz, 400, hata=True)

    hata = None
    if request.method == 'POST':
        sifre = request.form.get('sifre') or ''
        if sifre != (request.form.get('tekrar') or ''):
            hata = 'Şifreler aynı değil.'
        else:
            hata = sifre_kurali(sifre)
        if not hata:
            try:
                if vt.sifre_yenile(veri['a'], veri['e'], veri['v'], generate_password_hash(sifre)):
                    session.clear()
                    flash('Şifreniz yenilendi. Yeni şifrenizle giriş yapın.', 'basari')
                    return redirect(url_for('giris.giris'))
                return _bilgi('Bağlantı açılamadı', gecersiz, 400, hata=True)
            except vt.DepoHatasi as e:
                hata = str(e)
    return render_template('sifre_yenile.html', t=belirtec, hata=hata, en_az=EN_AZ_SIFRE,
                           eposta=k['eposta']), (400 if hata else 200)


# ---------------------------------------------------------------- yasal metinler

@kayit_bp.route('/kosullar')
def kosullar():
    return render_template('kosullar.html', kosul_surumu=KOSUL_SURUMU)


@kayit_bp.route('/gizlilik')
def gizlilik():
    return render_template('gizlilik.html')


@kayit_bp.route('/hakkinda')
def hakkinda():
    """Uygulama, açık kaynak ve çalışan sürümün kaynak bağlantısı (AGPL-3.0 md. 13: ağ
    üzerinden kullananlara kaynak sunulur). Alt bilginin "Hakkında" bağlantısı buraya gelir."""
    return render_template('hakkinda.html')
