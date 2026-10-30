"""Interfaccia astratta Exchange/Broker.

Strategia, rischio ed esecuzione parlano SOLO con questa interfaccia. Per passare al live si
sostituisce PaperBroker con HyperliquidLiveBroker (perp/live_broker.py): il resto non cambia.
"""
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional

HARD_MAX_LEVERAGE = 10  # tetto rigido: nessun config o override puo superarlo


@dataclass
class OrderRequest:
    coin: str
    is_buy: bool
    size: float                      # in unita della coin (es. BTC)
    order_type: str = "market"       # market | limit | trigger
    limit_px: Optional[float] = None
    reduce_only: bool = False
    trigger_px: Optional[float] = None
    tpsl: Optional[str] = None       # "sl" | "tp" per gli ordini trigger
    leverage: Optional[float] = None
    tif: str = "Gtc"                 # Gtc | Ioc | Alo (come su Hyperliquid)
    tag: str = ""                    # etichetta interna (entry, sl, tp1, close...)


@dataclass
class OrderResult:
    ok: bool
    status: str                      # filled | partial | resting | rejected | cancelled
    order_id: Optional[int] = None
    filled_sz: float = 0.0
    avg_px: float = 0.0
    fee: float = 0.0
    reason: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Broker(ABC):
    name = "abstract"
    is_live = False

    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel(self, coin: str, order_id: int) -> bool: ...

    @abstractmethod
    def open_orders(self, coin: Optional[str] = None) -> list: ...

    @abstractmethod
    def positions(self) -> dict:
        """{coin: {size (con segno), entry_px, margin, leverage, liq_px, upnl, funding_paid, ...}}"""

    @abstractmethod
    def balance(self) -> dict:
        """{account_value, free, margin_used, upnl}"""

    @abstractmethod
    def funding(self, coin: Optional[str] = None) -> float:
        """Funding cumulato pagato (+) / incassato (-) in USD."""

    def set_leverage(self, coin: str, leverage: float, isolated: bool = True) -> bool:
        return True

    def on_tick(self, now_ms: int) -> list:
        """Paper: valuta trigger/limit/liquidazioni. Live: niente (lo fa l'exchange)."""
        return []


def paper_only_enforced() -> bool:
    return os.environ.get("PERP_PAPER_ONLY", "1") != "0"


def make_broker(cfg, market, logger=None, state=None):
    """Sceglie il broker. Il live richiede TRE condizioni: MODE=live e LIVE_ENABLED=true nel config,
    PERP_PAPER_ONLY=0 e PERP_LIVE_CONFIRM=YES nell'ambiente. Oggi nessuna e impostata."""
    from .paper_broker import PaperBroker
    want_live = cfg.get("MODE") == "live" and cfg.get("LIVE_ENABLED") is True
    if want_live and not paper_only_enforced() and os.environ.get("PERP_LIVE_CONFIRM") == "YES":
        from .live_broker import HyperliquidLiveBroker
        return HyperliquidLiveBroker(cfg)
    if want_live and logger:
        logger("LIVE richiesto ma bloccato (PERP_PAPER_ONLY attivo o conferma mancante): uso PAPER")
    return PaperBroker(cfg, market, state=state)
