"""KULLANICILAR ortam değişkeni için şifre özeti üretir.

    .venv/bin/python sifre_ozeti.py ahmet

Çıktıdaki `ahmet=<özet>` satırı KULLANICILAR'a eklenir; birden fazla
kullanıcı virgülle ayrılır. Şifrenin kendisi hiçbir yere yazılmaz.
"""
import getpass
import sys

from werkzeug.security import generate_password_hash


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print('Kullanım: python sifre_ozeti.py <kullanici_adi>', file=sys.stderr)
        sys.exit(2)
    ad = sys.argv[1].strip().lower()
    if any(c in ad for c in ',= '):
        print('Kullanıcı adında virgül, eşittir ya da boşluk olamaz.', file=sys.stderr)
        sys.exit(2)
    sifre = getpass.getpass('Şifre: ')
    if len(sifre) < 10:
        print('Şifre en az 10 karakter olmalı.', file=sys.stderr)
        sys.exit(2)
    if getpass.getpass('Şifre (tekrar): ') != sifre:
        print('Şifreler aynı değil.', file=sys.stderr)
        sys.exit(2)
    print(f'{ad}={generate_password_hash(sifre)}')


if __name__ == '__main__':
    main()
