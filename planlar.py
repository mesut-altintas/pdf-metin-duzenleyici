"""Planlar. Tanımlar burada (kodda); hesabın planı veritabanında (`hesap.plan`).

Kararlar (kullanıcı, 22.11.2026; ayrıntı `docs/plan-kota.md`):
  * Planlar yalnız aylık belge kotasıyla ayrılır; hiçbir özellik kapalı değil.
  * Ücretsiz plan ayda 3 farklı belge.
  * O gün var olan kullanıcılar sınırsız "hediye" planla başlar (`GECIS`).
"""
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Plan:
    kod: str
    ad: str
    aylik_belge: int | None      # None: sınırsız


PLANLAR = {
    'ucretsiz': Plan('ucretsiz', 'Ücretsiz', 3),
    'hediye': Plan('hediye', 'Sınırsız (hediye)', None),
}
VARSAYILAN = 'ucretsiz'          # yönetimden eklenen yeni kullanıcı (formda değiştirilebilir)
GECIS = 'hediye'                 # 1.9'dan önce açılmış, hesabı olmayan kullanıcılar


def bul(kod):
    """Tanınmayan kod (ör. yeni bir plan eklendikten sonra eski sürüme dönülmesi)
    sınırsız sayılır ve log'a düşer: ödeyen müşteri sessizce kısıtlanmasın."""
    plan = PLANLAR.get(kod)
    if plan is None:
        log.error('tanınmayan plan kodu: %r; sınırsız sayıldı', kod)
        return Plan(str(kod), str(kod), None)
    return plan
