# Word / Excel'e çevirme (1.18)

Kod: `pdf_duzenle/cevir.py` (Flask'sız), uç `POST /api/cevir` (`pdf_duzenle/rotalar.py`),
arayüz `public/static/editor.js` ("Word / Excel'e çevirme" bölümü). Testler
`tests/test_cevir.py`, uç `tests/test_uygulama.py`, kota `tests/test_kota.py`.

## Kararlar (kullanıcı, 26.11.2026)

| Konu | Karar |
|---|---|
| Kota | Çevirme aylık belge hakkından sayılır, üretimle aynı parmak iziyle: aynı belgeyi o ay düzenleyip hem Word'e hem Excel'e çevirmek tek hak. Başarısız çevirme sayılmaz; dolu kotada çevirme hiç çalışmaz. |
| Excel düzeni | Tek çalışma sayfası ("Tablolar"). Sayfalara bölünmüş aynı tablo birleşir, tekrarlanan başlık satırı atlanır. Tutar, tarih ve yüzde gerçek sayı / tarih hücresi olur. |
| Değişiklikler | Ekranda ne görünüyorsa o çevrilir: değişiklik varsa sunucu önce PDF'i üretir (`motor.uygula`), sonra çevirir. Değişiklik yoksa yüklenen dosya olduğu gibi çevrilir. |
| Paket boyutu | Büyük paket (aşağıda) ve soğuk açılış bedeli kabul edildi; yavaşlık hissedilirse OpenCV'siz hafif paket ya da ayrı servis sonra konuşulur. |

## Kütüphane seçimi (ölçüm, 26.11.2026)

- **pdf2docx 0.5.13** (Artifex, **MIT**; eski sürümler AGPL sanılıyordu, paketteki
  LICENSE MIT). Sayfa düzenini, paragrafları, resimleri ve tabloları (çizgili + çizgisiz)
  çözümler; Word dosyasını python-docx ile yazar.
- **Excel de pdf2docx'in tablo bulucusundan.** PyMuPDF'in kendi `find_tables()`'ı
  çizgili faturada doğru, ama yalnız yatay çizgili banka ekstresinde ya hiç bulmuyor
  (`lines`) ya da sözcükleri hücrelere bölüyor (`text`: "MAAŞ ÖDE | MESİ"). pdf2docx aynı
  ekstrede 110 hareketi hücre hücre doğru buldu.
- pdf2docx OpenCV ve NumPy'ı zorunlu çeker (OpenCV yalnız vektör çizimleri resme
  kırpma ve resim döndürmede kullanılıyor). OpenCV 4.14 seçildi: Linux paketi açılmış
  153 MB (5.1: 172 MB).

## Vercel ölçümleri (önizleme yayını, fra1, 1 vCPU)

| Ölçüm | Değer |
|---|---|
| Paket | 325 MB → Vercel "exceeds the standard size; optimizing dependencies" kipine geçiyor (işlev 68 MB + bağımlılıklar çalışma anında). main: 81 MB, bu kip yok. |
| Soğuk açılış, giriş sayfası | main 1,8–2,2 sn → çevirmeli 3,4–3,6 sn (ikişer örnek). Sıcakta fark yok (0,3 sn). pdf2docx yalnız ilk çevirmede yüklenir; fark paketin kendisinden. |
| 3 sayfalık ekstre | Word 6,8 sn (ilk yükleme dahil), Excel 3,1 sn |
| 30 sayfalık ekstre (1.100 satır) | Word 61 sn, Excel 27 sn. Bu Mac'te 21 / 10 sn: sunucu ~3 kat yavaş. Word'de yazım (python-docx) çözümleme kadar sürüyor. |
| Düz metinli iki sütunlu rapor | Word 1 sn |

60 sn'lik işlev sınırında 30 sayfalık Word 504 aldı → `vercel.json` `maxDuration`
300 (projenin Fluid varsayılanı da 300). `cevir.SURE_BUTCESI` = 120 sn çözümleme
bütçesi: aşılırsa yarım dosya gönderilmez, "süre sınırı" hatası (kod `sure`) döner.
Sayfa sınırı düzenleyiciyle aynı (40).

Önizlemede ölçmek için: ortam değişkenleri yalnız Production'da, önizlemede giriş için
`vercel deploy --env KULLANICILAR=… --env SECRET_KEY=…` (yalnız o yayına). Önizlemeler
Vercel girişiyle korunuyor; `vercel curl` projede kalıcı bir "Protection Bypass for
Automation" anahtarı açar — açılırsa ölçümden sonra iptal edilir
(`PATCH /v1/projects/<id>/protection-bypass` `{"revoke": …}`).

## Word

- Bağlantı: pdf2docx bağlantıyı `w:r > w:hyperlink > w:r` diye yazıyor (OOXML şemasına
  aykırı). `_baglanti_ekle` yerine geçer: bağlantı paragrafın doğrudan çocuğu.
- Yalnız `http(s)` ve `mailto:` bağlantısı taşınır; `file:`, UNC, `javascript:` gibi
  adreslerin metni düz yazı kalır (Word'de tıklanınca zarar verebilir).
- pdf2docx'in sayfa döngüsü çözümlenemeyen sayfayı **sessizce** atlıyor; kendi döngümüz
  var: atlanan sayfa uyarıya yazılır ("Şu sayfalar çevrilemedi ve dosyada yok: 2."),
  hepsi atlanırsa hata.
- Seçilebilir metni olmayan (taranmış) PDF: uyarı — sayfalar resim olarak geçer, OCR yok.
- python-docx şablonunun yazar / açıklama ("python-docx") alanları boşaltılır.
- pdf2docx yüklenirken kök kaydediciye `basicConfig(level=INFO)` uyguluyor; yükleme
  anında köke geçici boş işleyici konur, uygulamanın log düzeyi değişmez.

### Chrome / web sayfası PDF'leri (1.19)

Ölçüm, 26.11.2026, Chrome'dan yazdırılmış 22 sayfalık bir web sayfası:

- Sözcük arası boşluk ayrı bir parçada (Skia boşluğu ayrı bir Type3 yazı tipinden çiziyor);
  pdf2docx yalnız boşluk olan parçayı atıyordu → "sözcüklerbirbirineyapışık". Bir metin parçasının
  ardındaki boşluk parçası artık korunur (`Spans.restore` yaması).
- Yazı tipleri Type3: MuPDF adı "Type3 (9 0 R)", kalın / eğik bayrağı boş. Aile, kalınlık
  (`FontWeight` ≥ 600) ve eğim `FontDescriptor`'dan okunur (`RawPageFitz._preprocess_text`
  yaması). Bu belgede 151 Type3 yazı tipi: IBM Plex Sans 400 / 700, Archivo SemiBold.
- Word'e yazılan ad: yaygın yazı tipiyse kendisi ("Calibri-Bold", "TimesNewRomanPSMT"
  tanınır), değilse türüne göre Arial / Times New Roman / Courier New (web yazı tipleri
  çoğu bilgisayarda yok, Word rastgele bir yazı tipine düşüyordu).
- Kalan sınır: kartlar, rozetler ve akış şeması gibi çizimler Word'de ayrı resim ve
  tablo olarak geliyor; metinle hizaları kayabiliyor. pdf2docx'in yapısı gereği; web
  sayfası düzeni Word'ün akan metin düzenine birebir oturmuyor.

## Excel

- Tablo: en az 2 satır ve 2 sütun (çerçeveli tek hücreli metin kutusu tablo sayılmaz).
- Birleştirme: sütun sayısı aynı ve (a) ilk satırı grubun başlığıyla aynı (başka
  sayfada) — başlık atlanır — ya da (b) sayfanın ilk tablosu, önceki sayfanın son
  tablosunun devamı ve sütun sınırları 3 pt içinde. Başka tablolar arasında bir boş satır.
- Birleşik hücreler korunur; her tablonun ilk satırı kalın; sütun eni en uzun metinden
  (8–60).
- Sayı biçimi belgeden okunur: açık örnekler sayılır ("12,50" Türkçe, "12.50"
  İngilizce; tarihler sayılmaz), "1.250" gibi iki yana çekilebilen değer çoğunluğa göre,
  eşitlikte Türkçe.
- Sayı olmayanlar metin kalır: baştaki sıfır ("0012"), 9 haneden uzun tam sayı (VKN,
  hesap no), 15 haneden uzun, geçersiz tarih, İngilizce belgede "/" ile tarih (ay / gün
  belirsiz).
- Para birimi biçime yazılır (`#,##0.00 "TL"`, `"$"#,##0.00`); parantezli tutar eksi;
  "%18" → 0,18 (`0%`).
- `=` ile başlayan metin formül olmaz (hücre türü açıkça metin).
- Tablo yoksa hata (kod `tablo_yok`): "Metni almak için Word'e çevirin."

## Ekranda bekleme göstergesi

Kullanıcı (27.11.2026): büyük dosyada yalnız düğme pasifleşiyor, işin sürdüğü görülmüyor.
Önceden yalnız PDF düğmelerinin altında soluk, sabit bir satır vardı.

- Tıklanan düğmede dönen simge ve "Çevriliyor…"; altında kayan çubuk ve geçen süre
  ("Word dosyası hazırlanıyor · 0:37").
- 20 sn'den sonra: "N sayfalık belgede çevirme bir iki dakika sürebilir. Sayfayı kapatmayın."
- Çevirme sürerken sekme kapatılır ya da sayfadan çıkılırsa tarayıcı sorar: sunucu işi
  bitirirse hak sayılır ama dosya inmez.
- **Yüzde yok:** sunucu ilerleme bildirmiyor; yüzde uydurma olurdu. Gerçek sayfa ilerlemesi
  ("Sayfa 8 / 22") sunucunun yanıtı akışla göndermesini ister (yanıt biçimi, hata ve kota
  başlıkları değişir) — gerekirse ayrı iş.
- Gösterge telefonda kapanan panelin (`yan-govde`) dışında, düğmelerle aynı yerde
  (`test_cevirme_gostergesi_gizli_baslar_ve_kapali_panelde_de_gorunur`). Saniye sayacı
  ekran okuyucuya okunmaz (`aria-hidden`).

## Kullanım kaydı

Olay türleri `word`, `excel`; yeni hata kodları `tablo_yok`, `sure`. Yönetim > Kullanım'da
"Çevrilen" sütunu. Dosya adı ve içerik yine yazılmaz.

## Açık

- Gerçek kullanıcı belgeleriyle kalite (özellikle çok sütunlu düzen ve grafikli
  belgeler Word'de) canlıda henüz denenmedi.
- Soğuk açılış bedeli (~1,5 sn) kullanım verisiyle izlenmeli.
