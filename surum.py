"""Uygulama sürümü. Her yayından ÖNCE bir artırılır ve README'deki "Sürümler"
listesine yazılır; açık depoya aynı adla etiket (`v<SURUM>`) gönderilir.

AGPL-3.0: ağ üzerinden kullananlara ÇALIŞAN sürümün kaynak kodu sunulur. Her
sayfanın alt bilgisi "Hakkında"ya, oradaki bağlantı `KAYNAK_DEPO`'daki `v<SURUM>` etiketine gider; bu yüzden etiket
yayından önce açık depoda olmalı. Kodu kendi sunucusunda çalıştıran, bağlantıyı
kendi deposuna çevirmelidir.
"""
SURUM = '1.15'
TELIF = '© 2026 Mesut Altıntaş'
KAYNAK_DEPO = 'https://github.com/mesut-altintas/pdf-metin-duzenleyici'
LISANS = 'AGPL-3.0'


def kaynak_adresi():
    """Çalışan sürümün kaynak kodu."""
    return f'{KAYNAK_DEPO}/tree/v{SURUM}'
