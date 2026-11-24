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
- **Kayıt herkese açılmadan önce** bir insan doğrulaması (ör. Cloudflare Turnstile)
  eklenmeli: `/kayit` başkasının adresine e-posta göndertmek için kullanılabilir; sınır
  bunu yavaşlatır ama durdurmaz.

## Ortam değişkenleri

| Ad | |
|---|---|
| `RESEND_API_KEY` | Resend anahtarı (yalnız gönderme izni). Yoksa şifremi unuttum ve kayıt kapalı (404, bağlantı çizilmez). |
| `EPOSTA_GONDEREN` | `PDF Metin Düzenleyici <pdf@altintas.biz>` |
| `UYGULAMA_ADRESI` | `https://pdf.altintas.biz` (23.11.2026; önce vercel.app adresiydi, bkz. "Alan adı") |
| `KAYIT_ACIK` | `1` ise kayıt açık. |

Testlerde gerçek gönderim kapalı (`conftest._gercek_eposta_yok`): `eposta._ilet`'i
değiştirmeyen test e-posta göndermeye kalkarsa düşer.

## Alan adı (23.11.2026)

İlk denemede şifre yenileme e-postası Resend'de `delivered` göründü ama şirket adresinin gelen
kutusuna düşmedi; bağlantı ~20 sn sonra iki kez otomatik açıldı (kurumsal bağlantı tarayıcısı
izi). Olası neden (tahmin): gönderen `altintas.biz`, bağlantı `*.vercel.app` — oltalama
filtrelerinin şüphelendiği birleşim. Uygulama `pdf.altintas.biz`'e alındı, `UYGULAMA_ADRESI`
buna çevrildi: bağlantı ile gönderen aynı alan adında.

Bağlantıyı açmak (GET) belirteci tüketmez; tarayıcıların ön açması şifre yenilemeyi bozmaz.

## Açık konular

- Yasal metinlerin köşeli parantezli yerleri (avukat) ve satıcı kimliği.
- Hesap kapatma / silme talebi (KVKK m. 11): usul ve saklama süresi kararı bekliyor.
- Kayıt açılmadan önce insan doğrulaması.
- Ürün alan adı alınınca gönderen ve uygulama adresi.
- Vercel istek logu bağlantıyı sorgu dizgisiyle yazar (belirteç ve içindeki adres 1 saat
  logda okunur); belirteci adres çubuğunun `#` kısmına almak düşünülebilir.
