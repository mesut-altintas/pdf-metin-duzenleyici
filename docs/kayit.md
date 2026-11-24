# E-postayla kayıt, şifremi unuttum, e-postayla giriş (1.10)

SaaS hazırlığının ikinci aşaması (ilki: `docs/plan-kota.md`). Ödeme sonraki aşamada.

## Kararlar (kullanıcı, 22.11.2026)

| Konu | Karar |
|---|---|
| Gönderen | altintas.biz (Resend'de doğrulanmış tek alan adı); ürün alan adı alınınca `EPOSTA_GONDEREN` + `UYGULAMA_ADRESI` değişir. |
| Doğrulama | Doğrulamadan giriş yok. |
| Kayıt sayfası | Kapalı; `KAYIT_ACIK=1` ile açılır (PyMuPDF lisansı netleşene kadar kullanıcı sayısı büyütülmez). Şifremi unuttum açık. |
| Yasal metinler | Taslak uygulamanın gerçek davranışına göre yazıldı (`/kosullar`, `/gizlilik`); "TASLAK — avukat onayı bekliyor" yazar. Köşeli parantezli yerler (satıcı bilgileri, saklama süresi, yurt dışı aktarım dayanağı, sorumluluk sınırı) doldurulmadan kayıt açılmamalı. |

## Akış

**Kayıt önce e-posta:** `/kayit` yalnız adres ister ve 24 saat geçerli bağlantı gönderir.
Bağlantı (`/kayit/tamamla`) açılınca ad, şifre ve koşul onayı istenir; hesap Ücretsiz
planla açılır, oturum başlar. Sonuç:
- "Doğrulanmamış hesap" durumu yok: hesap ancak adresin sahibi bağlantıyı açınca oluşur.
- Şifreyi başkası önceden belirleyemez (önce şifre isteyen düzende, başkasının adresine
  kaydolan kişi kendi şifresini koyar; adres sahibi bağlantıya tıklarsa o hesap açılır).
- Aynı bağlantı ikinci kez kullanılırsa "hesap zaten açık" der; e-posta benzersiz dizini
  çift hesabı veritabanında da engeller.

**Adres ele verilmez:** kayıt ve şifremi unuttum ekranı, adres kayıtlı olsun olmasın aynı
cevabı verir. Kayıtlı adrese "bu adresle hesabınız var" e-postası (giriş + şifremi unuttum
bağlantısı) gider; kayıtsız adrese şifre yenileme e-postası gitmez.

**Şifre yenileme bağlantısı tek kullanımlık:** belirteçte kullanıcı adı, adres ve oturum
sürümü var; yazım `WHERE oturum_surum = <bağlantıdaki> AND eposta = <bağlantıdaki> AND aktif`
koşullu UPDATE'iyle (yazınca sürüm artar: bağlantı ve açık oturumlar düşer). Yönetici
adresi değiştirirse eski bağlantı geçersiz. Süre 1 saat.

**Belirteç:** `itsdangerous` (SECRET_KEY ile imzalı, amaç başına tuz); veritabanında tablo yok.
SECRET_KEY değişirse gönderilmiş bütün bağlantılar geçersiz olur.

**Bağlantı adresi:** `UYGULAMA_ADRESI`'nden kurulur, istekteki Host'tan değil (Host
başlığı değiştirilerek şifre yenileme bağlantısı saldırganın sunucusuna yönlendirilemesin;
testi var).

**E-postayla giriş:** giriş kutusu kullanıcı adı ya da e-posta alır (küçük harfe çevrilir).
Kendi kaydolanın kullanıcı adı adresin yerel kısmından üretilir (iç kimlik; çakışırsa ek
alır); "ekleyen" sütununda `(kayıt)` yazar.

## Kötüye kullanım

- İstek sınırı (`giris_denemesi`, 15 dk): istemci adresi başına 10, e-posta başına 3 istek
  (kayıt ve şifremi unuttum birlikte sayılır). E-posta tabloya parmak iziyle yazılır.
- Giriş denemesi anahtarındaki ad / e-posta da artık parmak iziyle (1.10'dan önce açıktı).
- Bot tuzağı: ekran dışındaki "Web sitesi" alanı doluysa ekran aynı, e-posta gitmez.
- **"Ben robot değilim" (1.15):** kayıt ve şifremi unuttum formlarında Google reCAPTCHA v2
  (`robot.py`, düzen portalın `lfa/robot.py`'sinden; kullanıcı Turnstile yerine reCAPTCHA'yı
  seçti, 24.11.2026). Anahtar yoksa kutu çizilmez, doğrulama atlanır. Geçmezse e-posta gitmez
  ve istek sınırı sayılmaz. Google'a ulaşılamazsa ya da gizli anahtar hatalıysa istek işlenir,
  ERROR loglanır (gerçek kullanıcı durmasın; istek sınırı yine geçerli). Kapatma
  `ROBOT_DOGRULAMA_KAPALI=1`. Anahtarın alan adı listesinde `pdf.altintas.biz` olmalı.
- **Kayıt herkese açılmadan önce** reCAPTCHA anahtarları Vercel'e girilmeli: `/kayit`
  başkasının adresine e-posta göndertmek için kullanılabilir; sınır bunu yavaşlatır ama
  durdurmaz, robot doğrulaması durdurur.

## Ortam değişkenleri

| Ad | |
|---|---|
| `RESEND_API_KEY` | Resend anahtarı (yalnız gönderme izni). Yoksa şifremi unuttum ve kayıt kapalı (404, bağlantı çizilmez). |
| `EPOSTA_GONDEREN` | `PDF Metin Düzenleyici <pdf@altintas.biz>` |
| `UYGULAMA_ADRESI` | `https://pdf.altintas.biz` (23.11.2026; önce vercel.app adresiydi, bkz. "Alan adı") |
| `KAYIT_ACIK` | `1` ise kayıt açık. |
| `RECAPTCHA_SITE_KEY`, `RECAPTCHA_SECRET` | "Ben robot değilim" (reCAPTCHA v2). İkisi de doluysa kayıt ve şifremi unuttum formlarında kutu çıkar. |
| `ROBOT_DOGRULAMA_KAPALI` | `1` ise robot doğrulaması kapalı (anahtar dolu olsa da). |

Testlerde gerçek gönderim kapalı (`conftest._gercek_eposta_yok`): `eposta._ilet`'i
değiştirmeyen test e-posta göndermeye kalkarsa düşer.

## Alan adı (23.11.2026)

İlk denemede şifre yenileme e-postası Resend'de `delivered` göründü ama şirket adresinin gelen
kutusuna düşmedi; bağlantı ~20 sn sonra iki kez otomatik açıldı (kurumsal bağlantı tarayıcısı
izi). Olası neden (tahmin): gönderen `altintas.biz`, bağlantı `*.vercel.app` — oltalama
filtrelerinin şüphelendiği birleşim. Uygulama `pdf.altintas.biz`'e alındı, `UYGULAMA_ADRESI`
buna çevrildi: bağlantı ile gönderen aynı alan adında.

Bağlantıyı açmak (GET) belirteci tüketmez; tarayıcıların ön açması şifre yenilemeyi bozmaz.

## Hesap kapatma ve saklama (1.13)

Aydınlatma metni md. 7'nin kodda karşılığı:

- **Hesap kapatma:** kullanıcı [İLETİŞİM E-POSTASI]'na yazar, yönetici Yönetim'de "Kapat ve
  anonimleştir"e basar (`kullanicilar.kapat_anonimlestir`). Tek ifadede: kullanıcı adı
  `silinmis-<12 hane>` olur; ad soyad, e-posta, şifre özeti, son giriş silinir, giriş kapanır,
  oturum düşer; aynı ad `hesap`, `kullanim_olayi` ve `yonetim_kaydi`'nda da yeni kimlikle
  değişir. Sayılar (kullanım, kota) kalır, kişiyle ilişkisi kalmaz. Adres boşa çıkar, yeniden
  kayıt olunabilir. Kapatılmış hesap yönetimden değiştirilemez. Metin "en geç 30 gün" der;
  işlem anında yapılır, süre başvurunun karşılanması içindir.
- 1.13'e kadar "Sil" yalnız kullanıcı satırını siliyordu; ad kayıtlarda kalıyordu. `vt.sil`
  kodda duruyor, hiçbir uçtan çağrılmıyor.
- **Saklama sınırı:** `kullanim_olayi`'nda 2 yıldan eski satırlar her soğuk başlangıçta
  silinir (`kullanicilar.kullanim_saklama_siniri`, `KULLANIM_SAKLAMA_YIL`).
- Kapsam dışı (metin bunları sağlayıcıya bırakır): Resend'deki e-posta kayıtları, Vercel'in
  istek kayıtları.

## Açık konular

- Yasal metinlerin köşeli parantezli yerleri (avukat) ve satıcı kimliği.
- Kayıt açılmadan önce reCAPTCHA anahtarları (Google'da `pdf.altintas.biz` için).
- Ürün alan adı alınınca gönderen ve uygulama adresi.
- Vercel istek logu bağlantıyı sorgu dizgisiyle yazar (belirteç ve içindeki adres 1 saat
  logda okunur); belirteci adres çubuğunun `#` kısmına almak düşünülebilir.
