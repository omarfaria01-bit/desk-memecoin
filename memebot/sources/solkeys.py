"""v8.4f: indirizzi Solana: verifica sulla curva ed25519 (wallet normale vs PDA/programma) e indirizzi morti."""

_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CURVE_CACHE = {}


def _b58dec(s):
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    pad = len(s) - len(s.lstrip("1"))
    b = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * pad + b


def on_curve(addr):
    """v8.4f: True se l'indirizzo e una chiave ed25519 valida (wallet normale); False = PDA/programma (fuori curva).
    None se non decodificabile."""
    if addr in _CURVE_CACHE:
        return _CURVE_CACHE[addr]
    try:
        b = _b58dec(str(addr))
        if len(b) != 32:
            raise ValueError
        y = int.from_bytes(b, "little") & ((1 << 255) - 1)
        sign = b[31] >> 7
        if y >= _P:
            res = False
        else:
            y2 = y * y % _P
            x2 = (y2 - 1) * pow(_D * y2 + 1, _P - 2, _P) % _P
            if x2 == 0:
                res = sign == 0
            else:
                res = pow(x2, (_P - 1) // 2, _P) == 1
    except Exception:  # noqa: BLE001
        res = None
    if len(_CURVE_CACHE) > 20000:
        _CURVE_CACHE.clear()
    _CURVE_CACHE[addr] = res
    return res


# indirizzi "morti" (token bruciati di fatto): mai holder veri
DEAD_ADDRS = frozenset({
    "1nc1nerator11111111111111111111111111111111",   # incinerator
    "11111111111111111111111111111111",              # System Program
})
