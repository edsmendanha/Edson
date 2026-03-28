"""
MetodoBOTDIN.py  —  Bot de sinais para IQ Option
Python 3.11 | iqoptionapi local (pasta iqoptionapi/ na raiz)

Estratégia: EMAA/EMAB/EMAC/EMAD + Donchian + TA/TB + ENC/ENV
            tendência confirmada → sinal confirmado → entrada

Ordens: DIGITAL usa buy_digital_spot_v2; BINARIA usa buy
"""

from __future__ import annotations

import csv
import sys
import time
import threading
import importlib.util
from datetime import datetime, date
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 0. Garante que o diretório raiz do projeto está no sys.path
#    (para que `import iqoptionapi` resolva a pasta local iqoptionapi/)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. PATCH WEBSOCKET (opcional, controlado por config)
# ---------------------------------------------------------------------------

def _apply_ws_patch(enable: bool = True) -> None:
    """
    Monkey-patch para websocket-client >= 1.7 + iqoptionapi.
    Corrige erro: on_message() takes 2 positional arguments but 3 were given.
    """
    if not enable:
        return
    try:
        from iqoptionapi.ws.client import WebsocketClient  # type: ignore
        _orig = WebsocketClient.on_message

        def _patched(self, *args):
            msg = args[-1] if args else None
            return _orig(self, msg)

        WebsocketClient.on_message = _patched
        print("[WS patch] ON   (WebsocketClient.on_message normalizado)")
    except (ImportError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# 2. CONFIGURAÇÃO (ConfigObj)
# ---------------------------------------------------------------------------

def load_config(path: str = "config.txt"):
    """
    Carrega config.txt usando ConfigObj (seções INI padrão).
    Retorna objeto ConfigObj com seções [LOGIN], [ESTRATEGIA], [LOGS].
    """
    try:
        from configobj import ConfigObj  # type: ignore
    except ImportError:
        print("[ERRO] configobj não instalado. Rode: pip install configobj")
        sys.exit(1)

    cfg = ConfigObj(path, encoding="utf-8", raise_errors=True)
    return cfg


def _cfg_int(section: dict, key: str, default: int) -> int:
    try:
        return int(section.get(key, default))
    except (ValueError, TypeError):
        return default


def _cfg_float(section: dict, key: str, default: float) -> float:
    try:
        return float(section.get(key, default))
    except (ValueError, TypeError):
        return default


def _cfg_str(section: dict, key: str, default: str = "") -> str:
    try:
        return str(section.get(key, default)).strip()
    except Exception:
        return default


def _truthy(val: str) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes", "sim")


# ---------------------------------------------------------------------------
# 3. DIAGNÓSTICO DE AMBIENTE
# ---------------------------------------------------------------------------

def print_diagnostics(enable: bool = True) -> None:
    """Imprime versão do Python, caminho da iqoptionapi e websocket-client."""
    if not enable:
        return
    print(f"[DIAG] Python      : {sys.version.split()[0]}  ({sys.executable})")
    spec = importlib.util.find_spec("iqoptionapi")
    if spec:
        print(f"[DIAG] iqoptionapi : {spec.origin}")
    else:
        print("[DIAG] iqoptionapi : NÃO ENCONTRADO (veja README)")
    try:
        import websocket as _ws  # type: ignore
        ver = getattr(_ws, "version", None) or getattr(_ws, "__version__", "?")
        print(f"[DIAG] websocket-cl: {ver}")
    except ImportError:
        print("[DIAG] websocket-cl: NÃO ENCONTRADO")
    print()


# ---------------------------------------------------------------------------
# 4. ATIVOS.TXT — PARSING COM SEÇÕES
# ---------------------------------------------------------------------------

VALID_SECTIONS = {"DIGITAL M1", "BINARIA M1", "DIGITAL M5", "BINARIA M5"}


def load_assets_by_section(path: str = "Ativos.txt") -> dict[str, list[str]]:
    """
    Lê Ativos.txt com seções [DIGITAL M1], [BINARIA M1], [DIGITAL M5], [BINARIA M5].
    Linhas com #/; são comentários. Linhas vazias ignoradas.
    Retorna dict: section_name → lista de strings brutas.
    """
    result: dict[str, list[str]] = {s: [] for s in VALID_SECTIONS}
    current_section: Optional[str] = None

    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip().upper()
                current_section = section_name if section_name in VALID_SECTIONS else None
            elif current_section is not None:
                result[current_section].append(line)

    return result


def normalize_asset(raw: str, market: str) -> Optional[str]:
    """
    Normaliza o nome do ativo EM MEMÓRIA (nunca modifica o arquivo).

    Regras:
      - Sem sufixo  → append '-op' (OP) ou '-OTC' (OTC) com base no mercado do menu
      - Com -op / -OP / -Op  → normaliza para '-op'
      - Com -otc / -OTC / -Otc → normaliza para '-OTC'
    """
    raw = raw.strip()
    if not raw:
        return None

    upper = raw.upper()
    if upper.endswith("-OTC"):
        base = raw[: len(raw) - 4].upper()
        suffix = "-OTC"
    elif upper.endswith("-OP"):
        base = raw[: len(raw) - 3].upper()
        suffix = "-op"
    else:
        base = upper
        suffix = "-op" if market == "op" else "-OTC"

    if not base:
        return None
    return f"{base}{suffix}"


def filter_assets_for_market(raw_assets: list[str], market: str) -> list[tuple[str, str]]:
    """
    Normaliza e filtra ativos para o mercado selecionado (op ou otc).
    Retorna lista de (asset_key, api_asset).
    """
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_assets:
        norm = normalize_asset(raw, market)
        if norm is None:
            continue
        if market == "op" and not norm.endswith("-op"):
            continue
        if market == "otc" and not norm.endswith("-OTC"):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        api = asset_key_to_api(norm)
        result.append((norm, api))
    return result


def asset_key_to_api(asset_key: str) -> str:
    """
    Converte asset_key para o nome que a API da IQ Option aceita.
    """
    if asset_key.endswith("-OTC"):
        return asset_key
    if asset_key.endswith("-op"):
        return asset_key[:-3]
    return asset_key


# ---------------------------------------------------------------------------
# 5. SISTEMA DE LOGS POR SESSÃO / DIA
# ---------------------------------------------------------------------------

class SessionLogger:
    """
    Cria automaticamente:
      logs/YYYY-MM-DD/signals_YYYY-MM-DD_HH-MM-SS[_tag].csv
      logs/YYYY-MM-DD/events_YYYY-MM-DD_HH-MM-SS[_tag].log
    """

    SIGNAL_COLS = [
        "timestamp", "ativo", "option_type", "direcao", "timeframe",
        "confianca_pct", "status",
        "ema_a", "ema_b", "ema_c", "ema_d",
        "donchian_up", "donchian_dn",
        "ta", "tb", "enc", "env",
        "tendencia_confirmada", "motivo",
    ]

    def __init__(self, log_dir: str = "logs", session_tag: str = "") -> None:
        today = date.today().isoformat()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        tag = f"_{session_tag}" if session_tag else ""

        day_folder = Path(log_dir) / today
        day_folder.mkdir(parents=True, exist_ok=True)

        self._csv_path = day_folder / f"signals_{ts}{tag}.csv"
        self._log_path = day_folder / f"events_{ts}{tag}.log"

        with open(self._csv_path, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self.SIGNAL_COLS).writeheader()

        self._log_fh = open(self._log_path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._event(f"Sessão iniciada | csv={self._csv_path.name}")

    def write_signal(self, row: dict) -> None:
        with self._lock:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(
                    fh, fieldnames=self.SIGNAL_COLS, extrasaction="ignore"
                ).writerow(row)

    def event(self, msg: str) -> None:
        self._event(msg)

    def close(self) -> None:
        self._event("Sessão encerrada")
        self._log_fh.close()

    def _event(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with self._lock:
            self._log_fh.write(line)


# ---------------------------------------------------------------------------
# 6. CONEXÃO IQ OPTION (SEM THREAD/TIMEOUT HARD - estilo BOT M1/M5)
# ---------------------------------------------------------------------------

RECONNECT_BASE_DELAY_SEC = 5
RECONNECT_MAX_DELAY_SEC = 30


def connect_iq(email: str, senha: str, tipo_conta: str, max_retries: int = 5):
    """
    Conexão direta:
      - IQ_Option(email, senha)
      - ok, reason = connect()
      - sleep(1)
      - change_balance()
    Retorna (iq, True/False).
    """
    from iqoptionapi.stable_api import IQ_Option  # type: ignore

    tipo = tipo_conta.upper()

    for attempt in range(1, max_retries + 1):
        print(f"[CONN] Tentativa {attempt}/{max_retries} ({tipo})...")
        try:
            iq = IQ_Option(email, senha)
            ok, reason = iq.connect()
            if ok:
                time.sleep(1.0)
                iq.change_balance(tipo)
                print(f"[CONN] Conectado! Conta: {tipo}")
                return iq, True
            print(f"[CONN] Falha: {reason}")
        except Exception as exc:
            print(f"[CONN] Falha: {exc}")

        if attempt < max_retries:
            wait = min(RECONNECT_BASE_DELAY_SEC * attempt, RECONNECT_MAX_DELAY_SEC)
            print(f"[CONN] Aguardando {wait}s antes de nova tentativa...")
            time.sleep(wait)

    return None, False


def ensure_connected(iq, email: str, senha: str, tipo_conta: str, max_retries: int = 5):
    """
    Se caiu, reconecta recriando o objeto IQ_Option.
    Retorna (iq_atualizado, ok).
    """
    try:
        if iq is not None and iq.check_connect():
            return iq, True
    except Exception:
        pass

    print("[CONN] Conexão perdida. Reconectando...")
    iq2, ok = connect_iq(email, senha, tipo_conta, max_retries=max_retries)
    return (iq2 if ok else iq), bool(ok)


# ---------------------------------------------------------------------------
# 7. POOL BUILDER
# ---------------------------------------------------------------------------

def _get_open_time(iq) -> dict:
    try:
        data = iq.get_all_open_time()
        return data or {}
    except Exception:
        return {}


def _is_asset_open(api_asset: str, option_type: str, open_time: dict) -> bool:
    if not open_time:
        return False
    if option_type == "digital":
        section = open_time.get("digital", {})
    else:
        section = open_time.get("turbo", open_time.get("binary", {}))

    info = section.get(api_asset, {})
    return bool(info.get("open", False))


def build_pool(digital_assets: list[tuple[str, str]],
               binary_assets: list[tuple[str, str]],
               n_assets: int,
               iq,
               logger: SessionLogger) -> list[dict]:
    if iq is None:
        pool: list[dict] = []
        for ak, api in digital_assets:
            if len(pool) >= n_assets:
                break
            pool.append({"asset_key": ak, "api_asset": api, "option_type": "digital"})
        for ak, api in binary_assets:
            if len(pool) >= n_assets:
                break
            pool.append({"asset_key": ak, "api_asset": api, "option_type": "binary"})
        _log_pool(pool, logger)
        return pool

    while True:
        open_time = _get_open_time(iq)
        pool = []

        for ak, api in digital_assets:
            if len(pool) >= n_assets:
                break
            if _is_asset_open(api, "digital", open_time):
                pool.append({"asset_key": ak, "api_asset": api, "option_type": "digital"})

        for ak, api in binary_assets:
            if len(pool) >= n_assets:
                break
            if _is_asset_open(api, "binary", open_time):
                pool.append({"asset_key": ak, "api_asset": api, "option_type": "binary"})

        if pool:
            _log_pool(pool, logger)
            return pool

        print("[POOL] Nenhum ativo aberto no momento. Aguardando 60s...")
        logger.event("[POOL] Nenhum ativo aberto; aguardando 60s")
        time.sleep(60)


def _log_pool(pool: list[dict], logger: SessionLogger) -> None:
    logger.event(f"Pool construído: {len(pool)} ativo(s)")
    for item in pool:
        line = f"  {item['asset_key']} → api={item['api_asset']} ({item['option_type']})"
        logger.event(line)
        print(f"[POOL] {item['asset_key']} → {item['api_asset']} ({item['option_type'].upper()})")


# ---------------------------------------------------------------------------
# 8. INDICADORES TÉCNICOS
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1.0 - k)
    return round(ema, 6)


def _donchian(highs: list[float], lows: list[float], period: int) -> tuple[Optional[float], Optional[float]]:
    if len(highs) < period or len(lows) < period:
        return None, None
    return round(max(highs[-period:]), 6), round(min(lows[-period:]), 6)


def compute_indicators(closes: list[float], highs: list[float], lows: list[float], periods: dict) -> dict:
    result = {
        "ema_a": _ema(closes, periods["ema_a"]),
        "ema_b": _ema(closes, periods["ema_b"]),
        "ema_c": _ema(closes, periods["ema_c"]),
        "ema_d": _ema(closes, periods["ema_d"]),
    }
    result["donchian_up"], result["donchian_dn"] = _donchian(highs, lows, periods["donchian"])
    result["ta"] = _ema(highs, periods["ta"])
    result["tb"] = _ema(lows, periods["tb"])

    mid = result["ema_b"]
    pct = float(periods.get("enc_percent", 0.001))
    if mid is not None:
        result["enc"] = round(mid * (1.0 + pct), 6)
        result["env"] = round(mid * (1.0 - pct), 6)
    else:
        result["enc"] = None
        result["env"] = None
    return result


# ---------------------------------------------------------------------------
# 9. MOTOR DE SINAIS
# ---------------------------------------------------------------------------

def check_signal(ind: dict, price: float) -> dict:
    required = ["ema_a", "ema_b", "ema_c", "ema_d",
                "donchian_up", "donchian_dn", "ta", "tb", "enc", "env"]
    if any(ind.get(k) is None for k in required):
        return {
            "direction": None, "confirmed": False, "confidence": 0.0,
            "reason": "indicadores_insuficientes", "tendencia_confirmada": False,
        }

    ea, eb, ec, ed = ind["ema_a"], ind["ema_b"], ind["ema_c"], ind["ema_d"]
    dup, ddn = ind["donchian_up"], ind["donchian_dn"]
    ta, tb = ind["ta"], ind["tb"]
    enc, env_ = ind["enc"], ind["env"]

    trend_up = ea > eb > ec > ed
    trend_down = ea < eb < ec < ed
    tendencia_confirmada = trend_up or trend_down

    call_filters = [trend_up, price > dup, price > enc, ta > tb]
    put_filters = [trend_down, price < ddn, price < env_, ta < tb]

    call_score = sum(call_filters)
    put_score = sum(put_filters)
    total = len(call_filters)

    if call_score == total:
        return {
            "direction": "CALL", "confirmed": True, "confidence": 100.0,
            "reason": "todos_filtros_call", "tendencia_confirmada": tendencia_confirmada,
        }
    if put_score == total:
        return {
            "direction": "PUT", "confirmed": True, "confidence": 100.0,
            "reason": "todos_filtros_put", "tendencia_confirmada": tendencia_confirmada,
        }
    if call_score > put_score:
        return {
            "direction": "CALL", "confirmed": False,
            "confidence": round(call_score / total * 100, 1),
            "reason": f"filtros_call_{call_score}/{total}",
            "tendencia_confirmada": tendencia_confirmada,
        }
    if put_score > call_score:
        return {
            "direction": "PUT", "confirmed": False,
            "confidence": round(put_score / total * 100, 1),
            "reason": f"filtros_put_{put_score}/{total}",
            "tendencia_confirmada": tendencia_confirmada,
        }

    return {
        "direction": None, "confirmed": False, "confidence": 0.0,
        "reason": "sem_sinal", "tendencia_confirmada": tendencia_confirmada,
    }


# ---------------------------------------------------------------------------
# 10. GERENCIAMENTO DE ENTRADAS E STOP
# ---------------------------------------------------------------------------

class EntryManager:
    def __init__(self, max_entries: int, stop_win: float, stop_loss: float) -> None:
        self.max_entries = max_entries
        self.stop_win = stop_win
        self.stop_loss = stop_loss
        self._accepted = 0
        self._pnl = 0.0

    @property
    def accepted(self) -> int:
        return self._accepted

    def can_enter(self) -> tuple[bool, str]:
        if self.max_entries > 0 and self._accepted >= self.max_entries:
            return False, f"max_entradas_atingido ({self.max_entries})"
        if self.stop_win > 0 and self._pnl >= self.stop_win:
            return False, f"stop_win_atingido ({self._pnl:.2f})"
        if self.stop_loss > 0 and self._pnl <= -abs(self.stop_loss):
            return False, f"stop_loss_atingido ({self._pnl:.2f})"
        return True, ""

    def register_entry(self, pnl_result: float = 0.0) -> None:
        self._accepted += 1
        self._pnl += pnl_result


# ---------------------------------------------------------------------------
# 11. EXECUÇÃO DE ORDEM (agora retorna accepted)
# ---------------------------------------------------------------------------

def execute_order(iq, item: dict, direction: str, valor: float,
                  tf: int, logger: SessionLogger) -> tuple[bool, float]:
    asset_key = item["asset_key"]
    api_asset = item["api_asset"]
    option_type = item["option_type"]

    if iq is None:
        logger.event(
            f"[STUB] ORDEM ativo={asset_key} api={api_asset} dir={direction} "
            f"valor={valor:.2f} tf={tf}m tipo={option_type}"
        )
        return True, 0.0

    try:
        if option_type == "digital":
            status, order_id = iq.buy_digital_spot_v2(api_asset, valor, direction.lower(), tf)
        else:
            status, order_id = iq.buy(valor, api_asset, direction.lower(), tf)

        if status:
            logger.event(
                f"ORDEM_ACEITA ativo={asset_key} api={api_asset} dir={direction} "
                f"valor={valor:.2f} tf={tf}m tipo={option_type} id={order_id}"
            )
            print(f"[ORDEM] ✓ {asset_key} {direction} ${valor:.2f} {option_type.upper()} (id={order_id})")
            return True, 0.0

        logger.event(f"ORDEM_RECUSADA ativo={asset_key} api={api_asset} dir={direction} motivo={order_id}")
        print(f"[ORDEM] ✗ {asset_key} RECUSADA: {order_id}")
        return False, 0.0

    except Exception as exc:
        logger.event(f"ERRO_ORDEM ativo={asset_key} erro={exc}")
        print(f"[ORDEM] ERRO em {asset_key}: {exc}")
        return False, 0.0


# ---------------------------------------------------------------------------
# 12. HELPERS DE CANDLES
# ---------------------------------------------------------------------------

def fetch_candles(iq, api_asset: str, tf: int,
                  n: int = 50,
                  history: Optional[dict] = None,
                  retries: int = 3) -> tuple[list[float], list[float], list[float]]:
    if history is None:
        history = {}

    if iq is None:
        return _stub_candles(api_asset, history)

    for attempt in range(retries):
        done: threading.Event = threading.Event()
        holder: list = []

        def _get():
            try:
                candles = iq.get_candles(api_asset, tf * 60, n, time.time())
                holder.append(candles)
            except Exception as exc:
                holder.append(exc)
            finally:
                done.set()

        threading.Thread(target=_get, daemon=True).start()
        done.wait(timeout=15)

        if done.is_set() and holder and not isinstance(holder[0], Exception) and holder[0]:
            candles = holder[0]
            closes = [float(c["close"]) for c in candles]
            highs = [float(c["max"]) for c in candles]
            lows = [float(c["min"]) for c in candles]
            history[api_asset] = {"closes": closes, "highs": highs, "lows": lows}
            return closes, highs, lows

        if attempt < retries - 1:
            time.sleep(2)

    h = history.get(api_asset, {"closes": [], "highs": [], "lows": []})
    return h["closes"], h["highs"], h["lows"]


def _stub_candles(api_asset: str, history: dict) -> tuple[list[float], list[float], list[float]]:
    import random
    h = history.get(api_asset, {"closes": [], "highs": [], "lows": []})
    last = h["closes"][-1] if h["closes"] else 1.1000

    close = round(last + random.uniform(-0.0010, 0.0010), 5)
    high = round(close + random.uniform(0.0001, 0.0005), 5)
    low = round(close - random.uniform(0.0001, 0.0005), 5)

    h["closes"] = (h["closes"] + [close])[-60:]
    h["highs"] = (h["highs"] + [high])[-60:]
    h["lows"] = (h["lows"] + [low])[-60:]
    history[api_asset] = h
    return h["closes"], h["highs"], h["lows"]


def _sleep_until_next_candle(tf: int, stub: bool = False) -> None:
    if stub:
        time.sleep(1)
        return
    now = datetime.now()
    seconds = now.second + now.microsecond / 1_000_000
    wait = (tf * 60) - (seconds % (tf * 60))
    time.sleep(max(wait + 0.5, 1))


def _fmt_hhmmss(seconds: float) -> str:
    s = int(max(0, seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# 13. MENU INTERATIVO (com timers)
# ---------------------------------------------------------------------------

def _ask(prompt: str, options: list[str], default: str = "") -> str:
    opts_str = "/".join(options)
    while True:
        ans = input(f"{prompt} [{opts_str}]: ").strip()
        if not ans and default:
            for o in options:
                if o.upper() == default.upper():
                    return o
            return default
        for o in options:
            if o.upper() == ans.upper():
                return o
        print(f"  Opção inválida. Use: {opts_str}")


def _ask_float(prompt: str, default: float) -> float:
    while True:
        ans = input(f"{prompt} [padrão={default}]: ").strip()
        if not ans:
            return default
        try:
            val = float(ans.replace(",", "."))
            if val < 0:
                print("  Valor deve ser >= 0.")
                continue
            return val
        except ValueError:
            print("  Digite um número válido.")


def _ask_int(prompt: str, min_val: int, max_val: int, default: int) -> int:
    while True:
        ans = input(f"{prompt} [{min_val}-{max_val}, padrão={default}]: ").strip()
        if not ans:
            return default
        try:
            val = int(ans)
            if min_val <= val <= max_val:
                return val
            print(f"  Valor deve ser entre {min_val} e {max_val}.")
        except ValueError:
            print("  Digite um número inteiro.")


def run_menu(email: str) -> dict:
    print()
    print("=" * 60)
    print("        MetodoBOTDIN  —  Menu de Inicialização")
    print("=" * 60)
    print(f"  Usuário : {email}")
    print()

    tipo_conta = _ask("  1. Conta", ["demo", "real"], default="demo")
    tf_str = _ask("  2. Timeframe", ["M1", "M5"], default="M1")
    tf = 1 if tf_str.upper() == "M1" else 5
    market = _ask("  3. Mercado", ["OP", "OTC"], default="OP").lower()
    n_assets = _ask_int("  4. Ativos simultâneos", 1, 4, default=1)
    valor = _ask_float("  5. Valor por entrada", default=1.0)
    stop_win = _ask_float("  6. Stop Win  (0 = desabilitado)", default=0.0)
    stop_loss = _ask_float("  7. Stop Loss (0 = desabilitado)", default=0.0)
    max_entries = _ask_int("  8. Max entradas (0 = sem limite)", 0, 9999, default=10)

    session_max_minutes = _ask_int("  9. Duração máxima da sessão (0 = sem limite)", 0, 24 * 60, default=120)
    idle_stop_minutes = _ask_int("  10. Parar por inatividade (min sem ORDEM ACEITA; 0 = desabilitado)", 0, 24 * 60, default=20)

    print()
    print("=" * 60)
    print(f"  Conta       : {tipo_conta.upper()}")
    print(f"  Timeframe   : M{tf}")
    print(f"  Mercado     : {market.upper()}")
    print(f"  Ativos      : {n_assets}")
    print(f"  Entrada     : {valor:.2f}")
    print(f"  Stop Win    : {stop_win:.2f}   |   Stop Loss : {stop_loss:.2f}")
    print(f"  Max entradas: {max_entries if max_entries > 0 else 'sem limite'}")
    print(f"  Sessão máx  : {session_max_minutes} min" if session_max_minutes > 0 else "  Sessão máx  : sem limite")
    print(f"  Inatividade : {idle_stop_minutes} min" if idle_stop_minutes > 0 else "  Inatividade : desabilitado")
    print("=" * 60)
    print()

    confirm = _ask("  Confirmar e iniciar?", ["S", "N"], default="S")
    if confirm.upper() == "N":
        print("  Operação cancelada.")
        sys.exit(0)

    return {
        "tipo_conta": tipo_conta,
        "timeframe": tf,
        "market": market,
        "n_assets": n_assets,
        "valor": valor,
        "stop_win": stop_win,
        "stop_loss": stop_loss,
        "max_entries": max_entries,
        "session_max_minutes": session_max_minutes,
        "idle_stop_minutes": idle_stop_minutes,
    }


# ---------------------------------------------------------------------------
# 14. LOOP PRINCIPAL (com timers + contador)
# ---------------------------------------------------------------------------

def run_bot(session: dict, pool: list[dict], periods: dict,
            iq, logger: SessionLogger, email: str, senha: str) -> None:
    tf = session["timeframe"]
    valor = session["valor"]
    tipo_conta = session["tipo_conta"]

    entry_mgr = EntryManager(session["max_entries"], session["stop_win"], session["stop_loss"])
    candle_history: dict = {}
    min_candles = max(v for k, v in periods.items() if isinstance(v, int))

    start_ts = time.time()
    last_order_accepted_ts = start_ts

    session_max_minutes = int(session.get("session_max_minutes", 120) or 0)
    idle_stop_minutes = int(session.get("idle_stop_minutes", 20) or 0)

    session_max_seconds = session_max_minutes * 60 if session_max_minutes > 0 else 0
    idle_stop_seconds = idle_stop_minutes * 60 if idle_stop_minutes > 0 else 0

    logger.event(
        f"Parâmetros: tf={tf}m valor={valor:.2f} mercado={session['market'].upper()} "
        f"max_entradas={session['max_entries']} stop_win={session['stop_win']:.2f} "
        f"stop_loss={session['stop_loss']:.2f} session_max_min={session_max_minutes} idle_stop_min={idle_stop_minutes}"
    )
    logger.event(f"Pool: {[p['asset_key'] for p in pool]}")

    print(f"\n[BOT] Monitorando {len(pool)} ativo(s). Ctrl+C para encerrar.\n")

    try:
        while True:
            now_ts = time.time()

            # timer sessão
            if session_max_seconds > 0:
                remaining = session_max_seconds - (now_ts - start_ts)
                if remaining <= 0:
                    msg = f"[TIMER] Sessão encerrada por tempo máximo ({session_max_minutes} min)."
                    print(msg)
                    logger.event(msg)
                    break
                remain_txt = _fmt_hhmmss(remaining)
            else:
                remain_txt = "SEM LIMITE"

            # timer inatividade
            if idle_stop_seconds > 0:
                idle_elapsed = now_ts - last_order_accepted_ts
                if idle_elapsed >= idle_stop_seconds:
                    msg = f"[TIMER] Sessão encerrada por inatividade ({idle_stop_minutes} min sem ORDEM ACEITA)."
                    print(msg)
                    logger.event(msg)
                    break
                idle_txt = f"{_fmt_hhmmss(idle_elapsed)}/{_fmt_hhmmss(idle_stop_seconds)}"
            else:
                idle_txt = "OFF"

            print(f"[TIMER] restante={remain_txt} | inatividade={idle_txt} | trades={entry_mgr.accepted}")

            can_enter_global, block_reason = entry_mgr.can_enter()
            if not can_enter_global:
                print(f"\n[BOT] Encerramento: {block_reason}")
                logger.event(f"Encerramento: {block_reason}")
                break

            # Verifica/renova conexão
            if iq is not None:
                iq, ok = ensure_connected(iq, email, senha, tipo_conta)
                if not ok:
                    logger.event("[ERRO] Reconexão falhou. Encerrando.")
                    print("[ERRO] Não foi possível reconectar. Encerrando.")
                    break

            for item in pool:
                asset_key = item["asset_key"]
                api_asset = item["api_asset"]
                option_type = item["option_type"]
                ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                closes, highs, lows = fetch_candles(iq, api_asset, tf, n=50, history=candle_history)

                if len(closes) < min_candles:
                    logger.event(f"AGUARDANDO ativo={asset_key} candles={len(closes)}/{min_candles}")
                    continue

                price = closes[-1]
                ind = compute_indicators(closes, highs, lows, periods)
                sig = check_signal(ind, price)

                if sig["confirmed"]:
                    can_now, blk = entry_mgr.can_enter()
                    if can_now:
                        status = "CONFIRMADO"
                        motivo = sig["reason"]

                        print(
                            f"[SINAL] {ts_now}  {asset_key}  {sig['direction']}  "
                            f"conf={sig['confidence']}%  tf={tf}m  {option_type.upper()}  [{status}]"
                        )
                        logger.event(
                            f"CONFIRMADO ativo={asset_key} dir={sig['direction']} "
                            f"conf={sig['confidence']}% motivo={motivo} tipo={option_type}"
                        )

                        accepted, pnl = execute_order(iq, item, sig["direction"], valor, tf, logger)
                        if accepted:
                            last_order_accepted_ts = time.time()
                            entry_mgr.register_entry(pnl)
                        else:
                            # ordem recusada não conta como trade aceito
                            pass
                    else:
                        status = "BLOQUEADO"
                        motivo = blk
                        logger.event(f"BLOQUEADO ativo={asset_key} dir={sig['direction']} motivo={motivo}")
                elif sig["direction"] is not None:
                    status = "DETECTADO"
                    motivo = sig["reason"]
                    logger.event(f"DETECTADO ativo={asset_key} dir={sig['direction']} conf={sig['confidence']}% motivo={motivo}")
                else:
                    status = "SEM_SINAL"
                    motivo = sig["reason"]
                    logger.event(f"SEM_SINAL ativo={asset_key} motivo={motivo}")

                logger.write_signal({
                    "timestamp": ts_now,
                    "ativo": asset_key,
                    "option_type": option_type,
                    "direcao": sig.get("direction") or "",
                    "timeframe": tf,
                    "confianca_pct": sig["confidence"],
                    "status": status,
                    "ema_a": ind.get("ema_a", ""),
                    "ema_b": ind.get("ema_b", ""),
                    "ema_c": ind.get("ema_c", ""),
                    "ema_d": ind.get("ema_d", ""),
                    "donchian_up": ind.get("donchian_up", ""),
                    "donchian_dn": ind.get("donchian_dn", ""),
                    "ta": ind.get("ta", ""),
                    "tb": ind.get("tb", ""),
                    "enc": ind.get("enc", ""),
                    "env": ind.get("env", ""),
                    "tendencia_confirmada": sig.get("tendencia_confirmada", False),
                    "motivo": motivo,
                })

            _sleep_until_next_candle(tf, stub=(iq is None))

    except KeyboardInterrupt:
        print("\n[BOT] Interrompido pelo usuário.")
        logger.event("Interrompido pelo usuário (KeyboardInterrupt)")


# ---------------------------------------------------------------------------
# 15. ENTRY POINT
# ---------------------------------------------------------------------------

def _ensure_runtime_dirs() -> None:
    for folder in ("logs", "state", "presets"):
        Path(folder).mkdir(exist_ok=True)


def main() -> None:
    _ensure_runtime_dirs()

    config_path = Path("config.txt")
    if not config_path.exists():
        print(f"[ERRO] {config_path} não encontrado.")
        print("       Copie config.example.txt para config.txt e preencha suas credenciais.")
        sys.exit(1)

    cfg = load_config(str(config_path))

    login_section = cfg.get("LOGIN", {})
    estrategia = cfg.get("ESTRATEGIA", {})
    logs_section = cfg.get("LOGS", {})

    email = _cfg_str(login_section, "email")
    senha = _cfg_str(login_section, "senha")

    if not email or not senha or email.upper().startswith("SEU_"):
        print("[ERRO] Preencha email e senha em config.txt, seção [LOGIN].")
        sys.exit(1)

    diag = _truthy(_cfg_str(logs_section, "print_diagnostics", "true"))
    print_diagnostics(diag)

    ws_patch = _truthy(_cfg_str(logs_section, "enable_ws_on_message_patch", "true"))
    _apply_ws_patch(ws_patch)

    periods = {
        "ema_a": _cfg_int(estrategia, "ema_a", 3),
        "ema_b": _cfg_int(estrategia, "ema_b", 7),
        "ema_c": _cfg_int(estrategia, "ema_c", 17),
        "ema_d": _cfg_int(estrategia, "ema_d", 34),
        "donchian": _cfg_int(estrategia, "donchian", 20),
        "ta": _cfg_int(estrategia, "ta", 5),
        "tb": _cfg_int(estrategia, "tb", 5),
        "enc_percent": _cfg_float(estrategia, "enc_percent", 0.001),
    }

    ativos_path = Path("Ativos.txt")
    if not ativos_path.exists():
        print(f"[ERRO] {ativos_path} não encontrado.")
        sys.exit(1)

    all_assets = load_assets_by_section(str(ativos_path))
    total_raw = sum(len(v) for v in all_assets.values())
    non_empty = sum(1 for v in all_assets.values() if v)
    print(f"[INFO] Ativos.txt: {total_raw} entrada(s) em {non_empty} seção(ões)")

    session = run_menu(email)
    market = session["market"]
    tf = session["timeframe"]
    tf_str = f"M{tf}"

    raw_digital = all_assets.get(f"DIGITAL {tf_str}", [])
    raw_binary = all_assets.get(f"BINARIA {tf_str}", [])

    digital_assets = filter_assets_for_market(raw_digital, market)
    binary_assets = filter_assets_for_market(raw_binary, market)

    if not digital_assets and not binary_assets:
        print(
            f"[ERRO] Nenhum ativo {market.upper()} encontrado nas seções "
            f"[DIGITAL {tf_str}] e [BINARIA {tf_str}]."
        )
        print("       Verifique Ativos.txt e garanta sufixos -op ou -OTC.")
        sys.exit(1)

    print(
        f"[INFO] Seções {tf_str} | Digital: {len(digital_assets)} | "
        f"Binária: {len(binary_assets)} | Mercado: {market.upper()}"
    )

    log_dir = _cfg_str(logs_section, "log_dir", "logs")
    session_tag = _cfg_str(logs_section, "session_tag", "").strip()
    if not session_tag:
        session_tag = f"m{tf}_{market}"

    logger = SessionLogger(log_dir=log_dir, session_tag=session_tag)
    logger.event(
        f"Sessão | conta={session['tipo_conta'].upper()} | tf=M{tf} | mercado={market.upper()} | "
        f"ativos={session['n_assets']} | valor={session['valor']:.2f} | "
        f"stopwin={session['stop_win']:.2f} | stoploss={session['stop_loss']:.2f} | "
        f"maxentradas={session['max_entries']} | "
        f"session_max_min={session['session_max_minutes']} | idle_stop_min={session['idle_stop_minutes']}"
    )
    print(f"[INFO] Logs em: {log_dir}/{date.today().isoformat()}/")

    iq = None
    try:
        iq, ok = connect_iq(email, senha, session["tipo_conta"])
        if not ok:
            print("[ERRO] Não foi possível conectar após todas as tentativas.")
            logger.event("[ERRO] Conexão falhou após todos os retries.")
            logger.close()
            sys.exit(1)
    except ImportError:
        print("[AVISO] iqoptionapi não encontrada. Rodando em modo SIMULAÇÃO (stub).")
        logger.event("[AVISO] iqoptionapi ausente; modo stub ativo")

    try:
        pool = build_pool(digital_assets, binary_assets, session["n_assets"], iq, logger)
    except KeyboardInterrupt:
        print("\n[BOT] Cancelado durante seleção de pool.")
        logger.close()
        sys.exit(0)

    try:
        run_bot(session, pool, periods, iq, logger, email=email, senha=senha)
    finally:
        logger.close()
        print("[BOT] Encerrado.")


if __name__ == "__main__":
    main()
