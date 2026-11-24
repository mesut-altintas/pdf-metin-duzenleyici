# Plan ve aylık belge kotası (1.9)

Satışa (SaaS) hazırlığın ödemesiz ilk aşaması. Ödeme, e-postayla kayıt ve
ücretli planlar sonraki aşamalarda; bu aşamada planı yönetici elle atar.

## Kararlar (kullanıcı, 22.11.2026)

| Konu | Karar |
|---|---|
| Sayılan birim | Ayda **farklı belge**. Aynı PDF o ay yeniden düzenlenip indirilirse bir kez sayılır. |
| Ücretsiz plan | Ayda **3** belge. |
| Özellik ayrımı | **Yok.** Planlar yalnız kotayla ayrılır (metin ekle, paragraf, toplu bul-değiştir, liste herkeste açık). |
| Mevcut kullanıcılar | 1.9'dan önce açılmış her kullanıcı kendi hesabında **sınırsız (hediye)** planla başlar. |
| Yeni kullanıcı | Yönetimden eklenirken plan seçilir; varsayılan Ücretsiz. |
| Hedef pazar | Türkiye (ödeme ve fatura sonraki aşamada: iyzico Abonelik + Paraşüt önerildi, karar yok). |

## Yapı

- `planlar.py`: plan tanımları kodda. Tanınmayan plan kodu **sınırsız** sayılır ve
  log'a `ERROR` düşer (eski sürüme dönülürse ödeyen müşteri sessizce kısıtlanmasın).
- `hesap`: ödeyen birim, kullanıcıdan ayrı (ekip planında birden çok kullanıcı aynı
  hesaba bağlanacak). `kullanicilar.hesap_id` → `hesap.id`.
- `kota_sayaci (hesap_id, donem)`: ayın sayılan belge sayısı; sınır denetimi bu
  satırın kilidiyle.
- `kota_belge (hesap_id, donem, belge)`: o ay sayılmış belgelerin parmak izi
  (SHA-256'nın ilk 16 hanesi, `kullanim.parmak_izi`). Dosya adı ve içerik yok.
- Dönem İstanbul saatiyle ay (`kota.donem`; Türkiye'de yaz saati yok, UTC+3 sabit).
- Ortam değişkenindeki yöneticiler (`KULLANICILAR`) kotasız; hesapları yok.

## Akış (`/api/uret`)

1. Üretimden **önce** `kota.durum`: kota dolu ve belge bu ay sayılmamışsa motor hiç
   çalışmaz → 403, `{"kod": "kota"}`. Okunamazsa 503 ("denetlenemedi"); okunamayan
   kota ne dolu ne boş sayılır.
2. Üretim **başarılıysa** `kota.say` — tek ifade: belge işaretlenir (`kota_belge`,
   çakışırsa zaten sayılmış), sonra sayaç koşullu artırılır (`WHERE belge < sınır`).
   Eşzamanlı istekler sınırı aşamaz: koşul satır kilidinden sonra yeniden denetlenir
   (ayrı bağlantılarla sınandı: 8 eşzamanlı farklı belge → tam 3 sayıldı; 8 eşzamanlı
   aynı belge → 1 sayıldı, hepsi izinli).
3. Sınır o arada dolduysa işaret silinir, dosya gönderilmez (403).
4. Sayma yazılamazsa (bağlantı) dosya **yine gönderilir**, log'a `ERROR` düşer:
   kullanıcı yaptığı işi kaybetmez; yazma yeniden gönderilmez.

- Başarısız üretim sayılmaz.
- **Önizleme de üretimdir**: tam PDF tarayıcıya iner, önizlemeden indirilebilir.
- Başarılı cevapta `X-Kota: <kullanılan>/<sınır>` başlığı; tarayıcı sayacı günceller.
- `/api/analiz` cevabında `kota` (kullanılan, sınır, izin, mesaj): dolu kotada belge
  açılınca uyarı düğmelerin üstünde çıkar; değişiklikler yerinde kalır, plan
  değişirse aynı sayfadan indirilebilir.

## Bilinen dar pencere

Kota dolu ve aynı yeni belge iki sekmeden aynı anda gönderilirse: biri işareti koyup
sınıra takılır ve işareti siler; öbürü işareti "zaten sayılmış" görüp izin alabilir.
Ön denetim ikisini de üretimden önce durdurduğu için bu ancak kota iki isteğin arasında
dolarsa olur; sonuç kullanıcı lehine bir belge.

## Kapatma

`KOTA_KAPALI=1`: kota hiç okunmaz, sayılmaz; ekranda sayaç çizilmez, Yönetim'de
"Kota kapalı" yazar. Yayın beklemeden (ortam değişkeni + yeniden yayın).

## Geçiş

`kullanicilar._GECIS_SQL` her soğuk başlangıçta koşar: hesabı olmayan kullanıcıya
tek ifadede numara verir ve o numarayla `hediye` hesap açar. Hesapsız kullanıcı yoksa
hiçbir şey yazmaz; eşzamanlı başlangıçlar çift hesap açmaz (sınandı: 6 bağlantı,
200 kullanıcı → 200 hesap).
