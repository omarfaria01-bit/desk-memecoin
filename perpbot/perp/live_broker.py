"""Broker LIVE per Hyperliquid: STUB, volutamente non funzionante.

Non importa SDK, non legge chiavi, non firma nulla. Ogni metodo solleva NotImplementedError.
Vedi LIVE_READINESS.md per cosa scrivere qui (hyperliquid-python-sdk, API wallet/agent key,
ordini market=IOC con prezzo limite, trigger tpsl reduce-only, leva isolata, dry run).
"""
import os
from .exchange import Broker, OrderRequest, OrderResult, HARD_MAX_LEVERAGE


class HyperliquidLiveBroker(Broker):
    name = "hyperliquid-live"
    is_live = True

    def __init__(self, cfg):
        if not (cfg.get("MODE") == "live" and cfg.get("LIVE_ENABLED") is True
                and os.environ.get("PERP_PAPER_ONLY", "1") == "0"
                and os.environ.get("PERP_LIVE_CONFIRM") == "YES"):
            raise RuntimeError("Live non abilitato: servono MODE=live, LIVE_ENABLED=true, "
                               "PERP_PAPER_ONLY=0 e PERP_LIVE_CONFIRM=YES")
        self.cfg = cfg
        # TODO live: from hyperliquid.exchange import Exchange; from hyperliquid.info import Info
        # TODO live: chiave dell'API wallet (agent) da variabile d'ambiente HL_AGENT_KEY, mai su file/chat
        # TODO live: dry_run=True di default -> costruisce e logga gli ordini senza inviarli
        raise NotImplementedError("HyperliquidLiveBroker non e ancora implementato (vedi LIVE_READINESS.md)")

    def place_order(self, req: OrderRequest) -> OrderResult:
        # TODO live: leverage <= HARD_MAX_LEVERAGE; exchange.update_leverage(lev, coin, is_cross=False)
        # market -> exchange.order(coin, is_buy, sz, px_limite_slippage, {"limit": {"tif": "Ioc"}}, reduce_only)
        # trigger -> {"trigger": {"triggerPx": px, "isMarket": True, "tpsl": "sl"|"tp"}}, reduce_only=True
        raise NotImplementedError

    def cancel(self, coin, order_id):
        raise NotImplementedError  # exchange.cancel(coin, oid)

    def open_orders(self, coin=None):
        raise NotImplementedError  # info.frontend_open_orders(address)

    def positions(self):
        raise NotImplementedError  # info.user_state(address)["assetPositions"]

    def balance(self):
        raise NotImplementedError  # info.user_state(address)["marginSummary"]

    def funding(self, coin=None):
        raise NotImplementedError  # info.user_funding_history(address, start)
