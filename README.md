# PDF Metin Düzenleyici

Yüklenen PDF'in sayfalarını gösterir. Değiştirilecek metne tıklanır, yeni hâli
yazılır, sonuç asıl dosyanın görünümünde indirilir. **Yalnız seçilen metnin
değişen harfleri** yeniden yazılır; diğer metin, resim, çizgi, bağlantı ve
açıklamalara dokunulmaz. Uygulama bunu her üretimde ayrıca denetler; dokunulmuş
bir metin bulursa dosyayı üretmez.

PDF hiçbir yere kaydedilmez: sunucuda istek belleğinde işlenir, sonuç
tarayıcıdan indirilir.

Canlı: https://pdf.altintas.biz · Flask + [PyMuPDF](https://github.com/pymupdf/PyMuPDF) · Arayüz Türkçe.

Özellikler: metni yerinde değiştirme, toplu bul ve değiştir, yeni metin ekleme,
paragraf düzenleme, değişiklik listesini kaydet / yükle, telefonda kullanım (PWA),
kullanıcı yönetimi, aylık belge kotası, e-postayla kayıt ve şifre yenileme.

## Yerelde çalıştırma

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # SECRET_KEY ve KULLANICILAR'ı doldurun
.venv/bin/python sifre_ozeti.py ahmet    # KULLANICILAR için özet üretir
set -a; . ./.env; set +a; .venv/bin/python app.py   # http://127.0.0.1:5050
```

Testler:

```bash
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

## Ortam değişkenleri

| Ad | Açıklama |
|---|---|
| `SECRET_KEY` | Oturum çerezini imzalar. Vercel'de zorunlu; yoksa uygulama açılmaz. |
| `KULLANICILAR` | Kalıcı yöneticiler: `ad=özet,ad2=özet`. Özet `sifre_ozeti.py` ile üretilir; şifre hiçbir yere yazılmaz. Sayfadan değiştirilemezler; veritabanına ulaşılamasa da girebilirler. |
| `DATABASE_URL` | Neon Postgres. Diğer kullanıcılar burada; yöneticiler `/yonetim` sayfasından ekler, şifre sıfırlar, pasife alır, yönetici yapar, hesabı kapatıp anonimleştirir. Boşsa yalnız `KULLANICILAR` girer. |
| `KOTA_KAPALI` | `1` ise aylık belge kotası kapalı: sayılmaz, ekranda sayaç çizilmez (`docs/plan-kota.md`). |
| `RESEND_API_KEY`, `EPOSTA_GONDEREN`, `UYGULAMA_ADRESI` | E-posta gönderimi (Resend). Üçü de doluysa "Şifremi unuttum" açık; bağlantılar `UYGULAMA_ADRESI`'nden kurulur (`docs/kayit.md`). |
| `KAYIT_ACIK` | `1` ise herkes e-postayla kayıt olabilir (e-posta da tanımlı olmalı). Varsayılan kapalı. |
| `RECAPTCHA_SITE_KEY`, `RECAPTCHA_SECRET` | "Ben robot değilim" (Google reCAPTCHA v2). İkisi de doluysa kayıt ve şifremi unuttum formlarında kutu çıkar; `ROBOT_DOGRULAMA_KAPALI=1` kapatır. |

## Vercel'e yayın

Flask uygulaması `app.py`'deki `app` nesnesi; Vercel bunu kendisi tanır.
Statik dosyalar `public/static/` altında (Vercel CDN'den sunar).

```bash
vercel link
vercel env add SECRET_KEY production
vercel env add KULLANICILAR production
vercel deploy --prod
```

Sınırlar: Vercel işlevinin istek ve yanıt gövdesi 4,5 MB olduğundan dosya en
fazla 4,4 MB (4.400.000 bayt) ve 40 sayfa olabilir.

## Lisans

Copyright © 2026 Mesut Altıntaş

Bu program özgür yazılımdır: Free Software Foundation'ın yayımladığı **GNU Affero
Genel Kamu Lisansı sürüm 3** (AGPL-3.0) ya da (tercihinize göre) daha sonraki bir
sürümü koşullarıyla dağıtabilir ve değiştirebilirsiniz. Program yararlı olacağı
umuduyla, hiçbir garanti olmadan dağıtılır. Lisansın tam metni: [LICENSE](LICENSE).

AGPL, programı ağ üzerinden sunan herkesin, kullanıcılarına **çalışan sürümün**
kaynak kodunu sunmasını ister. Her sayfanın alt bilgisindeki "Hakkında" sayfası
çalışan sürümün koduna bağlantı verir (`surum.py`: `KAYNAK_DEPO`, `v<SURUM>` etiketi). Kodu kendi sunucunuzda
çalıştırıyorsanız bağlantıyı kendi deponuza çevirin.

Kullanılan PDF kütüphanesi PyMuPDF (Artifex Software) de AGPL-3.0 lisanslıdır.

## Sürümler

Sürüm `surum.py`'de; her yayından önce bir artırılır, buraya yazılır ve açık depoya
`v<SURUM>` etiketiyle gönderilir. Ekranın altında görünür.

| Sürüm | Tarih | Değişiklik |
|---|---|---|
| 1.15 | 24.11.2026 | Kayıt ve şifremi unuttum formlarında "Ben robot değilim" (reCAPTCHA v2); aydınlatma metnine Google aktarımı |
| 1.14 | 24.11.2026 | Alt bilgi sadeleşti: kaynak bağlantısı yeni "Hakkında" sayfasına taşındı |
| 1.13 | 24.11.2026 | Kullanım koşulları ve aydınlatma metni yeniden yazıldı (taslak); hesabı kapatıp kişisel veriyi anonimleştirme; kullanım kayıtlarında 2 yıl sınırı; kayıtta 18 yaş beyanı |
| 1.12 | 24.11.2026 | Kaynak kod AGPL-3.0 ile açıldı: alt bilgide çalışan sürümün kaynak bağlantısı, kullanım koşullarında açık kaynak notu |
| 1.11 | 23.11.2026 | Adres pdf.altintas.biz (e-postadaki bağlantılar gönderenle aynı alan adında); eski vercel.app adresi de açık |
| 1.10 | 22.11.2026 | E-postayla kayıt (kapalı, `KAYIT_ACIK` ile açılır), şifremi unuttum, e-postayla giriş, yönetimde kullanıcıya e-posta, kullanım koşulları ve aydınlatma metni taslağı |
| 1.9 | 22.11.2026 | Plan ve aylık belge kotası: hesap tablosu, ücretsiz plan ayda 3 farklı belge, mevcut kullanıcılara sınırsız hediye plan, Yönetim'de plan seçimi ve bu ayın sayısı |
| 1.8 | 22.11.2026 | Giriş (yükleme) sayfasının yeni görünümü: tek odaklı yükleme kartı, gizlilik notu, özellik kartları; telefonda tam genişlik düğme |
| 1.7 | 22.11.2026 | Paragraf düzenleme (yeniden dizme, hizalama, taşmada küçültme/uzatma, kalın sözcükleri koruma) |
| 1.6 | 22.11.2026 | Yeni metin ekleme (belgenin ya da standart yazı tipiyle, sürükleyerek konum, çok satır) |
| 1.5 | 22.11.2026 | Toplu bul ve değiştir (Türkçe büyük/küçük harf, tam sözcük, eşleşme seçimi, geri al) |
| 1.4 | 22.11.2026 | Değişiklik listesini kaydet / yükle (aynı şablona yeniden uygulama) |
| 1.3 | 22.11.2026 | Kullanım ölçümü (Yönetim > Kullanım), sürüm ve telif alt bilgisi |
| 1.2 | 22.11.2026 | Mobil kullanım, yüklenebilir uygulama (PWA), simge; boyut sınırı Vercel'in ölçülen sınırına göre |
| 1.1 | 22.11.2026 | Yönetim sayfası; kullanıcılar Neon'da |
| 1.0 | 22.11.2026 | İlk yayın: PDF metin düzenleme, giriş |

## Belgeler

- [docs/motor.md](docs/motor.md): motorun yöntemi, dayandığı ölçümler, bilinen sınırlar.
- [docs/plan-kota.md](docs/plan-kota.md): planlar, aylık belge kotası, kararlar ve akış.
- [docs/kayit.md](docs/kayit.md): e-postayla kayıt, şifremi unuttum, kötüye kullanım önlemleri.
