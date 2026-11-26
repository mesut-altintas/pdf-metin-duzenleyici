# Motor: nasıl çalışır, neye dayanır

`pdf_duzenle/motor.py` Flask'a bağımlı değil. İleride portala modül olarak
taşınırsa olduğu gibi alınabilir (portal kuralı: ağır kütüphane modül düzeyinde
import edilmez — portalda `import pymupdf` fonksiyon içine alınmalı).

## Akış

1. **analiz**: her sayfa JPEG'e çizilir (toplam yanıt bütçesine sığana dek
   çözünürlük düşürülür) ve sayfa içeriğindeki metin parçaları (span) döner.
   Parça kimliği `sayfa-blok-satır-span`; aynı dosya + aynı PyMuPDF sürümünde
   kararlı. Üretim ucu dosyayı yeniden çıkarır, kimliği ve **eski metni**
   karşılaştırır; uymazsa üretmez.
2. **uygula**: her değişiklik için ortak baş ve son ek bulunur, yalnız değişen
   harfler silinir (`_planla`). Genişlik aynıysa yalnız orta kısım yazılır;
   değilse hizaya göre (sol: baştan sonrası, sağ: sondan öncesi, orta: hepsi).
3. Silme: her harfin merkezine 0,4 pt'lik redaksiyon kutusu
   (`images=NONE, graphics=NONE`, dolgu yok).
4. Yazma: yazı tipi sırası `asil` → `program` → harf harf `karma` / `benzer`.
5. Denetim: dokunulmayan her harf (boşluk ve sentetik boşluk hariç) aynı yerde
   (±0,25 pt) aynı harf olarak durmalı; durmuyorsa `PdfHatasi`, dosya üretilmez.

## Yeni metin ekleme (1.6)

`uygula(veri, duzeltmeler, eklemeler)`: eklemeler düzeltmelerden SONRA yazılır
(silinen yere yazılan yeni metin karışmasın). Nokta sayfa görüntüsünde oran (sol
üst 0,0; y taban çizgisi) → `page.derotation_matrix` ile içerik düzlemine; yön
ve "aşağı" da aynı matrisle, böylece döndürülmüş sayfada yazı ekranda yatay.
Yazı tipi: `belge` → düzeltmelerdeki seçim (`_yazim_sec`, aynı nesne / karma /
benzer; sayfada yoksa belgenin başka sayfası), `standart` → 12 standart yazı
tipinden biri. Satır aralığı 1,2 × boyut. Hiçbir şey silinmez; yine de sonunda
var olan bütün harflerin yerinde durduğu denetlenir (bozmayla sınanamıyor:
ekleme silme yapmadığı için yalnız bir hata durumunda tetiklenir).

## Resim ekleme (1.17)

`uygula(..., resimler)`: `[{sayfa, x, y, w, h, veri}]`; kutu sayfa görüntüsünde oran (sol üst).
PNG ya da JPEG, en çok 20 resim, her biri 3 MB ve en uzun kenarı 4096 piksel. Resimler en son
eklenir, her şeyin üstünde durur; kutuya tam oturtulur (`keep_proportion=False` — oran tarayıcıda
korunur ya da kullanıcı kenar tutamacıyla bilerek değiştirir). Döndürme (90°), beyaz zemini
kaldırma ve saydamlık tarayıcıda resmin kendisine işlenir (`public/static/resim.js`, `editor.js`
"pişirme"); sunucu yalnız son resmi yerleştirir. Metnin üstüne binen resim uyarı verir; sonunda
dokunulmayan bütün harflerin yerinde durduğu yine denetlenir.

**Ölçüm (26.11.2026, PyMuPDF 1.28.2):** döndürülmüş sayfada (0 / 90 / 180 / 270) kutu
`derotation_matrix` ile içerik düzlemine çevrilip `normalize()` edilir; resim **yalnız
`rotate=page.rotation` ile** ekranda dik ve doğru yerde durur (dört renkli deneme resmiyle, dört
dönüşte dört çeyrek; `test_resim_kutuya_ekranda_dik_oturur`). `Pixmap.clear_with(0)` alfayı 255
bırakıyor — saydam deneme resmi `set_rect(..., (0, 0, 0, 0))` ile yapılır.

## Paragraf düzenleme (1.7)

`uygula(..., paragraflar=[{id: 'P<sayfa>-<blok>', eski, yeni, hiza}])`.
Paragraf = bloktaki en az iki yatay, düzenlenebilir satır; sol / orta / sağ
kenarı hizalı, aynı yazı tipinde ve satır aralığında ardışık bloklar birleşir.
Ölçülen: sol kenar, ilk satır girintisi, asıl taban çizgileri (Chrome piksele
yuvarlıyor, aralıklar eşit değil), ortanca aralık (uzayan satırlar için),
doğal sözcük aralığı, hizalama (iki yana: tam satırların sağ kenarı eşit ve
aralıkları doğaldan geniş). Sözcükler: boşluk karakteri ya da yazı boyutunun
%20'sinden geniş konum aralığı sözcüğü bitirir (yalnız boşluk olan parçalar
parça listesine girmiyor). Yeniden dizme sözcük sözcük; iki yana yaslıda artan
boşluk sözcük aralarına eşit. Taşma (kullanıcı kararı): önce asıl boyut, sonra
%3'lük adımlarla en çok %15 küçültme, yine sığmazsa asıl boyutta aşağı uzama +
alttakine binerse uyarı. Biçim (kullanıcı kararı): sözcük farkı (difflib);
değişmeyen sözcük biçimini korur, değiştirilen yerinin, eklenen solundakinin
biçimini alır. Düzenlenen paragraf sonuç dosyasında yeniden paragraf olarak
tanınır (test). Aynı paragrafta satır ve paragraf değişikliği birlikte gelemez.

Bilinen sınırlar: liste maddeleri / adres satırları da "paragraf" sayılabilir
(paragraf düzenleme yalnız isteğe bağlı bir seçenek); tireleme yapılmaz;
satır içi farklı boyut / renk korunur ama satır yüksekliği baskın boyuttan.

## Ölçümler (PyMuPDF 1.28.2, 22.11.2026)

| Ölçülen | Sonuç |
|---|---|
| PyMuPDF 2.1 bağımlılıkları | `onnxruntime` zorunlu, paket 67 MB → 1.28.2'de kalındı (bağımlılıksız) |
| Merkezden 0,4 pt redaksiyon | yalnız o harf silinir; komşu harf, renkli zemin, çizgi kalır |
| `add_redact_annot` varsayılan dolgu | yok (beyaz kutu çizilmez) |
| Redaksiyonun yan etkisi | üst üste binen **Link ve FreeText açıklamalarını siler**; Highlight ve form alanı kalır → `/Annots` önce saklanıp geri konur |
| Redaksiyon sonrası `/Resources` | sayfaya doğrudan sözlük olarak yazılıyor |
| `get_text` | FreeText açıklama yazısını da sayfa metni gibi veriyor → `get_displaylist(annots=False)` ile yalnız içerik |
| Döndürülmüş sayfa | çıkarma koordinatları döndürülmemiş düzlemde; ekran için `rotation_matrix` |
| Kırpılmış sayfa | koordinatlar cropbox köşesine göre; `transformation_matrix` hesaba katıyor |
| `TextWriter` | döndürülmemiş düzlemde yazar; `morph` matrisi y'si yukarı düzlemde (`Matrix(0,-1,1,0)` → yön (0,1)) |
| Alt küme yazı tipi (Chrome) | Type0 Identity-H, ToUnicode yalnız kullanılan glifler |
| Alt küme yazı tipi (PyMuPDF `subset_fonts`) | ToUnicode bütün glifleri eşliyor, çizimi yalnız kullanılanlarda → **mürekkep denetimi şart** |
| Alt kümenin kendi harf tablosu | yok (`has_glyph` hep 0) → yazı tipi programını yeniden yüklemek çoğu dosyada işlemez |
| Alt kümede boşluk / tire | çoğu zaman U+00A0 / U+00AD olarak eşleniyor → ekranda düz boşluk / tire |
| Standart 14 (`helv` vb.) | Türkçe harflerin hepsi var, `₺` yok |
| Görünmez metin (OCR katmanı) | `char_flags` 0 ve `alpha` 0 → düzenlenemez işaretlenir |
| Sahip şifreli PDF | `is_encrypted` False dönüyor ama `permissions` doğru → izin bitine bakılır |
| `send_file` | `Cache-Control: no-cache` yazıyor → `after_request` `no-store` ile ezer |
| Vercel gövde sınırı (canlı, 22.11.2026) | ondalık: 4.492.812 bayt geçti, 4.494.359 bayt uygulamaya ulaşmadan 413 `FUNCTION_PAYLOAD_TOO_LARGE`. Önceki 1024 tabanlı 4,3 MB dosya sınırı (4.508.876) bunun üstündeydi → 4.400.000'e indi |

## Bilinen sınırlar

- Satır kaydırma yok: uzayan metin aynı satırda sağa taşar; yanındaki metne
  binerse uyarı verilir. "Sığdır" seçeneği tek oranla yatay sıkıştırır (en az %50).
- Asıl yazı tipinde olmayan harf benzer yazı tipiyle (Helvetica / Times / Courier)
  yazılır; uyarı verilir.
- Dikey yazım, görünmez metin, resimdeki yazı (taranmış belge) düzenlenemez.
- Tc (harf aralığı) tahmin edilir; Tz (yatay ölçek) ve kontur ile çizilmiş metin
  asıl gibi yeniden üretilmez.
- Vercel istek / yanıt gövdesi ondalık 4,5 MB (başlıklar dahil): dosya 4,4 MB'a (4.400.000 bayt),
  yanıt 4,4 MB'a sınırlı.
