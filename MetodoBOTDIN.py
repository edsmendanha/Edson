"""
MetodoBOTDIN.py  –  Esqueleto principal do bot MetodoBOTDIN
Python 3.11 | IQ Option API local (pasta iqoptionapi/ na raiz)

Estratégia base: EMAA/EMAB/EMAC/EMAD + Donchian + TA/TB + ENC/ENV +
                 tendencia_confirmada → sinal confirmado → entrada.

Execução de ordens: STUB (não envia ordem real; substitua quando pronto).
"""

from __future__ import annotations

import csv
import os
import sys
import time
import threading
import importlib.util
from configparser import ConfigParser, NoSectionError, NoOptionError
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 1. AUTODIAGNÓSTICO
# ---------------------------------------------------------------------------

def _print_diagnostics(config: dict) -> None:
    """Imprime versões e caminhos na inicialização (controlado por config)."""
    if not _truthy(config.get("print_diagnostics", "true")):
        return

    print(f"[DIAG] Python        : {sys.version.split()[0]}  ({sys.executable})")

    # iqoptionapi: mostra de onde está sendo carregado
    spec = importlib.util.find_spec("iqoptionapi")
    if spec:
        print(f"[DIAG] iqoptionapi   : {spec.origin}")
    else:
        print("[DIAG] iqoptionapi   : NÃO ENCONTRADO (veja README)")

    # websocket-client
    try:
        import websocket as _ws
        ver = getattr(_ws, "version", None) or getattr(_ws, "__version__", "?")
        print(f"[DIAG] websocket-cl  : {ver}")
    except ImportError:
        print("[DIAG] websocket-cl  : NÃO ENCONTRADO")

    print()


# ---------------------------------------------------------------------------
# 2. PATCH WEBSOCKET (opcional, controlado por config)
# ---------------------------------------------------------------------------

def _apply_ws_patch_if_needed(config: dict) -> None:
    """Monkey-patch opcional para websocket-client >= 1.7 + iqoptionapi."""
    if not _truthy(config.get("enable_ws_on_message_patch", "true")):
        print("[WS patch] OFF  (desabilitado em config.txt)")
        return

    try:
        from websocket import WebSocketApp  # noqa: F401 (imported to verify availability)

        # Detecta se o WebsocketClient da iqoptionapi tem assinatura de 2 args
        try:
            from iqoptionapi.ws.client import WebsocketClient

            _orig_on_message = WebsocketClient.on_message

            def _patched_on_message(self, *args):
                # Normaliza para receber (message,) independente da versão
                message = args[-1] if args else None
                return _orig_on_message(self, message)

            WebsocketClient.on_message = _patched_on_message
            print("[WS patch] ON   (WebsocketClient.on_message normalizado)")
        except (ImportError, AttributeError):
            print("[WS patch] SKIP (iqoptionapi não encontrada; patch ignorado)")
    except ImportError:
        print("[WS patch] SKIP (websocket não encontrado)")


# ---------------------------------------------------------------------------
# 3. CONFIGURAÇÃO
# ---------------------------------------------------------------------------

def _truthy(val: str) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes", "sim")


def load_config(path: str = "config.txt") -> dict:
    """
    Lê config.txt (estilo INI sem seção, ou com seção).
    Retorna um dicionário simples chave → valor (strings).
    """
    raw = Path(path).read_text(encoding="utf-8")
    # Adiciona seção fake para reutilizar ConfigParser
    ini = "[main]\n" + raw
    cp = ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read_string(ini)
    cfg: dict = {}
    for key, val in cp["main"].items():
        cfg[key] = val.strip()
    return cfg


# ---------------------------------------------------------------------------
# 4. LISTA DE ATIVOS
# ---------------------------------------------------------------------------

def load_assets(path: str = "Ativos.txt") -> list[str]:
    """Lê Ativos.txt e retorna lista de ativos (sem comentários/brancos)."""
    assets: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                assets.append(line)
    return assets


# ---------------------------------------------------------------------------
# 5. SISTEMA DE LOGS POR SESSÃO E DIA
# ---------------------------------------------------------------------------

class SessionLogger:
    """
    Gerencia logs estruturados por sessão.
    Cria automaticamente:
        logs/YYYY-MM-DD/signals_YYYY-MM-DD_HH-MM-SS[_tag].csv
        logs/YYYY-MM-DD/events_YYYY-MM-DD_HH-MM-SS[_tag].log
    """

    # Colunas do CSV de sinais
    SIGNAL_COLS = [
        "timestamp", "ativo", "direcao", "timeframe",
        "confianca_pct", "status",
        "ema_a", "ema_b", "ema_c", "ema_d",
        "donchian_up", "donchian_dn",
        "ta", "tb", "enc", "env",
        "tendencia_confirmada", "motivo",
    ]

    def __init__(self, log_dir: str = "logs", session_tag: str = "") -> None:
        today = date.today().isoformat()           # YYYY-MM-DD
        ts    = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        tag   = f"_{session_tag}" if session_tag else ""

        day_folder = Path(log_dir) / today
        day_folder.mkdir(parents=True, exist_ok=True)

        self._csv_path = day_folder / f"signals_{ts}{tag}.csv"
        self._log_path = day_folder / f"events_{ts}{tag}.log"

        # Inicializa CSV com cabeçalho
        with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.SIGNAL_COLS).writeheader()

        # Abre log de eventos em modo append
        self._log_fh = open(self._log_path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

        self._event(f"Sessão iniciada | csv={self._csv_path.name}")

    # --- API pública ---

    def write_signal(self, row: dict) -> None:
        """Grava uma linha no CSV de sinais."""
        with self._lock:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.SIGNAL_COLS, extrasaction="ignore")
                w.writerow(row)

    def event(self, msg: str) -> None:
        """Grava uma linha no arquivo .log de eventos."""
        self._event(msg)

    def close(self) -> None:
        self._event("Sessão encerrada")
        self._log_fh.close()

    # --- Interno ---

    def _event(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with self._lock:
            self._log_fh.write(line)


# ---------------------------------------------------------------------------
# 6. INDICADORES TÉCNICOS (cálculo simples, sem pandas)
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> Optional[float]:
    """EMA simples para lista de floats (sem pandas). Retorna None se insuficiente."""
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 6)


def _donchian(highs: list[float], lows: list[float], period: int) -> tuple[Optional[float], Optional[float]]:
    """Retorna (upper, lower) do canal Donchian para o período dado."""
    if len(highs) < period or len(lows) < period:
        return None, None
    upper = max(highs[-period:])
    lower = min(lows[-period:])
    return round(upper, 6), round(lower, 6)


def compute_indicators(closes: list[float], highs: list[float], lows: list[float],
                        periods: dict) -> dict:
    """
    Calcula todos os indicadores necessários.

    periods esperado:
        ema_a, ema_b, ema_c, ema_d  → períodos das EMAs
        donchian                    → período do canal Donchian
        ta, tb                      → períodos de tendência auxiliar
    """
    result = {
        "ema_a": _ema(closes, periods["ema_a"]),
        "ema_b": _ema(closes, periods["ema_b"]),
        "ema_c": _ema(closes, periods["ema_c"]),
        "ema_d": _ema(closes, periods["ema_d"]),
    }
    result["donchian_up"], result["donchian_dn"] = _donchian(
        highs, lows, periods["donchian"]
    )
    # TA / TB: EMA de período auxiliar nos máximos/mínimos
    result["ta"] = _ema(highs, periods["ta"])
    result["tb"] = _ema(lows, periods["tb"])

    # ENC / ENV: canal de envelope (médio ± 0.1% como proxy; ajuste o percentual
    # conforme o QuadCode original ao integrar os parâmetros definitivos)
    mid = result["ema_b"]
    if mid is not None:
        result["enc"] = round(mid * 1.001, 6)
        result["env"] = round(mid * 0.999, 6)
    else:
        result["enc"] = None
        result["env"] = None

    return result


# ---------------------------------------------------------------------------
# 7. MOTOR DE SINAIS
# ---------------------------------------------------------------------------

def check_signal(ind: dict, price: float, mode: str = "op") -> dict:
    """
    Avalia os indicadores e retorna um dicionário de sinal:
        direction : "CALL" | "PUT" | None
        confirmed : bool
        confidence: float (0–100)
        reason    : str
        tendencia_confirmada: bool

    Critérios base (essência QuadCode):
      CALL: ema_a > ema_b > ema_c > ema_d  AND  price > donchian_up  AND  price > enc
      PUT : ema_a < ema_b < ema_c < ema_d  AND  price < donchian_dn  AND  price < env

    Confiança: soma ponderada dos filtros que passaram.
    """

    required = ["ema_a", "ema_b", "ema_c", "ema_d",
                 "donchian_up", "donchian_dn", "ta", "tb", "enc", "env"]
    if any(ind.get(k) is None for k in required):
        return {"direction": None, "confirmed": False, "confidence": 0.0,
                "reason": "indicadores_insuficientes", "tendencia_confirmada": False}

    ea, eb, ec, ed = ind["ema_a"], ind["ema_b"], ind["ema_c"], ind["ema_d"]
    dup, ddn       = ind["donchian_up"], ind["donchian_dn"]
    ta, tb         = ind["ta"], ind["tb"]
    enc, env_      = ind["enc"], ind["env"]

    # --- Tendência confirmada (alinhamento de EMAs) ---
    trend_up   = ea > eb > ec > ed
    trend_down = ea < eb < ec < ed
    tendencia_confirmada = trend_up or trend_down

    # --- Filtros individuais ---
    call_filters = [
        trend_up,
        price > dup,
        price > enc,
        ta > tb,
    ]
    put_filters = [
        trend_down,
        price < ddn,
        price < env_,
        ta < tb,
    ]

    call_score = sum(call_filters)
    put_score  = sum(put_filters)

    total_filters = len(call_filters)
    confidence_call = round(call_score / total_filters * 100, 1)
    confidence_put  = round(put_score  / total_filters * 100, 1)

    # Sinal confirmado: todos os filtros OK
    if call_score == total_filters:
        return {
            "direction": "CALL",
            "confirmed": True,
            "confidence": confidence_call,
            "reason": "todos_filtros_call",
            "tendencia_confirmada": tendencia_confirmada,
        }
    if put_score == total_filters:
        return {
            "direction": "PUT",
            "confirmed": True,
            "confidence": confidence_put,
            "reason": "todos_filtros_put",
            "tendencia_confirmada": tendencia_confirmada,
        }

    # Sinal detectado (parcial) mas não confirmado
    if call_score > put_score:
        return {
            "direction": "CALL",
            "confirmed": False,
            "confidence": confidence_call,
            "reason": f"filtros_call_{call_score}/{total_filters}",
            "tendencia_confirmada": tendencia_confirmada,
        }
    if put_score > call_score:
        return {
            "direction": "PUT",
            "confirmed": False,
            "confidence": confidence_put,
            "reason": f"filtros_put_{put_score}/{total_filters}",
            "tendencia_confirmada": tendencia_confirmada,
        }

    return {"direction": None, "confirmed": False, "confidence": 0.0,
            "reason": "sem_sinal", "tendencia_confirmada": tendencia_confirmada}


# ---------------------------------------------------------------------------
# 8. CONTADOR DE ENTRADAS E STOP
# ---------------------------------------------------------------------------

class EntryManager:
    """Controla o número de entradas aceitas e verifica stop win/loss."""

    def __init__(self, max_entries: int, stop_win: float, stop_loss: float) -> None:
        self.max_entries = max_entries  # 0 = sem limite
        self.stop_win    = stop_win     # 0 = desabilitado
        self.stop_loss   = stop_loss    # 0 = desabilitado
        self._accepted   = 0
        self._pnl        = 0.0

    @property
    def accepted(self) -> int:
        return self._accepted

    def can_enter(self) -> tuple[bool, str]:
        """Retorna (pode_entrar, motivo_bloqueio)."""
        if self.max_entries > 0 and self._accepted >= self.max_entries:
            return False, f"max_entradas_atingido ({self.max_entries})"
        if self.stop_win > 0 and self._pnl >= self.stop_win:
            return False, f"stop_win_atingido ({self._pnl:.2f})"
        if self.stop_loss > 0 and self._pnl <= -abs(self.stop_loss):
            return False, f"stop_loss_atingido ({self._pnl:.2f})"
        return True, ""

    def register_entry(self, pnl_result: float = 0.0) -> None:
        """Registra uma entrada aceita e seu resultado (stub: pnl_result=0)."""
        self._accepted += 1
        self._pnl      += pnl_result


# ---------------------------------------------------------------------------
# 9. STUB DE EXECUÇÃO DE ORDEM
# ---------------------------------------------------------------------------

def execute_order_stub(ativo: str, direcao: str, valor: float,
                       timeframe: int, logger: SessionLogger) -> float:
    """
    STUB: simula execução de ordem.
    Substitua por chamada real à IQ Option API quando o bot estiver pronto.

    Retorna: pnl simulado (0.0 no stub).
    """
    logger.event(
        f"[STUB] ORDEM  ativo={ativo}  dir={direcao}  "
        f"valor={valor:.2f}  tf={timeframe}m  → pnl=0.00 (stub)"
    )
    return 0.0


# ---------------------------------------------------------------------------
# 10. LOOP PRINCIPAL
# ---------------------------------------------------------------------------

def _build_signal_row(ts: str, ativo: str, direcao: str, tf: int,
                      ind: dict, sig: dict, status: str, motivo: str) -> dict:
    return {
        "timestamp":            ts,
        "ativo":                ativo,
        "direcao":              direcao or "",
        "timeframe":            tf,
        "confianca_pct":        sig["confidence"],
        "status":               status,
        "ema_a":                ind.get("ema_a", ""),
        "ema_b":                ind.get("ema_b", ""),
        "ema_c":                ind.get("ema_c", ""),
        "ema_d":                ind.get("ema_d", ""),
        "donchian_up":          ind.get("donchian_up", ""),
        "donchian_dn":          ind.get("donchian_dn", ""),
        "ta":                   ind.get("ta", ""),
        "tb":                   ind.get("tb", ""),
        "enc":                  ind.get("enc", ""),
        "env":                  ind.get("env", ""),
        "tendencia_confirmada": sig.get("tendencia_confirmada", False),
        "motivo":               motivo,
    }


def run_bot(config: dict, assets: list[str], logger: SessionLogger) -> None:
    """
    Loop principal do bot.
    - Conecta à IQ Option (stub se lib não encontrada).
    - Itera sobre ativos a cada candle fechado.
    - Avalia sinais e executa entradas controladas.
    """
    # --- Parâmetros ---
    tf          = int(config.get("timeframe", "1"))
    valor       = float(config.get("valor_entrada", "1.00"))
    modo        = config.get("modo_operacao", "op").lower()
    max_entries = int(config.get("max_entradas", "10"))
    stop_win    = float(config.get("stop_win",  "0.00"))
    stop_loss   = float(config.get("stop_loss", "0.00"))
    email       = config.get("email", "")
    senha       = config.get("senha", "")
    tipo_conta  = config.get("tipo_conta", "demo")

    # Períodos dos indicadores (ajuste conforme o QuadCode original)
    periods = {
        "ema_a":    3,
        "ema_b":    7,
        "ema_c":    17,
        "ema_d":    34,
        "donchian": 20,
        "ta":       5,
        "tb":       5,
    }

    entry_mgr = EntryManager(max_entries, stop_win, stop_loss)

    # --- Validação de modo OTC vs OP ---
    if modo == "otc":
        otc_assets = [a for a in assets if "OTC" in a.upper()]
        if not otc_assets:
            print("[AVISO] Modo OTC selecionado, mas nenhum ativo OTC em Ativos.txt.")
            logger.event("[AVISO] Nenhum ativo OTC encontrado; encerrando.")
            return
        assets_run = otc_assets
        print(f"[INFO] Modo OTC  | {len(assets_run)} ativos carregados")
    else:
        op_assets = [a for a in assets if "OTC" not in a.upper()]
        if not op_assets:
            print("[AVISO] Modo OP selecionado, mas nenhum ativo OP em Ativos.txt.")
            logger.event("[AVISO] Nenhum ativo OP encontrado; encerrando.")
            return
        assets_run = op_assets
        print(f"[INFO] Modo OP   | {len(assets_run)} ativos carregados")

    logger.event(
        f"Parâmetros: tf={tf}m  valor={valor}  modo={modo}  "
        f"max_entradas={max_entries}  stop_win={stop_win}  stop_loss={stop_loss}"
    )
    logger.event(f"Ativos: {assets_run}")

    # --- Conexão IQ Option ---
    iq = None
    try:
        from iqoptionapi.stable_api import IQ_Option  # type: ignore

        print(f"[INFO] Conectando à IQ Option ({tipo_conta})...")
        iq = IQ_Option(email, senha)

        # Timeout hard de 30 s para não travar em connect
        connected = threading.Event()
        conn_error: list = []

        def _connect():
            try:
                result, reason = iq.connect()
                if result:
                    connected.set()
                else:
                    conn_error.append(reason)
                    connected.set()
            except Exception as exc:
                conn_error.append(str(exc))
                connected.set()

        t = threading.Thread(target=_connect, daemon=True)
        t.start()
        connected.wait(timeout=30)

        if not connected.is_set() or conn_error:
            reason_str = conn_error[0] if conn_error else "timeout"
            print(f"[ERRO] Falha na conexão: {reason_str}")
            logger.event(f"[ERRO] Conexão falhou: {reason_str}")
            return

        iq.change_balance(tipo_conta.upper())
        print(f"[INFO] Conectado | conta={tipo_conta.upper()}")
        logger.event(f"Conexão OK | conta={tipo_conta.upper()}")

    except ImportError:
        print("[AVISO] iqoptionapi não encontrada. Rodando em modo SIMULAÇÃO (stub).")
        logger.event("[AVISO] iqoptionapi ausente; modo stub ativo")

    # --- Histórico de candles (buffer por ativo) ---
    candle_history: dict[str, dict] = {
        a: {"closes": [], "highs": [], "lows": []} for a in assets_run
    }

    # --- Loop de sinais ---
    print("\n[BOT] Iniciando monitoramento. Ctrl+C para encerrar.\n")
    try:
        while True:
            can_enter, block_reason = entry_mgr.can_enter()
            if not can_enter:
                print(f"\n[BOT] Encerramento gracioso: {block_reason}")
                logger.event(f"Encerramento gracioso: {block_reason}")
                break

            for ativo in assets_run:
                ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # --- Obter candles (real ou simulado) ---
                closes, highs, lows = _fetch_candles(iq, ativo, tf, candle_history)

                if len(closes) < max(periods.values()):
                    # Histórico ainda insuficiente para calcular todos os indicadores
                    continue

                price = closes[-1]
                ind   = compute_indicators(closes, highs, lows, periods)
                sig   = check_signal(ind, price, mode=modo)

                # --- Lógica de status ---
                if sig["confirmed"]:
                    can_enter_now, blk = entry_mgr.can_enter()
                    if can_enter_now:
                        status = "CONFIRMADO"
                        motivo = sig["reason"]
                        # Console: SOMENTE sinais confirmados
                        print(
                            f"[SINAL] {ts_now}  {ativo}  {sig['direction']}  "
                            f"conf={sig['confidence']}%  tf={tf}m  [{status}]"
                        )
                        logger.event(
                            f"CONFIRMADO  ativo={ativo}  dir={sig['direction']}  "
                            f"conf={sig['confidence']}%  motivo={motivo}"
                        )
                        # Executa ordem (stub)
                        pnl = execute_order_stub(ativo, sig["direction"], valor, tf, logger)
                        entry_mgr.register_entry(pnl)
                    else:
                        status = "BLOQUEADO"
                        motivo = blk
                        logger.event(
                            f"BLOQUEADO  ativo={ativo}  dir={sig['direction']}  "
                            f"conf={sig['confidence']}%  motivo={motivo}"
                        )
                elif sig["direction"] is not None:
                    status = "DETECTADO"
                    motivo = sig["reason"]
                    logger.event(
                        f"DETECTADO  ativo={ativo}  dir={sig['direction']}  "
                        f"conf={sig['confidence']}%  motivo={motivo}"
                    )
                else:
                    status = "SEM_SINAL"
                    motivo = sig["reason"]
                    # Sem sinal: loga apenas no arquivo, sem print no console
                    logger.event(f"SEM_SINAL  ativo={ativo}  motivo={motivo}")

                # Sempre grava no CSV (para análise posterior)
                row = _build_signal_row(
                    ts_now, ativo, sig.get("direction"), tf,
                    ind, sig, status, motivo
                )
                logger.write_signal(row)

            # Aguarda próximo "tick" (intervalo reduzido para simulação)
            _sleep_until_next_candle(tf, stub=(iq is None))

    except KeyboardInterrupt:
        print("\n[BOT] Interrompido pelo usuário.")
        logger.event("Interrompido pelo usuário (KeyboardInterrupt)")


# ---------------------------------------------------------------------------
# 11. HELPERS DE CANDLES
# ---------------------------------------------------------------------------

def _fetch_candles(iq, ativo: str, tf: int,
                   history: dict) -> tuple[list[float], list[float], list[float]]:
    """
    Obtém candles do ativo.
    - Se iq disponível: tenta via API com timeout hard de 15 s.
    - Caso contrário (stub): usa valores simulados.
    """
    if iq is None:
        return _stub_candles(ativo, history)

    result_holder: list = []
    done = threading.Event()

    def _get():
        try:
            # Pede 50 candles para ter buffer suficiente
            candles = iq.get_candles(ativo, tf * 60, 50, time.time())
            result_holder.append(candles)
        except Exception as exc:
            result_holder.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_get, daemon=True)
    t.start()
    done.wait(timeout=15)

    if not done.is_set() or not result_holder or isinstance(result_holder[0], Exception):
        # Timeout ou erro: retorna histórico anterior
        h = history[ativo]
        return h["closes"], h["highs"], h["lows"]

    candles = result_holder[0]
    if not candles:
        h = history[ativo]
        return h["closes"], h["highs"], h["lows"]

    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["max"])   for c in candles]
    lows   = [float(c["min"])   for c in candles]

    history[ativo] = {"closes": closes, "highs": highs, "lows": lows}
    return closes, highs, lows


def _stub_candles(ativo: str, history: dict) -> tuple[list[float], list[float], list[float]]:
    """Gera candles simulados para teste sem API."""
    import random
    h = history[ativo]
    last = h["closes"][-1] if h["closes"] else 1.1000

    # Caminhada aleatória simples
    close = round(last + random.uniform(-0.0010, 0.0010), 5)
    high  = round(close + random.uniform(0.0001, 0.0005), 5)
    low   = round(close - random.uniform(0.0001, 0.0005), 5)

    h["closes"] = (h["closes"] + [close])[-60:]
    h["highs"]  = (h["highs"]  + [high])[-60:]
    h["lows"]   = (h["lows"]   + [low])[-60:]

    return h["closes"], h["highs"], h["lows"]


def _sleep_until_next_candle(tf: int, stub: bool = False) -> None:
    """
    Aguarda até o próximo candle fechado.
    - stub=True: dorme 1 s (para testes rápidos).
    - stub=False: dorme tf minutos menos segundos já decorridos.
    """
    if stub:
        time.sleep(1)
        return

    now     = datetime.now()
    seconds = now.second + now.microsecond / 1e6
    wait    = (tf * 60) - (seconds % (tf * 60))
    # Adiciona 0.5 s de margem para garantir candle fechado
    time.sleep(max(wait + 0.5, 1))


# ---------------------------------------------------------------------------
# 12. MENU INTERATIVO
# ---------------------------------------------------------------------------

def _menu(config: dict) -> dict:
    """Menu simples de texto para confirmar ou ajustar parâmetros."""
    print("=" * 55)
    print("        MetodoBOTDIN  —  Menu de Inicialização")
    print("=" * 55)
    print(f"  Email       : {config.get('email', '?')}")
    print(f"  Conta       : {config.get('tipo_conta', 'demo')}")
    print(f"  Modo        : {config.get('modo_operacao', 'op').upper()}")
    print(f"  Timeframe   : {config.get('timeframe', '1')}m")
    print(f"  Valor/entrada: {config.get('valor_entrada', '1.00')}")  # unidade conforme conta
    print(f"  Max entradas: {config.get('max_entradas', '10')}")
    print(f"  Stop Win    : {config.get('stop_win', '0.00')}")
    print(f"  Stop Loss   : {config.get('stop_loss', '0.00')}")
    print("=" * 55)
    print("Opções:")
    print("  1) Iniciar com configuração acima (OP)")
    print("  2) Iniciar modo OTC")
    print("  3) Sair")
    print()

    while True:
        choice = input("Escolha [1/2/3]: ").strip()
        if choice == "1":
            config["modo_operacao"] = "op"
            return config
        if choice == "2":
            config["modo_operacao"] = "otc"
            return config
        if choice == "3":
            print("Encerrando.")
            sys.exit(0)
        print("Opção inválida. Tente novamente.")


# ---------------------------------------------------------------------------
# 13. ENTRY POINT
# ---------------------------------------------------------------------------

def _ensure_runtime_dirs() -> None:
    """Cria as pastas de runtime que não devem ir ao git."""
    for folder in ("logs", "state", "presets"):
        Path(folder).mkdir(exist_ok=True)


def main() -> None:
    _ensure_runtime_dirs()

    # Carrega configuração
    config_path = "config.txt"
    if not Path(config_path).exists():
        print(f"[ERRO] Arquivo {config_path} não encontrado.")
        print("       Crie-o a partir do template e preencha suas credenciais.")
        sys.exit(1)

    config = load_config(config_path)

    # Diagnóstico de ambiente
    _print_diagnostics(config)

    # Patch websocket (se necessário)
    _apply_ws_patch_if_needed(config)

    # Carrega ativos
    ativos_path = "Ativos.txt"
    if not Path(ativos_path).exists():
        print(f"[ERRO] Arquivo {ativos_path} não encontrado.")
        sys.exit(1)

    assets = load_assets(ativos_path)
    if not assets:
        print("[ERRO] Nenhum ativo encontrado em Ativos.txt.")
        sys.exit(1)

    print(f"[INFO] {len(assets)} ativos carregados de {ativos_path}")

    # Menu de inicialização
    config = _menu(config)

    # Cria logger de sessão
    log_dir     = config.get("log_dir", "logs")
    session_tag = config.get("session_tag", "").strip()
    # Adiciona modo e número de ativos à tag automática se tag vazia
    tf   = config.get("timeframe", "1")
    modo = config.get("modo_operacao", "op")
    if not session_tag:
        session_tag = f"m{tf}_{modo}_{len(assets)}ativos"

    logger = SessionLogger(log_dir=log_dir, session_tag=session_tag)
    print(f"[INFO] Logs em: {log_dir}/{date.today().isoformat()}/")

    try:
        run_bot(config, assets, logger)
    finally:
        logger.close()
        print("[BOT] Encerrado.")


if __name__ == "__main__":
    main()
