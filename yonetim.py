"""Yönetim sayfası: kullanıcı ekleme, şifre sıfırlama, pasife alma, yönetici
atama, kalıcı silme. Yalnız yöneticiler.

Kurallar (her uç sunucuda ayrıca uygular; düğmeyi gizlemek koruma değildir):
  * Yönetici kendini pasife alamaz, silemez, yöneticiliğini kaldıramaz.
  * Ortam değişkenindeki yöneticiler burada değiştirilemez; o adla kayıt açılmaz.
  * Üretilen şifre yalnız bir kez, sonuç sayfasında gösterilir (çerezde, flash'ta,
    yönlendirmede taşınmaz).
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

import kota
import kullanicilar as vt
import kullanim
import planlar
from giris import (AD_DESENI, EPOSTA_DESENI, ortam_kullanicilari, sifre_kurali, sifre_uret,
                   yonetici_gerekli)

yonetim_bp = Blueprint('yonetim', __name__, url_prefix='/yonetim')

KAYIT_SINIRI = 50
AD_KURALI = 'Kullanıcı adı 2–40 karakter: küçük harf (Türkçe harf olmadan), rakam, nokta, tire, alt çizgi.'
EPOSTA_KURALI = 'Geçerli bir e-posta adresi yazın (Türkçe harf olmadan).'


def _ciz(hata=None, form=None, kod=200):
    kullanicilar, kayitlar, okuma_hatasi = [], [], None
    if vt.tanimli():
        try:
            kullanicilar = vt.liste(kota.donem())
            kayitlar = vt.kayitlar(KAYIT_SINIRI)
        except vt.DepoHatasi as e:
            okuma_hatasi = str(e)
    return render_template(
        'yonetim.html', ortam=sorted(ortam_kullanicilari()), kullanicilar=kullanicilar,
        kayitlar=kayitlar, kayit_siniri=KAYIT_SINIRI, vt_var=vt.tanimli(),
        okuma_hatasi=okuma_hatasi, hata=hata, form=form or {}, planlar=planlar.PLANLAR,
        plan_bul=planlar.bul, varsayilan_plan=planlar.VARSAYILAN, gecis_plan=planlar.GECIS,
        kota_kapali=kota.kapali()), kod


def _hedef(ad):
    """İşlem yapılabilecek bir veritabanı kullanıcısı mı? (hata metni | None)"""
    if not vt.tanimli():
        return 'Veritabanı tanımlı değil.'
    if not AD_DESENI.fullmatch(ad):
        return 'Kullanıcı bulunamadı.'
    if ad in ortam_kullanicilari():
        return 'Ortam değişkenindeki yönetici bu sayfadan değiştirilemez.'
    return None


def _sonuc(hata, basari):
    if hata:
        flash(hata, 'hata')
    else:
        flash(basari, 'basari')
    return redirect(url_for('yonetim.liste'))


@yonetim_bp.route('/')
@yonetici_gerekli
def liste():
    return _ciz()


@yonetim_bp.route('/ekle', methods=['POST'])
@yonetici_gerekli
def ekle():
    ben = g.kullanici_nesnesi
    ad = (request.form.get('ad') or '').strip().lower()
    gorunen = ' '.join((request.form.get('gorunen_ad') or '').split())[:80]
    sifre = request.form.get('sifre') or ''
    yonetici = request.form.get('yonetici') == '1'
    degistirmeli = request.form.get('degistirmeli') == '1'
    plan = request.form.get('plan') or planlar.VARSAYILAN
    adres = (request.form.get('eposta') or '').strip().lower() or None
    form = {'ad': ad, 'gorunen_ad': gorunen, 'yonetici': yonetici, 'degistirmeli': degistirmeli,
            'plan': plan, 'eposta': adres or ''}
    uretildi = not sifre
    if uretildi:
        sifre = sifre_uret()

    if not vt.tanimli():
        hata = 'Veritabanı tanımlı değil; kullanıcı eklenemez.'
    elif plan not in planlar.PLANLAR:
        hata = 'Geçersiz plan.'
    elif adres and not EPOSTA_DESENI.fullmatch(adres):
        hata = EPOSTA_KURALI
    elif not AD_DESENI.fullmatch(ad):
        hata = AD_KURALI
    elif ad in ortam_kullanicilari():
        hata = 'Bu ad ortam değişkenindeki bir yöneticinin; başka bir ad seçin.'
    else:
        hata = sifre_kurali(sifre)
    if not hata:
        try:
            if vt.ekle(ad, gorunen, generate_password_hash(sifre), yonetici, degistirmeli, ben.ad, plan,
                       adres):
                return render_template('yonetim_sonuc.html', baslik='Kullanıcı eklendi', ad=ad,
                                       sifre=sifre if uretildi else None, degistirmeli=degistirmeli)
            hata = (f'“{ad}” adında bir kullanıcı zaten var.' if vt.bul(ad)
                    else 'Bu e-posta başka bir kullanıcıda.')
        except vt.DepoHatasi as e:
            hata = str(e)
    return _ciz(hata=hata, form=form, kod=400)


@yonetim_bp.route('/<ad>/sifre', methods=['POST'])
@yonetici_gerekli
def sifre_sifirla(ad):
    ben = g.kullanici_nesnesi
    hata = _hedef(ad)
    if not hata and ad == ben.ad:
        hata = 'Kendi şifrenizi “Şifre” sayfasından değiştirin.'
    sifre = request.form.get('sifre') or ''
    uretildi = not sifre
    if uretildi:
        sifre = sifre_uret()
    degistirmeli = request.form.get('degistirmeli') == '1'
    hata = hata or sifre_kurali(sifre)
    if not hata:
        try:
            if vt.sifre_ata(ad, generate_password_hash(sifre), degistirmeli, ben.ad, 'şifresi sıfırlandı'):
                return render_template('yonetim_sonuc.html', baslik='Şifre sıfırlandı', ad=ad,
                                       sifre=sifre if uretildi else None, degistirmeli=degistirmeli)
            hata = 'Kullanıcı bulunamadı.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, '')


@yonetim_bp.route('/<ad>/aktif', methods=['POST'])
@yonetici_gerekli
def aktif(ad):
    ben = g.kullanici_nesnesi
    deger = request.form.get('deger') == '1'
    hata = _hedef(ad)
    if not hata and ad == ben.ad and not deger:
        hata = 'Kendinizi pasife alamazsınız.'
    if not hata:
        try:
            if not vt.aktiflik(ad, deger, ben.ad):
                hata = 'Değişiklik yapılmadı: kullanıcı yok ya da zaten bu durumda.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, f'“{ad}” ' + ('etkinleştirildi.' if deger else 'pasife alındı; açık oturumu kapandı.'))


@yonetim_bp.route('/<ad>/yonetici', methods=['POST'])
@yonetici_gerekli
def yonetici(ad):
    ben = g.kullanici_nesnesi
    deger = request.form.get('deger') == '1'
    hata = _hedef(ad)
    if not hata and ad == ben.ad and not deger:
        hata = 'Kendi yöneticiliğinizi kaldıramazsınız.'
    if not hata:
        try:
            if not vt.yoneticilik(ad, deger, ben.ad):
                hata = 'Değişiklik yapılmadı: kullanıcı yok ya da zaten bu durumda.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, f'“{ad}” ' + ('yönetici yapıldı.' if deger else 'artık yönetici değil.'))


@yonetim_bp.route('/<ad>/plan', methods=['POST'])
@yonetici_gerekli
def plan_degistir(ad):
    ben = g.kullanici_nesnesi
    plan = request.form.get('plan') or ''
    hata = _hedef(ad)
    if not hata and plan not in planlar.PLANLAR:
        hata = 'Geçersiz plan.'
    if not hata:
        try:
            if not vt.plan_ata(ad, plan, ben.ad):
                hata = 'Değişiklik yapılmadı: kullanıcı yok ya da planı zaten bu.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, '' if hata else f'“{ad}” planı artık: {planlar.PLANLAR[plan].ad}.')


@yonetim_bp.route('/<ad>/eposta', methods=['POST'])
@yonetici_gerekli
def eposta_degistir(ad):
    ben = g.kullanici_nesnesi
    adres = (request.form.get('eposta') or '').strip().lower() or None
    hata = _hedef(ad)
    if not hata and adres and not EPOSTA_DESENI.fullmatch(adres):
        hata = EPOSTA_KURALI
    if not hata:
        try:
            if not vt.eposta_ata(ad, adres, ben.ad):
                hata = 'Değişiklik yapılmadı: kullanıcı yok ya da adres aynı.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, f'“{ad}” e-postası ' + ('kaydedildi.' if adres else 'kaldırıldı.'))


@yonetim_bp.route('/<ad>/sil', methods=['POST'])
@yonetici_gerekli
def sil(ad):
    ben = g.kullanici_nesnesi
    hata = _hedef(ad)
    if not hata and ad == ben.ad:
        hata = 'Kendinizi silemezsiniz.'
    if not hata:
        try:
            if not vt.sil(ad, ben.ad):
                hata = 'Kullanıcı bulunamadı.'
        except vt.DepoHatasi as e:
            hata = str(e)
    return _sonuc(hata, f'“{ad}” kalıcı olarak silindi.')


@yonetim_bp.route('/kullanim')
@yonetici_gerekli
def kullanim_sayfasi():
    try:
        gun = int(request.args.get('gun', 30))
    except ValueError:
        gun = 30
    if gun not in kullanim.DONEMLER:
        gun = 30
    satirlar, olaylar, farkli, hata = [], [], 0, None
    if vt.tanimli():
        try:
            satirlar = kullanim.ozet(gun)
            olaylar = kullanim.olaylar(gun)
            farkli = kullanim.farkli_belge(gun)
        except vt.DepoHatasi as e:
            hata = str(e)
    return render_template(
        'kullanim.html', gun=gun, donemler=kullanim.DONEMLER, satirlar=satirlar,
        toplam=kullanim.toplam(satirlar), farkli=farkli, olaylar=olaylar,
        olay_siniri=kullanim.OLAY_SINIRI, turler=kullanim.TURLER, hatalar=kullanim.HATALAR,
        vt_var=vt.tanimli(), hata=hata)
