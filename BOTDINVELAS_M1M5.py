#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import time
import json
import sys
import threading
import csv
import statistics
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from configobj import ConfigObj
from iqoptionapi.stable_api import IQ_Option

BOTDIN_VERSION = "2026-03-25-m5-minimal-multiativo"

# =========================
# CONFIG
# =========================
config = ConfigObj('config.txt')
email = config['LOGIN']['email']
senha = config['LOGIN']['senha']
tipo_default = config['AJUSTES'].get('tipo', 'binarias').strip().lower()

DEBUG = False

IDLE_SLEEP_S_M1 = 0.55
IDLE_SLEEP_S_M5 = 1.50
PENDING_SLEEP_S_M1 = 0.70
PENDING_SLEEP_S_M5 = 1.10
PENDING_PRINT_THROTTLE_S = 12.0

RESULT_DELAY_AFTER_EXPIRY_SECONDS = 20

# =========================
# AGENDAMENTO / ATIVO FECHADO
# =========================
ALLOW_CLOSED_ASSET_IF_SCHEDULED = True
WAIT_ASSET_OPEN_IF_SCHEDULED = True
WAIT_ASSET_OPEN_TIMEOUT_SECONDS = 30 * 60  # 30 min
WAIT_ASSET_OPEN_CHECK_EVERY_SECONDS = 8
WAIT_ASSET_OPEN_PRINT_EVERY_SECONDS = 30

# =========================
# PASTAS / PATHS
# =========================
BASE_DIR = Path('.')
LOG_DIR = BASE_DIR / 'logs'
STATE_DIR = BASE_DIR / 'state'
PRESETS_DIR = BASE_DIR / 'presets'
STATE_PATH = STATE_DIR / 'bot_state.json'
FAVORITES_FILE = BASE_DIR / 'favoritos.txt'

INSTANCE_TAG = "unset"

BLOCKED_LOG: Optional[Path] = None
LATENCY_CSV: Optional[Path] = None
TRADES_CSV: Optional[Path] = None
PATTERNS_CSV: Optional[Path] = None
ERRORS_LOG: Optional[Path] = None


def _mkdirp(p: Path):
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _sanitize_tag(tag: str) -> str:
    tag = (tag or "").strip()
    if not tag:
        return "default"
    tag = tag.replace(" ", "_")
    tag = re.sub(r"[^A-Za-z0-9_\-\.]+", "", tag)
    return tag[:40] if len(tag) > 40 else tag


def _init_paths_with_tag(tag: str):
    global INSTANCE_TAG, BLOCKED_LOG, LATENCY_CSV, TRADES_CSV, PATTERNS_CSV, ERRORS_LOG

    _mkdirp(LOG_DIR)
    _mkdirp(STATE_DIR)
    _mkdirp(PRESETS_DIR)

    INSTANCE_TAG = _sanitize_tag(tag)
    BLOCKED_LOG = LOG_DIR / f'blocked_reasons_{INSTANCE_TAG}.log'
    LATENCY_CSV = LOG_DIR / f'latency_log_{INSTANCE_TAG}.csv'
    TRADES_CSV = LOG_DIR / f'trades_log_{INSTANCE_TAG}.csv'
    PATTERNS_CSV = LOG_DIR / f'patterns_log_{INSTANCE_TAG}.csv'
    ERRORS_LOG = LOG_DIR / f'runtime_errors_{INSTANCE_TAG}.log'


# =========================
# GLOBAIS
# =========================
API = None
conta = None  # PRACTICE/REAL
tipo = tipo_default  # binary/digital

PURCHASE_BUFFER_SECONDS = int(config.get('AJUSTES', {}).get('purchase_buffer_seconds', 8))

USE_BUY_THREAD = True
BUY_LATENCY_AVG = 0.9
BUY_LATENCY_ALPHA = 0.4
BUY_LATENCY_MARGIN = 1.0

CANDLES_LOOKBACK = 320
MIN_CANDLES_REQUIRED = 120

ENTRY_MODE = "reversal"
TIMEFRAME_MINUTES = 1
PENDING_EXPIRE_CANDLES = 2

ENABLE_ATR_FILTER = True
ATR_PERIOD = 14
ATR_ADAPTIVE_WINDOW = 30
ATR_ADAPTIVE_FACTOR = 0.70
ATR_MIN_RATIO_ABS_M1 = 0.000035
ATR_MIN_RATIO_ABS_M5 = 0.000020
ATR_RATIO_QUEUE_M1 = deque(maxlen=ATR_ADAPTIVE_WINDOW)
ATR_RATIO_QUEUE_M5 = deque(maxlen=ATR_ADAPTIVE_WINDOW)

ENABLE_TREND_STRENGTH_FILTER = True
ADX_PERIOD = 14
ADX_MIN_M1 = 17.0
ADX_MIN_M5 = 18.0
BB_PERIOD = 20
BB_STD = 2.0
BB_WIDTH_MIN_M1 = 0.00045
BB_WIDTH_MIN_M5 = 0.00070
SLOPE_LOOKBACK = 8
SLOPE_MIN_M1 = 0.00008
SLOPE_MIN_M5 = 0.00012

ENTRY_WINDOW_SECONDS_M1 = 8
ENTRY_WINDOW_SECONDS_M5 = 25

OPEN_TIME_CACHE_TTL_S = 15
_last_open_time_cache: Dict[Tuple[str, Optional[str]], Tuple[bool, float]] = {}

RIGIDEZ_MODE = "normal"

AMOUNT_MODE = "fixed"
AMOUNT_FIXED = 1.0
AMOUNT_PERCENT = 1.0
AMOUNT_RECALC_EACH = True
AMOUNT_MIN = 0.01

STOP_LOSS_PCT = 0.0
STOP_WIN_PCT = 0.0

BLOCKED_COUNTERS = defaultdict(int)

pending: Optional[Dict[str, Any]] = None
pending_id_active: Optional[Tuple[str, int, str, int]] = None
pending_lock_until_ts: int = 0
last_pending_print_ts_by_id: Dict[Tuple[str, int, str, int], float] = {}
_last_pending_status_printed_for_id: Optional[Tuple[str, int, str, int]] = None

# =========================
# RESULTADOS
# =========================
EXTRA_WAIT_SECONDS = 12
M1_RESULT_TIMEOUT = 80
M5_RESULT_TIMEOUT = 260

EARLY_LOSS_GUARD_SECONDS = 55
EARLY_LOSS_STABLE_SAMPLES = 4
EARLY_LOSS_SAMPLE_INTERVAL_S = 1.5
EARLY_LOSS_EPS = 0.02

# Presets
PRESET_PATH: Optional[Path] = None


# =========================
# CSV
# =========================
def _ensure_csv_headers():
    assert LATENCY_CSV is not None
    assert TRADES_CSV is not None
    assert PATTERNS_CSV is not None

    if not LATENCY_CSV.exists():
        with LATENCY_CSV.open('w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['ts_iso', 'instance_tag', 'delta_s', 'buy_latency_avg_s', 'method'])
    if not TRADES_CSV.exists():
        with TRADES_CSV.open('w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                'ts_iso', 'instance_tag',
                'ativo', 'tf_min', 'entry_mode', 'rigidez',
                'direcao', 'order_id',
                'result_method', 'result', 'profit',
                'balance_before', 'balance_after',
                'amount_used', 'buy_latency_avg_s',
                'pattern_name', 'pattern_from',
                'secs_left_at_buy'
            ])
    if not PATTERNS_CSV.exists():
        with PATTERNS_CSV.open('w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                'ts_iso', 'instance_tag',
                'ativo', 'tf_min', 'event',
                'pattern_name', 'pattern_from', 'expected_confirm_from',
                'mode', 'direction_hint', 'confirmed', 'confirm_from', 'details'
            ])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def server_hhmmss() -> str:
    try:
        ts = int(API.get_server_timestamp())
    except Exception:
        ts = int(time.time())
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _log_error(msg: str, exc: Optional[BaseException] = None):
    try:
        if ERRORS_LOG is not None:
            with ERRORS_LOG.open('a', encoding='utf-8') as f:
                f.write(f"{datetime.now().isoformat()} | {INSTANCE_TAG} | {msg}\n")
                if exc is not None:
                    f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
                    f.write("\n")
    except Exception:
        pass
    if DEBUG and exc:
        traceback.print_exc()


def _log_blocked(reason: str, details: Optional[str] = None):
    BLOCKED_COUNTERS[reason] += 1
    try:
        if BLOCKED_LOG is not None:
            with BLOCKED_LOG.open('a', encoding='utf-8') as f:
                ts = datetime.now().isoformat()
                f.write(f"{ts} | {INSTANCE_TAG} | {reason} | {details or ''}\n")
    except Exception:
        pass


def console_event(line: str):
    print(line)


# =========================
# Display helpers
# =========================
def display_asset_name(asset: str) -> str:
    if not isinstance(asset, str):
        return str(asset)
    s = asset
    s = re.sub(r'-otc\b', '-OTC', s, flags=re.IGNORECASE)
    s = re.sub(r'-op\b', '-OP', s, flags=re.IGNORECASE)
    return s


def fmt_money_signed(v: Optional[float]) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.2f}"
    except Exception:
        return ""


def fmt_result_line(label: str, profit: Optional[float], method: Optional[str]) -> str:
    _ = method  # console não mostra método
    partes = [f"Resultado: {label.upper()}"]
    if profit is not None:
        partes.append(f"Profit: {fmt_money_signed(profit)}")
    return " | ".join(partes)


# =========================
# Presets / Auto-tag
# =========================
def _asset_key_for_preset(asset: str) -> str:
    s = str(asset).upper().replace("-", "")
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s or "ASSET"


def _mode_key_for_preset(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return "REV" if m == "reversal" else ("BRK" if m == "breakout" else "MODE")


def _rigidez_key_for_preset(rigidez: str) -> str:
    r = str(rigidez or "").strip().lower()
    return "RIG" if r == "rigida" else ("NOR" if r == "normal" else "RIGZ")


def _preset_filename(asset: str, tf_min: int, mode: str, rigidez: str) -> str:
    a = _asset_key_for_preset(asset)
    m = _mode_key_for_preset(mode)
    r = _rigidez_key_for_preset(rigidez)
    tf = f"M{int(tf_min)}"
    return f"{a}_{m}_{r}_{tf}.json"


def _auto_tag_from_choices(asset: str, tf_min: int, mode: str, rigidez: str) -> str:
    return _preset_filename(asset, tf_min, mode, rigidez).replace(".json", "")


def build_preset_dict(ativo: Optional[str] = None, ativo_chave: Optional[str] = None,
                      runtime_min: Optional[int] = None) -> Dict[str, Any]:
    account = "demo" if conta == "PRACTICE" else ("real" if conta == "REAL" else "")
    return {
        "created_at": now_iso(),
        "instance_tag": INSTANCE_TAG,
        "account": account,
        "tradetype": str(tipo).lower(),
        "asset": ativo or "",
        "asset_category": ativo_chave or "",
        "tf_min": int(TIMEFRAME_MINUTES),
        "entry_mode": ENTRY_MODE,
        "rigidez": RIGIDEZ_MODE,
        "runtime_min": runtime_min if runtime_min is not None else "",
        "amount_mode": AMOUNT_MODE,
        "amount_fixed": float(AMOUNT_FIXED) if AMOUNT_MODE == "fixed" else "",
        "amount_percent": float(AMOUNT_PERCENT) if AMOUNT_MODE == "percent" else "",
        "amount_recalc_each": bool(AMOUNT_RECALC_EACH),
        "stop_loss_pct": float(STOP_LOSS_PCT),
        "stop_win_pct": float(STOP_WIN_PCT),
        "result_delay_after_expiry_seconds": int(RESULT_DELAY_AFTER_EXPIRY_SECONDS),
        "bot_version": BOTDIN_VERSION,
    }


def write_preset_file(preset_path: Path, preset_data: Dict[str, Any]):
    _mkdirp(PRESETS_DIR)
    try:
        with preset_path.open("w", encoding="utf-8") as f:
            json.dump(preset_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log_error(f"Falha ao salvar preset em {preset_path}", e)


# =========================
# IQ connect / utils
# =========================
def connect():
    global API
    print('BOTDIN_VERSION =', BOTDIN_VERSION)
    print('🔌 Conectando na IQ Option...')
    API = IQ_Option(email, senha)
    ok, reason = API.connect()
    if not ok:
        print('\n❌ Falha na conexão:', reason)
        sys.exit(1)
    print('✅ Conectado.')


def get_profile_name() -> str:
    try:
        perfil = json.loads(json.dumps(API.get_profile_ansyc()))
        return str(perfil.get('name', '') or '').strip()
    except Exception:
        return ""


def get_available_balance():
    try:
        bal = API.get_balance()
        if isinstance(bal, dict):
            if 'available' in bal:
                return float(bal['available'])
            if 'result' in bal and isinstance(bal['result'], dict) and 'available' in bal['result']:
                return float(bal['result']['available'])
            for v in bal.values():
                if isinstance(v, (int, float)):
                    return float(v)
            return None
        if isinstance(bal, (int, float)):
            return float(bal)
    except Exception:
        return None
    return None


def seconds_left_in_period(minutes: int) -> int:
    try:
        ts = API.get_server_timestamp()
        period = minutes * 60
        return int(period - (ts % period))
    except Exception:
        return 0


def _normalize_asset_name(name: str) -> str:
    if not isinstance(name, str):
        return ''
    s = name.upper()
    s = re.sub(r'[^A-Z0-9\-]', '', s)
    return s


def _categories_priority(preferred_tipo):
    preferred = 'digital' if 'digital' in str(preferred_tipo).lower() else 'binary'
    order = []
    for c in (preferred, 'binary' if preferred != 'binary' else 'digital', 'turbo'):
        if c not in order:
            order.append(c)
    return order


def _parse_user_asset_input(raw: str) -> Dict[str, Any]:
    s = (raw or "").strip()
    s2 = re.sub(r'\s+', '', s)
    upper = s2.upper()

    suffix = None
    if upper.endswith("-OTC"):
        suffix = "OTC"
    elif upper.endswith("-OP"):
        suffix = "OP"

    allow_otc = (suffix == "OTC")
    return {"base": upper, "suffix": suffix, "allow_otc": allow_otc}


def _is_open(open_times: Dict[str, Any], categoria: str, ativo: str) -> bool:
    try:
        info = open_times.get(categoria, {}).get(ativo)
        return isinstance(info, dict) and bool(info.get("open"))
    except Exception:
        return False


def _find_open_in_table(open_times: Dict[str, Any], ativo: str) -> Tuple[Optional[str], Optional[str]]:
    for cat in _categories_priority(tipo):
        if _is_open(open_times, cat, ativo):
            return ativo, cat
    return None, None


def find_preferred_variant_with_rules(base: str, allow_otc: bool) -> Tuple[Optional[str], Optional[str]]:
    try:
        ot = API.get_all_open_time()
    except Exception:
        return None, None

    base_n = _normalize_asset_name(base)

    name, cat = _find_open_in_table(ot, base)
    if name:
        return name, cat

    candidates_common: List[Tuple[str, str]] = []
    candidates_op: List[Tuple[str, str]] = []
    candidates_otc: List[Tuple[str, str]] = []

    for categoria in ('binary', 'digital', 'turbo'):
        table = ot.get(categoria, {})
        for name2, info in table.items():
            if not (isinstance(info, dict) and info.get('open')):
                continue

            name2_u = str(name2).upper()
            name2_n = _normalize_asset_name(name2_u)

            if name2_n == base_n:
                return name2, categoria
            if base_n and (base_n in name2_n):
                if '-OTC' in name2_u:
                    candidates_otc.append((name2, categoria))
                elif '-OP' in name2_u:
                    if name2_n.startswith(base_n):
                        candidates_op.append((name2, categoria))
                else:
                    candidates_common.append((name2, categoria))

    if candidates_common:
        for c in _categories_priority(tipo):
            for n, cat in candidates_common:
                if cat == c:
                    return n, cat
        return candidates_common[0]

    if candidates_op:
        for c in _categories_priority(tipo):
            for n, cat in candidates_op:
                if cat == c:
                    return n, cat
        return candidates_op[0]

    if allow_otc and candidates_otc:
        for c in _categories_priority(tipo):
            for n, cat in candidates_otc:
                if cat == c:
                    return n, cat
        return candidates_otc[0]

    return None, None


def ativo_aberto(ativo, chave_preferida=None) -> bool:
    cache_key = (ativo, chave_preferida)
    nowt = time.time()
    if cache_key in _last_open_time_cache:
        val, ts = _last_open_time_cache[cache_key]
        if nowt - ts <= OPEN_TIME_CACHE_TTL_S:
            return bool(val)
    try:
        open_times = API.get_all_open_time()
        if not open_times:
            _last_open_time_cache[cache_key] = (False, nowt)
            return False
        if chave_preferida:
            info = open_times.get(chave_preferida, {}).get(ativo)
            ok = isinstance(info, dict) and info.get('open', False)
            _last_open_time_cache[cache_key] = (ok, nowt)
            return ok
        for k in ('binary', 'digital', 'turbo'):
            info = open_times.get(k, {}).get(ativo)
            if isinstance(info, dict):
                ok = info.get('open', False)
                _last_open_time_cache[cache_key] = (ok, nowt)
                return ok
    except Exception:
        _last_open_time_cache[cache_key] = (False, nowt)
        return False
    _last_open_time_cache[cache_key] = (False, nowt)
    return False


def is_asset_known_anywhere_case_insensitive(ativo: str) -> bool:
    try:
        target_n = _normalize_asset_name(ativo)
        ot = API.get_all_open_time()
        for cat in ('binary', 'digital', 'turbo'):
            table = ot.get(cat, {})
            if not isinstance(table, dict):
                continue
            for k in table.keys():
                if _normalize_asset_name(str(k)) == target_n:
                    return True
    except Exception:
        return False
    return False


def can_purchase_now(ativo, period_minutes=1, chave_preferida=None):
    if not ativo_aberto(ativo, chave_preferida=chave_preferida):
        return False
    return seconds_left_in_period(period_minutes) > PURCHASE_BUFFER_SECONDS


def get_candles_safe(ativo: str, timeframe: int, qnt: int, max_tentativas=6):
    for _ in range(max_tentativas):
        try:
            end_ts = API.get_server_timestamp()
            velas = API.get_candles(ativo, timeframe, qnt, end_ts)
            if velas and len(velas) >= qnt:
                return velas
        except Exception as e:
            _log_error("Erro ao buscar candles.", e)
        time.sleep(0.5)
    return None


# =========================
# Indicadores / filtros
# =========================
def ema_series(values: List[float], period: int) -> List[Optional[float]]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out: List[Optional[float]] = [None] * (period - 1) + [ema]
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
        out.append(ema)
    return out


def ema_slope_norm(closes: List[float], period: int, lookback: int) -> Optional[float]:
    if len(closes) < period + lookback + 5:
        return None
    es = ema_series(closes, period)
    if not es or es[-1] is None:
        return None
    idx2 = len(es) - 1
    idx1 = idx2 - lookback
    if idx1 < 0:
        return None
    e2, e1 = es[idx2], es[idx1]
    if e1 is None or e2 is None:
        return None
    base = closes[-1] if closes[-1] != 0 else 1e-12
    return abs(e2 - e1) / base


def calculate_atr_from_candles(velas: List[Dict[str, Any]], periodo=14) -> Optional[float]:
    if not velas or len(velas) < periodo + 1:
        return None
    trs = []
    for i in range(1, len(velas)):
        high = velas[i]['max']
        low = velas[i]['min']
        prev_close = velas[i - 1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < periodo:
        return None
    return sum(trs[-periodo:]) / periodo


def adaptive_atr_threshold_update(tf_min: int, atr_ratio: Optional[float]) -> float:
    base = ATR_MIN_RATIO_ABS_M5 if tf_min == 5 else ATR_MIN_RATIO_ABS_M1
    q = ATR_RATIO_QUEUE_M5 if tf_min == 5 else ATR_RATIO_QUEUE_M1
    if atr_ratio is None:
        return base
    try:
        q.append(float(atr_ratio))
        if len(q) < 10:
            return base
        med = statistics.median(list(q))
        dyn = max(base, med * ATR_ADAPTIVE_FACTOR)
        return max(base, dyn)
    except Exception:
        return base


def passes_atr_filter(tf_min: int, velas: List[Dict[str, Any]]) -> bool:
    if not ENABLE_ATR_FILTER:
        return True
    atr = calculate_atr_from_candles(velas, periodo=ATR_PERIOD)
    if atr is None:
        return False
    closes = [float(v["close"]) for v in velas]
    mean_close = sum(closes[-ATR_PERIOD:]) / ATR_PERIOD if len(closes) >= ATR_PERIOD else closes[-1]
    if mean_close == 0:
        return False
    ratio = atr / mean_close
    thr = adaptive_atr_threshold_update(tf_min, ratio)
    if ratio < thr:
        _log_blocked("atr_low", f"tf={tf_min} ratio={ratio:.6f} thr={thr:.6f}")
        return False
    return True


def bb_width_norm(closes: List[float], period: int = 20, std_mult: float = 2.0) -> Optional[float]:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    sd = var ** 0.5
    upper = mean + std_mult * sd
    lower = mean - std_mult * sd
    mid = mean if mean != 0 else 1e-12
    return (upper - lower) / mid


def adx_from_candles(velas: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if not velas or len(velas) < period + 2:
        return None
    highs = [float(v["max"]) for v in velas]
    lows = [float(v["min"]) for v in velas]
    closes = [float(v["close"]) for v in velas]
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(velas)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        pdm = up_move if (up_move > down_move and up_move > 0) else 0.0
        mdm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        plus_dm.append(pdm)
        minus_dm.append(mdm)
        tr_list.append(tr)
    if len(tr_list) < period + 1:
        return None

    def wilder(values: List[float], p: int) -> List[float]:
        first = sum(values[:p])
        out = [first]
        for i in range(p, len(values)):
            out.append(out[-1] - (out[-1] / p) + values[i])
        return out

    tr_s = wilder(tr_list, period)
    pdm_s = wilder(plus_dm, period)
    mdm_s = wilder(minus_dm, period)
    n = min(len(tr_s), len(pdm_s), len(mdm_s))
    tr_s, pdm_s, mdm_s = tr_s[-n:], pdm_s[-n:], mdm_s[-n:]
    dx = []
    for i in range(n):
        trv = tr_s[i]
        if trv == 0:
            dx.append(0.0)
            continue
        pdi = 100.0 * (pdm_s[i] / trv)
        mdi = 100.0 * (mdm_s[i] / trv)
        denom = pdi + mdi
        dx.append(0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom)
    adx_s = wilder(dx, period)
    if not adx_s:
        return None
    return float(adx_s[-1] / period)


def passes_trend_strength_filter(tf_min: int, velas: List[Dict[str, Any]]) -> bool:
    if not ENABLE_TREND_STRENGTH_FILTER:
        return True

    closes = [float(v["close"]) for v in velas]
    adx_min = ADX_MIN_M5 if tf_min == 5 else ADX_MIN_M1
    bb_min = BB_WIDTH_MIN_M5 if tf_min == 5 else BB_WIDTH_MIN_M1
    slope_min = SLOPE_MIN_M5 if tf_min == 5 else SLOPE_MIN_M1

    adx = adx_from_candles(velas, period=ADX_PERIOD)
    if adx is None or adx < adx_min:
        _log_blocked("trend_weak_adx", f"tf={tf_min} adx={adx}")
        return False

    bbw = bb_width_norm(closes, period=BB_PERIOD, std_mult=BB_STD)
    if bbw is None or bbw < bb_min:
        _log_blocked("range_squeeze_bbw", f"tf={tf_min} bbw={bbw}")
        return False

    slope = ema_slope_norm(closes, period=21, lookback=SLOPE_LOOKBACK)
    if slope is None or slope < slope_min:
        _log_blocked("ema_flat_slope", f"tf={tf_min} slope={slope}")
        return False

    return True


# =========================
# Patterns
# =========================
def _find_candle_by_from(velas: List[Dict[str, Any]], from_ts: int) -> Optional[Dict[str, Any]]:
    for c in velas:
        if int(c.get("from", -1)) == int(from_ts):
            return c
    return None


def _candle_parts(c: Dict[str, Any]) -> Dict[str, float]:
    o = float(c['open'])
    cl = float(c['close'])
    h = float(c['max'])
    l = float(c['min'])
    return {"open": o, "close": cl, "high": h, "low": l}


def is_hammer(c) -> bool:
    p = _candle_parts(c)
    body = abs(p["close"] - p["open"])
    rng = max(1e-12, (p["high"] - p["low"]))
    upper = p["high"] - max(p["open"], p["close"])
    lower = min(p["open"], p["close"]) - p["low"]
    if (body / rng) > 0.35:
        return False
    if lower < 2.0 * max(body, 1e-12):
        return False
    if upper > 0.8 * max(body, 1e-12):
        return False
    return True


def is_harami_bearish(prev_c, cur_c) -> bool:
    p0 = _candle_parts(prev_c)
    p1 = _candle_parts(cur_c)
    if not (p0["close"] > p0["open"] and p1["close"] < p1["open"]):
        return False
    high0, low0 = max(p0["open"], p0["close"]), min(p0["open"], p0["close"])
    high1, low1 = max(p1["open"], p1["close"]), min(p1["open"], p1["close"])
    if not (high1 <= high0 and low1 >= low0):
        return False
    if abs(p1["close"] - p1["open"]) >= 0.8 * abs(p0["close"] - p0["open"]):
        return False
    return True


def is_harami_bullish(prev_c, cur_c) -> bool:
    p0 = _candle_parts(prev_c)
    p1 = _candle_parts(cur_c)
    if not (p0["close"] < p0["open"] and p1["close"] > p1["open"]):
        return False
    high0, low0 = max(p0["open"], p0["close"]), min(p0["open"], p0["close"])
    high1, low1 = max(p1["open"], p1["close"]), min(p1["open"], p1["close"])
    if not (high1 <= high0 and low1 >= low0):
        return False
    if abs(p1["close"] - p1["open"]) >= 0.8 * abs(p0["close"] - p0["open"]):
        return False
    return True


# =========================
# MOTOR DE REVERSÃO V15
# Score/contexto/sustentação, impulso, wick, RSI, BB.
# Substitui completamente a detecção e confirmação reversal da v14.
# Padrões breakout/harami/hammer preservados como fallback.
# =========================

# --- Parâmetros fixos do motor V15 (bloco centralizado, não dispersar) ---
V15_SCORE_MIN = 80           # Score mínimo para sinal reversal V15 (0–100)
V15_CONFIRM_POLLS = 3        # Polls consecutivos de confirmação sustentada necessários
V15_RSI_PERIOD = 14          # Período RSI
V15_RSI_OVERSOLD = 30        # RSI abaixo deste valor = oversold → sinal call
V15_RSI_OVERBOUGHT = 70      # RSI acima deste valor = overbought → sinal put
V15_BB_PERIOD = 20           # Período Bollinger Bands
V15_BB_STD = 2.0             # Multiplicador de desvio padrão para BB
V15_BB_PROXIMITY = 0.20      # Fração da largura da banda para considerar "próximo do extremo"
V15_IMPULSE_LOOKBACK = 5     # Número de velas para cálculo de impulso (tendência recente)
V15_CONTEXT_LOOKBACK = 12    # Número de velas para contexto de tendência prévia
V15_WICK_RATIO = 0.45        # Wick mínimo (wick/range) para pontuar sombra longa
V15_CANDLES_NEEDED = 40      # Mínimo de velas para o motor V15 funcionar
# Thresholds internos do motor (ajustáveis para calibração fina)
V15_TREND_THRESHOLD = 0.0008   # Variação mínima relativa para considerar tendência (não sideways)
V15_IMPULSE_THRESHOLD = 0.001  # Variação mínima relativa de impulso para pontuar contexto
V15_IMPULSE_MULTIPLIER = 8000  # Fator de escala: impulso*fator → pontos (capado em 25)
V15_WICK_SCORE_MAX = 25        # Pontuação máxima por componente wick
V15_WICK_SCORE_FACTOR = 35     # Fator multiplicador: wick_ratio*fator → pontos brutos

# =========================
# FILTRO ESTRUTURAL M5 (v15.1)
# Aplicado exclusivamente no timeframe M5 para sinais V15.
# Garante que a vela candidata esteja próxima do extremo recente,
# evitando reversões "no meio do range" (zonas ruidosas).
# Para ajuste futuro: altere M5_EXTREME_CANDLES (janela) e
# M5_EXTREME_FRAC (tolerância — 0.20 = 20% mais baixos/altos).
# =========================
M5_EXTREME_CANDLES = 20   # Quantidade de velas retroativas para definir o range estrutural
M5_EXTREME_FRAC    = 0.20 # Fração do range aceita como "extremo" (20% → tolerância razoável)


def _m5_extreme_filter(direction: str, velas: List[Dict[str, Any]]) -> bool:
    """
    Filtro de localização estrutural exclusivo do M5 (v15.1).

    Verifica se o fechamento da vela candidata (penúltima da lista)
    está no extremo do range das últimas M5_EXTREME_CANDLES velas:
      - CALL: close nos 20% mais BAIXOS do range  → tende a reversão de alta
      - PUT : close nos 20% mais ALTOS  do range  → tende a reversão de baixa

    A tolerância de 20% foi escolhida deliberadamente para NÃO endurecer
    demais o critério no M5 (que já é seletivo por natureza).
    Retorna True se o sinal PASSA o filtro, False se deve ser rejeitado.
    Para ajustar a rigidez: aumente M5_EXTREME_FRAC (mais permissivo)
    ou diminua (mais restritivo).
    """
    # Garante janela suficiente; se não houver velas bastantes, não bloqueia
    window = velas[-(M5_EXTREME_CANDLES + 2):-1]  # inclui candidata e N anteriores
    if len(window) < 3:
        return True

    highs  = [float(v.get("max", v.get("high", v.get("close", 0)))) for v in window]
    lows   = [float(v.get("min", v.get("low",  v.get("close", 0)))) for v in window]
    range_high = max(highs)
    range_low  = min(lows)
    range_size = range_high - range_low

    if range_size < 1e-10:
        return True  # range degenerado → não bloqueia

    candidate_close = float(window[-1].get("close", 0))
    threshold = range_size * M5_EXTREME_FRAC

    if direction == "call":
        # Aceita se fechamento está nos M5_EXTREME_FRAC mais baixos do range
        return candidate_close <= range_low + threshold
    else:  # put
        # Aceita se fechamento está nos M5_EXTREME_FRAC mais altos do range
        return candidate_close >= range_high - threshold


def _v15_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Calcula RSI (Relative Strength Index) com suavização Wilder."""
    if len(closes) < period + 2:
        return None
    # Seed com as primeiras 'period' diferenças
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    # Wilder smoothing para as velas restantes
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if abs(avg_loss) < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _v15_bollinger(closes: List[float], period: int = 20,
                   std_mult: float = 2.0) -> Optional[Tuple[float, float, float]]:
    """Retorna (upper, middle, lower) das Bandas de Bollinger."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    sd = var ** 0.5
    return mean + std_mult * sd, mean, mean - std_mult * sd


def _v15_impulse(velas: List[Dict[str, Any]], lookback: int = 5) -> Optional[float]:
    """
    Calcula impulso como variação normalizada dos fechamentos nas últimas
    'lookback' velas. Positivo = alta recente, Negativo = queda recente.
    """
    if len(velas) < lookback + 1:
        return None
    closes = [float(v["close"]) for v in velas[-(lookback + 1):]]
    base = closes[0] if abs(closes[0]) > 1e-10 else 1e-12
    return (closes[-1] - closes[0]) / abs(base)


def _v15_context(velas: List[Dict[str, Any]], lookback: int = 12) -> Optional[str]:
    """
    Retorna contexto de tendência prévia: 'downtrend', 'uptrend' ou 'sideways'.
    Analisa somente velas antes da vela candidata (excluindo as 2 últimas).
    """
    if len(velas) < lookback + 3:
        return None
    # Pega as velas antes das 2 últimas (candidata e in-progress)
    ctx_velas = velas[-(lookback + 2):-2]
    if len(ctx_velas) < 2:
        return None
    closes = [float(v["close"]) for v in ctx_velas]
    half = max(1, len(closes) // 2)
    first_avg = sum(closes[:half]) / half
    second_avg = sum(closes[half:]) / max(1, len(closes) - half)
    base = abs(first_avg) if abs(first_avg) > 1e-10 else 1e-12
    change = (second_avg - first_avg) / base
    if change < -V15_TREND_THRESHOLD:
        return "downtrend"
    if change > V15_TREND_THRESHOLD:
        return "uptrend"
    return "sideways"


def _v15_wick_score(c: Dict[str, Any]) -> Tuple[int, Optional[str]]:
    """
    Pontua a sombra (wick) da vela candidata para sinal de reversão.
      Wick inferior longo → reversão de alta (call).
      Wick superior longo → reversão de baixa (put).
    Retorna (pontos: 0–25, 'call'|'put'|None).
    """
    p = _candle_parts(c)
    rng = max(p["high"] - p["low"], 1e-12)
    lower_wick = min(p["open"], p["close"]) - p["low"]
    upper_wick = p["high"] - max(p["open"], p["close"])
    lower_ratio = lower_wick / rng
    upper_ratio = upper_wick / rng
    if lower_ratio >= V15_WICK_RATIO and lower_ratio > upper_ratio:
        # Sombra inferior dominante → call (suporte rejeitado)
        pts = int(min(V15_WICK_SCORE_MAX, lower_ratio * V15_WICK_SCORE_FACTOR))
        return pts, "call"
    if upper_ratio >= V15_WICK_RATIO and upper_ratio > lower_ratio:
        # Sombra superior dominante → put (resistência rejeitada)
        pts = int(min(V15_WICK_SCORE_MAX, upper_ratio * V15_WICK_SCORE_FACTOR))
        return pts, "put"
    return 0, None


def check_patterns(tf_min: int, velas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    MOTOR DE REVERSÃO V15
    ─────────────────────
    Detecta sinais de reversão por score composto (máximo 100 pontos):
      • RSI         (0–25 pts): oversold/overbought indica exaustão da tendência
      • BB          (0–25 pts): preço próximo das bandas extremas
      • Wick        (0–25 pts): sombra longa indica rejeição de preço
      • Impulso+Ctx (0–25 pts): tendência prévia confirma contexto reversal

    Sinal disparado quando score >= V15_SCORE_MIN (padrão 80) e
    a direção vencedora supera a oposta.

    Fallback v14: Harami Bearish/Bullish e Hammer preservados para
    casos onde o motor V15 não atinge pontuação mínima.
    """
    if not velas or len(velas) < max(V15_CANDLES_NEEDED, 6):
        return None

    period = tf_min * 60
    c_last = velas[-2]   # vela candidata (penúltima, já fechada)
    c_prev = velas[-3]   # vela anterior (para fallback harami)
    pattern_from = int(c_last.get("from", 0))
    expected_confirm_from = pattern_from + period
    closes = [float(v["close"]) for v in velas]

    # ── Componente RSI (0–25 pts) ──────────────────────────────────────────
    rsi = _v15_rsi(closes, V15_RSI_PERIOD)
    rsi_pts = 0
    rsi_dir: Optional[str] = None
    if rsi is not None:
        if rsi <= V15_RSI_OVERSOLD:
            rsi_pts, rsi_dir = 25, "call"
        elif rsi <= V15_RSI_OVERSOLD + 10:
            rsi_pts, rsi_dir = 12, "call"
        elif rsi >= V15_RSI_OVERBOUGHT:
            rsi_pts, rsi_dir = 25, "put"
        elif rsi >= V15_RSI_OVERBOUGHT - 10:
            rsi_pts, rsi_dir = 12, "put"

    # ── Componente BB (0–25 pts) ───────────────────────────────────────────
    bb = _v15_bollinger(closes, V15_BB_PERIOD, V15_BB_STD)
    bb_pts = 0
    bb_dir: Optional[str] = None
    if bb is not None:
        upper, _mid, lower = bb
        band_width = max(upper - lower, 1e-12)
        price = float(c_last.get("close", closes[-2]))
        prox_thr = band_width * V15_BB_PROXIMITY
        dist_lower = price - lower
        dist_upper = upper - price
        if dist_lower <= prox_thr and dist_lower >= 0:
            frac = max(0.0, 1.0 - dist_lower / max(prox_thr, 1e-12))
            bb_pts, bb_dir = int(frac * 25), "call"
        elif dist_lower < 0:
            # Preço abaixo da banda inferior: máximos pontos call
            bb_pts, bb_dir = 25, "call"
        elif dist_upper <= prox_thr and dist_upper >= 0:
            frac = max(0.0, 1.0 - dist_upper / max(prox_thr, 1e-12))
            bb_pts, bb_dir = int(frac * 25), "put"
        elif dist_upper < 0:
            # Preço acima da banda superior: máximos pontos put
            bb_pts, bb_dir = 25, "put"

    # ── Componente Wick (0–25 pts) ─────────────────────────────────────────
    wick_pts, wick_dir = _v15_wick_score(c_last)

    # ── Componente Impulso + Contexto (0–25 pts) ───────────────────────────
    # Tendência prévia de queda + impulso negativo → contexto para call (reversão)
    # Tendência prévia de alta + impulso positivo → contexto para put (reversão)
    impulse = _v15_impulse(velas, V15_IMPULSE_LOOKBACK)
    context = _v15_context(velas, V15_CONTEXT_LOOKBACK)
    imp_pts = 0
    imp_dir: Optional[str] = None
    if impulse is not None and context is not None:
        if context == "downtrend" and impulse < -V15_IMPULSE_THRESHOLD:
            imp_pts = int(min(V15_WICK_SCORE_MAX, abs(impulse) * V15_IMPULSE_MULTIPLIER))
            imp_dir = "call"
        elif context == "uptrend" and impulse > V15_IMPULSE_THRESHOLD:
            imp_pts = int(min(V15_WICK_SCORE_MAX, abs(impulse) * V15_IMPULSE_MULTIPLIER))
            imp_dir = "put"

    # ── Soma de scores por direção ─────────────────────────────────────────
    call_score = (rsi_pts if rsi_dir == "call" else 0) + \
                 (bb_pts if bb_dir == "call" else 0) + \
                 (wick_pts if wick_dir == "call" else 0) + \
                 (imp_pts if imp_dir == "call" else 0)
    put_score  = (rsi_pts if rsi_dir == "put" else 0) + \
                 (bb_pts if bb_dir == "put" else 0) + \
                 (wick_pts if wick_dir == "put" else 0) + \
                 (imp_pts if imp_dir == "put" else 0)

    # ── Disparo do sinal V15 ───────────────────────────────────────────────
    if call_score >= V15_SCORE_MIN and call_score > put_score:
        # ── Filtro estrutural M5 (v15.1): só aceita sinal no extremo do range ──
        # Aplicado exclusivamente no M5 para evitar reversão no meio do range.
        # Demais timeframes (M1 etc.) passam direto sem este filtro.
        if tf_min == 5 and not _m5_extreme_filter("call", velas):
            return None
        return {
            "pattern_name": "ReversalV15_CALL",
            "direction_hint": "call",
            "requires_confirmation": True,
            "pattern_from": pattern_from,
            "expected_confirm_from": expected_confirm_from,
            "v15_score": call_score,
            "v15_confirm_count": 0,
            "pattern_mode": "v15",
        }
    if put_score >= V15_SCORE_MIN and put_score > call_score:
        # ── Filtro estrutural M5 (v15.1): só aceita sinal no extremo do range ──
        if tf_min == 5 and not _m5_extreme_filter("put", velas):
            return None
        return {
            "pattern_name": "ReversalV15_PUT",
            "direction_hint": "put",
            "requires_confirmation": True,
            "pattern_from": pattern_from,
            "expected_confirm_from": expected_confirm_from,
            "v15_score": put_score,
            "v15_confirm_count": 0,
            "pattern_mode": "v15",
        }

    # ── Fallback v14: Harami Bearish / Bullish / Hammer ───────────────────
    if is_harami_bearish(c_prev, c_last):
        return {
            "pattern_name": "HaramiBearish",
            "direction_hint": "put",
            "requires_confirmation": True,
            "pattern_from": pattern_from,
            "expected_confirm_from": expected_confirm_from,
            "v15_score": 0,
            "v15_confirm_count": 0,
            "pattern_mode": "fallback",
        }
    if is_harami_bullish(c_prev, c_last):
        return {
            "pattern_name": "HaramiBullish",
            "direction_hint": "call",
            "requires_confirmation": True,
            "pattern_from": pattern_from,
            "expected_confirm_from": expected_confirm_from,
            "v15_score": 0,
            "v15_confirm_count": 0,
            "pattern_mode": "fallback",
        }
    if is_hammer(c_last):
        return {
            "pattern_name": "Hammer",
            "direction_hint": "call",
            "requires_confirmation": True,
            "pattern_from": pattern_from,
            "expected_confirm_from": expected_confirm_from,
            "v15_score": 0,
            "v15_confirm_count": 0,
            "pattern_mode": "fallback",
        }

    return None


def confirm_pending(tf_min: int, pending: Dict[str, Any], velas: List[Dict[str, Any]]) -> Tuple[str, Optional[str]]:
    """
    CONFIRMAÇÃO DE REVERSÃO V15
    ───────────────────────────
    Para sinais V15 (pattern_mode='v15'):
      Requer V15_CONFIRM_POLLS polls consecutivos onde o preço confirma
      a direção prevista (acima/abaixo do fechamento da vela de sinal).
      NOTA: modifica pending['v15_confirm_count'] diretamente a cada poll
      para rastrear o progresso de sustentação (efeito colateral intencional).

    Para sinais fallback (pattern_mode='fallback': harami/hammer):
      Usa a lógica clássica da v14 — confirmação na vela seguinte
      ao padrão (candle close versus referência).

    Retorna:
      ("confirmed", direction) — sinal confirmado, pode entrar
      ("waiting", None)        — aguardando mais dados/polls
      ("rejected", None)       — sinal inválido, descartar
      ("expired", None)        — fora do prazo de validade
      ("error", None)          — dados insuficientes
    """
    period = tf_min * 60
    pattern_from = int(pending["pattern_from"])
    expected_confirm_from = int(pending["expected_confirm_from"])
    expire_from = pattern_from + (PENDING_EXPIRE_CANDLES * period)
    direction_hint = pending.get("direction_hint", "call")
    pattern_mode = pending.get("pattern_mode", "fallback")

    now_server = int(API.get_server_timestamp())
    if now_server >= expire_from + 2:
        return "expired", None

    c_pattern = _find_candle_by_from(velas, pattern_from)
    if c_pattern is None:
        return "error", None

    # ─── Confirmação V15: sustentação por múltiplos polls ─────────────────
    if pattern_mode == "v15":
        # Aguarda abertura da janela de confirmação
        if now_server < expected_confirm_from:
            return "waiting", None

        # Referência: fechamento da vela de sinal
        p_ref = float(c_pattern.get("close", 0))
        if p_ref == 0:
            return "error", None

        # Usa a última vela disponível para comparar preço atual
        c_confirm = velas[-1] if velas else None
        if c_confirm is None:
            return "waiting", None

        c_price = float(c_confirm.get("close", c_confirm.get("open", p_ref)))

        # Verifica confirmação de direção no poll atual
        confirmed_now = (direction_hint == "call" and c_price > p_ref) or \
                        (direction_hint == "put" and c_price < p_ref)

        if confirmed_now:
            # Incrementa contador de polls confirmados consecutivos
            pending["v15_confirm_count"] = pending.get("v15_confirm_count", 0) + 1
            if pending["v15_confirm_count"] >= V15_CONFIRM_POLLS:
                return "confirmed", direction_hint
            return "waiting", None
        else:
            # Poll falhou: reseta contador de sustentação
            pending["v15_confirm_count"] = 0
            # Se passou 1 período inteiro após o esperado sem confirmar, rejeita
            if now_server >= expected_confirm_from + period:
                return "rejected", None
            return "waiting", None

    # ─── Confirmação fallback v14: harami/hammer (lógica clássica) ────────
    c_next = _find_candle_by_from(velas, expected_confirm_from)
    if c_next is None:
        return "waiting", None

    patt = pending["pattern_name"]
    if patt == "Hammer":
        ok = float(c_next["close"]) > float(c_pattern["max"])
        return ("confirmed" if ok else "rejected"), ("call" if ok else None)
    if patt == "HaramiBullish":
        mid = (float(c_pattern["max"]) + float(c_pattern["min"])) / 2.0
        ok = (float(c_next["close"]) > float(c_next["open"])) and (float(c_next["close"]) > mid)
        return ("confirmed" if ok else "rejected"), ("call" if ok else None)
    if patt == "HaramiBearish":
        mid = (float(c_pattern["max"]) + float(c_pattern["min"])) / 2.0
        ok = (float(c_next["close"]) < float(c_next["open"])) and (float(c_next["close"]) < mid)
        return ("confirmed" if ok else "rejected"), ("put" if ok else None)
    return "error", None


# =========================
# Timing / amount
# =========================
def within_entry_window(tf_min: int) -> Tuple[bool, int, int]:
    period = tf_min * 60
    window = ENTRY_WINDOW_SECONDS_M5 if tf_min == 5 else ENTRY_WINDOW_SECONDS_M1
    try:
        now_s = int(API.get_server_timestamp())
    except Exception:
        now_s = int(time.time())
    sec = int(now_s % period)
    return sec <= window, sec, window


def compute_amount(balance: float) -> float:
    if AMOUNT_MODE == "fixed":
        amt = float(AMOUNT_FIXED)
    else:
        amt = (balance * (AMOUNT_PERCENT / 100.0)) if AMOUNT_RECALC_EACH else float(AMOUNT_FIXED)
    if amt < AMOUNT_MIN:
        amt = AMOUNT_MIN
    amt = round(amt, 2)
    if amt > balance:
        amt = round(max(AMOUNT_MIN, balance), 2)
    return amt


# =========================
# Resultado
# =========================
def _try_query_order_methods(order_id):
    methods = [
        'get_order', 'get_order_by_id', 'get_order_history', 'get_positions',
        'get_position', 'get_position_by_id', 'get_positions_history', 'get_order_history_v2'
    ]
    for name in methods:
        try:
            fn = getattr(API, name, None)
            if fn is None:
                continue
            for arg in (order_id, str(order_id), int(order_id) if str(order_id).isdigit() else order_id,):
                try:
                    res = fn(arg) if arg is not None else fn()
                except TypeError:
                    try:
                        res = fn()
                    except Exception:
                        res = None
                except Exception:
                    res = None
                if res:
                    return {'method': name, 'input_arg': arg, 'raw': res}
        except Exception:
            continue
    return None


def _parse_order_query_response(raw):
    def inspect_item(item):
        if isinstance(item, dict):
            keys = {k.lower(): v for k, v in item.items()}
            for k in ('profit', 'payout', 'profit_amount', 'result', 'win', 'amount_win'):
                if k in keys:
                    try:
                        return {'profit': float(keys[k]), 'raw': item}
                    except Exception:
                        pass
            for k in ('status', 'state', 'order_status', 'close_status'):
                if k in keys:
                    val = str(keys[k]).lower()
                    if 'win' in val or 'profit' in val or 'paid' in val:
                        return {'status': 'win', 'raw': item}
                    if 'loss' in val or 'lose' in val or 'lost' in val:
                        return {'status': 'loss', 'raw': item}
                    return {'status': val, 'raw': item}
            for v in item.values():
                parsed = inspect_item(v)
                if parsed:
                    return parsed
        elif isinstance(item, (list, tuple)):
            for it in item:
                parsed = inspect_item(it)
                if parsed:
                    return parsed
        return None

    parsed = inspect_item(raw)
    if not parsed:
        return {'status': 'unknown', 'profit': None, 'raw': raw}

    status = parsed.get('status')
    profit = parsed.get('profit')
    if profit is not None:
        try:
            profit = float(profit)
            if profit > 0:
                status = 'win'
            elif profit < 0:
                status = 'loss'
            else:
                status = 'unknown'
        except Exception:
            pass

    return {'status': status or 'unknown', 'profit': profit,
            'raw': parsed.get('raw') if 'raw' in parsed else raw}


def _early_loss_by_stable_balance(balance_before: float, amount: float,
                                 poll_interval: float = EARLY_LOSS_SAMPLE_INTERVAL_S) -> bool:
    if balance_before is None or amount is None:
        return False
    try:
        balance_before = float(balance_before)
        amount = float(amount)
    except Exception:
        return False
    if amount <= 0:
        return False

    amostras: List[float] = []
    for _ in range(EARLY_LOSS_STABLE_SAMPLES):
        time.sleep(poll_interval)
        bal = get_available_balance()
        if bal is None:
            return False
        amostras.append(float(bal))

    mn = min(amostras)
    mx = max(amostras)
    if (mx - mn) > EARLY_LOSS_EPS:
        return False

    last = amostras[-1]
    if last < balance_before - (amount * 0.30):
        return True

    return False


def check_order_result(order_id, amount, saldo_before=None, timeout_seconds=90, poll_interval=1.5):
    start_check_ts = time.time()
    deadline = start_check_ts + timeout_seconds

    try:
        q = _try_query_order_methods(order_id)
        if q:
            parsed = _parse_order_query_response(q['raw'])
            if parsed.get('status') in ('win', 'loss'):
                bal = get_available_balance()
                return {'result': parsed['status'], 'profit': parsed.get('profit'), 'balance_after': bal,
                        'method': 'order_query', 'raw': q}
    except Exception:
        pass

    if saldo_before is None:
        saldo_before = get_available_balance()
    if saldo_before is None:
        return {'result': 'unknown', 'profit': None, 'balance_after': None, 'method': 'no_balance', 'raw': None}

    expected_post_purchase = saldo_before - (amount or 0.0)
    eps_small = max(0.01, (amount or 0.0) * 0.005)
    profit_threshold = max(0.01, (amount or 0.0) * 0.05)

    purchase_seen = False
    last_bal = saldo_before
    extended_wait_used = False
    early_loss_checked = False

    purchase_detect_deadline = min(time.time() + 8.0, deadline)
    while time.time() <= purchase_detect_deadline:
        time.sleep(0.4)
        bal = get_available_balance()
        if bal is None:
            continue
        if bal <= expected_post_purchase + eps_small:
            purchase_seen = True
            last_bal = bal
            break
        last_bal = bal

    while time.time() <= deadline:
        try:
            q = _try_query_order_methods(order_id)
            if q:
                parsed = _parse_order_query_response(q['raw'])
                if parsed.get('status') in ('win', 'loss'):
                    bal = get_available_balance()
                    return {'result': parsed['status'], 'profit': parsed.get('profit'), 'balance_after': bal,
                            'method': 'order_query', 'raw': q}
        except Exception:
            pass

        time.sleep(poll_interval)
        bal = get_available_balance()
        if bal is None:
            continue

        if purchase_seen and (not early_loss_checked):
            elapsed_check = time.time() - start_check_ts
            if elapsed_check >= EARLY_LOSS_GUARD_SECONDS:
                early_loss_checked = True
                try:
                    if _early_loss_by_stable_balance(saldo_before, amount):
                        bal_now = get_available_balance()
                        profit = (bal_now - saldo_before) if (bal_now is not None) else None
                        return {'result': 'loss', 'profit': profit, 'balance_after': bal_now,
                                'method': 'early_loss_stable_balance', 'raw': None}
                except Exception:
                    pass

        if purchase_seen:
            delta_post = bal - expected_post_purchase
            if delta_post > profit_threshold:
                profit = bal - saldo_before
                return {'result': 'win', 'profit': profit, 'balance_after': bal, 'method': 'balance_poll', 'raw': None}

            if bal < expected_post_purchase - eps_small:
                profit = bal - saldo_before
                return {'result': 'loss', 'profit': profit, 'balance_after': bal, 'method': 'balance_poll', 'raw': None}

            remaining = deadline - time.time()
            if remaining <= 2.0:
                if not extended_wait_used:
                    deadline += EXTRA_WAIT_SECONDS
                    extended_wait_used = True
                    continue
                else:
                    bal_now = get_available_balance() or last_bal
                    profit = bal_now - saldo_before
                    return {'result': 'win' if profit > 0 else ('loss' if profit < 0 else 'unknown'),
                            'profit': profit, 'balance_after': bal_now, 'method': 'timeout_estimated', 'raw': None}
        else:
            delta = bal - saldo_before
            if delta > profit_threshold:
                return {'result': 'win', 'profit': delta, 'balance_after': bal, 'method': 'balance_poll', 'raw': None}
            if delta < - (amount or 0.0) + eps_small:
                purchase_seen = True
                expected_post_purchase = bal
                last_bal = bal
                continue

        last_bal = bal

    profit = last_bal - (saldo_before or 0.0)
    return {'result': 'win' if profit > 0 else ('loss' if profit < 0 else 'unknown'),
            'profit': profit, 'balance_after': last_bal, 'method': 'timeout_estimated', 'raw': None}


# =========================
# Buy
# =========================
def _do_buy_minimal(amount, ativo, direction, expiration):
    t0 = time.perf_counter()
    try:
        status, info = API.buy(amount, ativo, direction, expiration)
    except Exception as e:
        status, info = False, e
    t1 = time.perf_counter()
    delta = t1 - t0

    global BUY_LATENCY_AVG
    BUY_LATENCY_AVG = BUY_LATENCY_ALPHA * delta + (1.0 - BUY_LATENCY_ALPHA) * BUY_LATENCY_AVG
    try:
        if LATENCY_CSV is not None:
            with LATENCY_CSV.open('a', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow([now_iso(), INSTANCE_TAG, f"{delta:.6f}", f"{BUY_LATENCY_AVG:.6f}", "buy"])
    except Exception:
        pass

    return status, info


def _buy_worker(direction, ativo, amount, expiration, result_container, event):
    status, info = _do_buy_minimal(amount, ativo, direction, expiration)
    result_container["res"] = {"success": bool(status), "order_id": info if status else None, "info": info}
    event.set()


# =========================
# Rigidez
# =========================
def _apply_rigidez():
    global ADX_MIN_M1, ADX_MIN_M5, BB_WIDTH_MIN_M1, BB_WIDTH_MIN_M5, SLOPE_MIN_M1, SLOPE_MIN_M5
    global ENTRY_WINDOW_SECONDS_M1, ENTRY_WINDOW_SECONDS_M5
    global ATR_ADAPTIVE_FACTOR
    if RIGIDEZ_MODE != "rigida":
        return
    ADX_MIN_M1 += 2.0
    ADX_MIN_M5 += 2.0
    BB_WIDTH_MIN_M1 *= 1.20
    BB_WIDTH_MIN_M5 *= 1.20
    SLOPE_MIN_M1 *= 1.25
    SLOPE_MIN_M5 *= 1.25
    ENTRY_WINDOW_SECONDS_M1 = min(ENTRY_WINDOW_SECONDS_M1, 6)
    ENTRY_WINDOW_SECONDS_M5 = min(ENTRY_WINDOW_SECONDS_M5, 18)
    ATR_ADAPTIVE_FACTOR = max(ATR_ADAPTIVE_FACTOR, 0.85)


# =========================
# Menus
# =========================
def ask_yes_no(prompt):
    while True:
        r = input(prompt + " (s/n): ").strip().lower()
        if r in ('s', 'n'):
            return r == 's'


# =========================
# FAVORITOS / LISTA DE ATIVOS
# =========================
def load_favorites() -> List[str]:
    """Lê favoritos.txt (um ativo por linha). Linhas com # são comentários."""
    favs: List[str] = []
    try:
        if FAVORITES_FILE.exists():
            with FAVORITES_FILE.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    favs.append(_normalize_asset_name(line))
    except Exception:
        pass
    return favs


def build_asset_list(use_otc: bool, max_count: int) -> List[Tuple[str, str]]:
    """Monta lista de (ativo, categoria) priorizando favoritos.txt.

    1. Lê favoritos.txt — usa os que estão abertos e correspondem ao tipo.
    2. Completa com outros ativos abertos do tipo até max_count.
    """
    try:
        ot = API.get_all_open_time()
    except Exception:
        return []

    # Todos os ativos abertos do tipo escolhido (sem duplicatas de nome)
    open_assets: List[Tuple[str, str]] = []
    seen: set = set()
    for categoria in _categories_priority(tipo):
        table = ot.get(categoria, {})
        if not isinstance(table, dict):
            continue
        for name, info in table.items():
            if not (isinstance(info, dict) and info.get('open')):
                continue
            name_u = str(name).upper()
            if name_u in seen:
                continue
            if use_otc:
                if '-OTC' in name_u:
                    open_assets.append((name, categoria))
                    seen.add(name_u)
            else:
                if '-OP' in name_u:
                    open_assets.append((name, categoria))
                    seen.add(name_u)

    # Mapa nome normalizado -> (name, categoria) para lookup rápido
    open_map: Dict[str, Tuple[str, str]] = {
        _normalize_asset_name(n): (n, c) for n, c in open_assets
    }

    favs = load_favorites()
    result: List[Tuple[str, str]] = []
    used: set = set()

    # 1ª passagem: favoritos abertos (na ordem do arquivo)
    for fav in favs:
        if len(result) >= max_count:
            break
        entry = open_map.get(fav)
        if entry and entry[0].upper() not in used:
            result.append(entry)
            used.add(entry[0].upper())

    # 2ª passagem: completa com outros abertos do tipo
    for name, cat in open_assets:
        if len(result) >= max_count:
            break
        if name.upper() not in used:
            result.append((name, cat))
            used.add(name.upper())

    return result


def ask_market_type() -> bool:
    """Pergunta tipo de mercado. Retorna True para OTC, False para Mercado Aberto (-OP)."""
    print("\n" + "=" * 70)
    print("🌍 TIPO DE MERCADO")
    print("=" * 70)
    print("  1) Mercado Aberto  (ativos -OP)")
    print("  2) OTC")
    while True:
        r = input("\n👉 Digite 1 ou 2 [1]: ").strip() or "1"
        if r == "1":
            return False
        if r == "2":
            return True
        print("❌ Opção inválida!")


def ask_num_assets() -> int:
    """Pergunta quantos ativos operar simultaneamente (1 a 8)."""
    print("\n" + "=" * 70)
    print("📊 NÚMERO DE ATIVOS SIMULTÂNEOS")
    print("=" * 70)
    print("  Escolha quantos ativos operar ao mesmo tempo (1 a 8).")
    while True:
        r = input("\n👉 Digite um número de 1 a 8 [4]: ").strip() or "4"
        try:
            n = int(r)
            if 1 <= n <= 8:
                return n
        except Exception:
            pass
        print("❌ Digite um número entre 1 e 8.")


def ask_time_hhmm(prompt):
    while True:
        raw = input(prompt + " (HH:MM): ").strip()
        try:
            hh, mm = map(int, raw.split(':'))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except Exception:
            pass


def round_up_to_next_period(ts_seconds: int, period_minutes: int) -> int:
    period = period_minutes * 60
    remainder = ts_seconds % period
    if remainder == 0:
        return ts_seconds
    return ts_seconds + (period - remainder)


def ask_timeframe():
    print("\n" + "=" * 70)
    print("⏱️  TIMEFRAME")
    print("=" * 70)
    print("  1) M1")
    print("  2) M5")
    while True:
        r = input("\n👉 Digite 1 ou 2 [1]: ").strip() or "1"
        if r == "1":
            return 1
        if r == "2":
            return 5


def ask_entry_mode():
    print("\n" + "=" * 70)
    print("🧠 MODO DE ENTRADA")
    print("=" * 70)
    print("  1) REVERSÃO  ✅ (único modo disponível nesta versão)")
    print("  2) ROMPIMENTO  🚧 (em desenvolvimento — indisponível)")
    while True:
        r = input("\n👉 Pressione Enter para continuar com REVERSÃO: ").strip() or "1"
        if r == "1":
            return "reversal"
        if r == "2":
            print("⚠️  O modo ROMPIMENTO ainda não está disponível. Selecione 1 para REVERSÃO.")
            continue


def ask_rigidez():
    print("\n" + "=" * 70)
    print("🧱 RIGIDEZ DAS ENTRADAS")
    print("=" * 70)
    print("  1) Normal")
    print("  2) Rígida")
    while True:
        r = input("\n👉 Digite 1 ou 2 [1]: ").strip() or "1"
        if r == "1":
            return "normal"
        if r == "2":
            return "rigida"


def ask_amount_menu():
    global AMOUNT_MODE, AMOUNT_FIXED, AMOUNT_PERCENT, AMOUNT_RECALC_EACH
    print("\n" + "=" * 70)
    print("💵 VALOR POR OPERAÇÃO")
    print("=" * 70)
    print("  1) Valor FIXO")
    print("  2) Percentual do SALDO")
    while True:
        modo = input("\n👉 Escolha 1 ou 2 [1]: ").strip() or "1"
        if modo not in ("1", "2"):
            print("❌ Opção inválida!")
            continue

        if modo == "1":
            AMOUNT_MODE = "fixed"
            while True:
                raw = input('\n💵 Valor fixo por operação: $').strip().replace(',', '.')
                try:
                    v = float(raw)
                    if v > 0:
                        AMOUNT_FIXED = v
                        break
                except Exception:
                    pass
                print("❌ Digite um número válido > 0")
            break

        AMOUNT_MODE = "percent"
        while True:
            raw = input('\n📊 Percentual do saldo (ex: 1 para 1%): ').strip().replace(',', '.')
            try:
                p = float(raw)
                if p > 0:
                    AMOUNT_PERCENT = p
                    break
            except Exception:
                pass
            print("❌ Digite um número válido > 0")

        AMOUNT_RECALC_EACH = ask_yes_no("👉 Recalcular percentual a cada operação?")
        if not AMOUNT_RECALC_EACH:
            bal_now = get_available_balance() or 0.0
            AMOUNT_FIXED = round(max(AMOUNT_MIN, bal_now * (AMOUNT_PERCENT / 100.0)), 2)
            print(f"✅ {AMOUNT_PERCENT}% do saldo atual = ${AMOUNT_FIXED:.2f} (fixado)")
        break


def ask_stop_loss_win():
    global STOP_LOSS_PCT, STOP_WIN_PCT
    print("\n" + "=" * 70)
    print("🛑 STOP LOSS")
    print("=" * 70)
    while True:
        raw = input('\n👉 Stop Loss em % (0 desativa) [0]: ').strip().replace(',', '.')
        if raw == '':
            STOP_LOSS_PCT = 0.0
            break
        try:
            STOP_LOSS_PCT = float(raw)
            if STOP_LOSS_PCT >= 0:
                break
        except Exception:
            pass
        print("❌ Valor inválido")

    print("\n" + "=" * 70)
    print("🎯 STOP WIN")
    print("=" * 70)
    while True:
        raw = input('\n👉 Stop Win em % (0 desativa) [0]: ').strip().replace(',', '.')
        if raw == '':
            STOP_WIN_PCT = 0.0
            break
        try:
            STOP_WIN_PCT = float(raw)
            if STOP_WIN_PCT >= 0:
                break
        except Exception:
            pass
        print("❌ Valor inválido")


# =========================
# Estado
# =========================
def load_state():
    try:
        _mkdirp(STATE_DIR)
        if STATE_PATH.exists():
            with STATE_PATH.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(d):
    try:
        _mkdirp(STATE_DIR)
        with STATE_PATH.open('w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def choose_asset_interactive(start_ts: Optional[int]):
    """
    - Se agendado: aceita ativo fechado (se existir na lista da IQ, case-insensitive)
    - No horário: resolve automaticamente a variante aberta (-op etc)
    - OTC: só se você digitar -OTC
    """
    global PRESET_PATH

    scheduled = start_ts is not None
    state = load_state()

    while True:
        raw = input('\n👉 Digite o ativo (ex: EURJPY, EURJPY-OP ou EURJPY-OTC): ').strip()
        if not raw:
            print("❌ Nome inválido.")
            continue

        parsed = _parse_user_asset_input(raw)
        base = parsed["base"]
        suffix = parsed["suffix"]
        allow_otc = parsed["allow_otc"]

        if suffix == "OTC":
            alt_name, alt_ch = find_preferred_variant_with_rules(base, allow_otc=True)
            if alt_name:
                if "-OTC" not in str(alt_name).upper():
                    print(f"❌ Você digitou OTC, mas a alternativa encontrada não é OTC: {alt_name}. Escolha outro ativo.")
                    continue
                if (not ativo_aberto(alt_name, chave_preferida=alt_ch)) and (not scheduled):
                    print(f"❌ Ativo OTC está fechado agora: {alt_name}. (Sem agendamento não pode). Escolha outro.")
                    continue

                print(f"✅ Usando: {alt_name} ({alt_ch})")
                state.update({'last_asset': alt_name, 'last_asset_category': alt_ch, 'tipo': tipo})
                save_state(state)

                auto_tag = _auto_tag_from_choices(alt_name, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
                _init_paths_with_tag(auto_tag)
                _ensure_csv_headers()

                filename = _preset_filename(alt_name, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
                PRESET_PATH = PRESETS_DIR / filename
                preset = build_preset_dict(ativo=alt_name, ativo_chave=alt_ch, runtime_min=None)
                write_preset_file(PRESET_PATH, preset)

                return alt_name, alt_ch, {"base": base, "allow_otc": True}

            print(f"❌ Nenhuma variante OTC encontrada/aberta para '{base}'. Escolha outro ativo.")
            continue

        alt_name, alt_ch = find_preferred_variant_with_rules(base, allow_otc=False)
        if alt_name:
            print(f"✅ Usando: {alt_name} ({alt_ch})")
            state.update({'last_asset': alt_name, 'last_asset_category': alt_ch, 'tipo': tipo})
            save_state(state)

            auto_tag = _auto_tag_from_choices(alt_name, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
            _init_paths_with_tag(auto_tag)
            _ensure_csv_headers()

            filename = _preset_filename(alt_name, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
            PRESET_PATH = PRESETS_DIR / filename
            preset = build_preset_dict(ativo=alt_name, ativo_chave=alt_ch, runtime_min=None)
            write_preset_file(PRESET_PATH, preset)

            return alt_name, alt_ch, {"base": base, "allow_otc": False}

        if scheduled and ALLOW_CLOSED_ASSET_IF_SCHEDULED:
            if not is_asset_known_anywhere_case_insensitive(base):
                print(f"❌ Ativo '{base}' não existe na tabela do get_all_open_time(). Escolha outro.")
                continue

            preferred_cat = _categories_priority(tipo)[0]
            print(f"✅ Ativo aceito para agendamento (mesmo fechado agora): {base} ({preferred_cat})")

            state.update({'last_asset': base, 'last_asset_category': preferred_cat, 'tipo': tipo})
            save_state(state)

            auto_tag = _auto_tag_from_choices(base, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
            _init_paths_with_tag(auto_tag)
            _ensure_csv_headers()

            filename = _preset_filename(base, TIMEFRAME_MINUTES, ENTRY_MODE, RIGIDEZ_MODE)
            PRESET_PATH = PRESETS_DIR / filename
            preset = build_preset_dict(ativo=base, ativo_chave=preferred_cat, runtime_min=None)
            write_preset_file(PRESET_PATH, preset)

            return base, preferred_cat, {"base": base, "allow_otc": False}

        print(f"❌ Nenhuma variante aberta encontrada para '{base}'. Tente outro ativo (ou agende o início).")


# =========================
# Guards: aguardar e resolver variante
# =========================
def _fmt_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def resolve_open_asset_variant(base: str, allow_otc: bool) -> Tuple[Optional[str], Optional[str]]:
    return find_preferred_variant_with_rules(base, allow_otc=allow_otc)


def wait_until_asset_open_or_timeout(base: str, allow_otc: bool, timeout_seconds: int) -> Tuple[bool, Optional[str], Optional[str]]:
    t0 = time.time()
    last_print = 0.0
    while True:
        name, cat = resolve_open_asset_variant(base, allow_otc=allow_otc)
        if name and cat and ativo_aberto(name, chave_preferida=cat):
            return True, name, cat

        elapsed = time.time() - t0
        remaining = int(timeout_seconds - elapsed)
        if remaining <= 0:
            return False, None, None

        nowt = time.time()
        if nowt - last_print >= WAIT_ASSET_OPEN_PRINT_EVERY_SECONDS:
            console_event(
                f"⏳ Ativo ainda fechado: {display_asset_name(base)} | "
                f"aguardando abrir... (timeout em {_fmt_hms(remaining)})"
            )
            last_print = nowt

        time.sleep(WAIT_ASSET_OPEN_CHECK_EVERY_SECONDS)


# =========================
# LOOP PRINCIPAL
# =========================
def loop_patterns(ativo: str, ativo_chave: str, tf_min: int, runtime_seconds: Optional[int],
                  start_timestamp: Optional[int], schedule_base: str, schedule_allow_otc: bool):

    global pending, pending_id_active, pending_lock_until_ts, _last_pending_status_printed_for_id

    period = tf_min * 60
    expiration = 1 if tf_min == 1 else 5

    # Aguardar horário agendado
    if start_timestamp is not None and start_timestamp > time.time():
        console_event(f"⏳ Aguardando início agendado: {datetime.fromtimestamp(start_timestamp).strftime('%d/%m/%Y %H:%M:%S')}")
        while time.time() < start_timestamp:
            time.sleep(0.5)

    # Se agendado: resolver variante aberta real (-op etc) e esperar até abrir
    if start_timestamp is not None and WAIT_ASSET_OPEN_IF_SCHEDULED:
        console_event(f"🔎 Resolvendo ativo quando abrir: base={display_asset_name(schedule_base)} (OTC={'sim' if schedule_allow_otc else 'não'})")
        ok, real_asset, real_cat = wait_until_asset_open_or_timeout(
            schedule_base, schedule_allow_otc, WAIT_ASSET_OPEN_TIMEOUT_SECONDS
        )
        if not ok:
            console_event("❌ Ativo não abriu em 30 minutos após o início agendado. Encerrando bot.")
            return

        ativo = real_asset
        ativo_chave = real_cat
        console_event(f"✅ Ativo aberto detectado: {display_asset_name(ativo)} ({ativo_chave}). Iniciando operações...")

    initial_bal = get_available_balance()
    stop_loss_threshold = None
    stop_win_threshold = None
    if initial_bal:
        if STOP_LOSS_PCT > 0:
            stop_loss_threshold = initial_bal * (1.0 - STOP_LOSS_PCT / 100.0)
        if STOP_WIN_PCT > 0:
            stop_win_threshold = initial_bal * (1.0 + STOP_WIN_PCT / 100.0)

    start_exec = time.time()
    stop_ts = None if runtime_seconds is None else start_exec + runtime_seconds

    console_event(
        f"🚀 Loop iniciado | TF=M{tf_min} | Expiração={expiration}min | "
        f"Rigidez={RIGIDEZ_MODE.upper()} | mode={ENTRY_MODE.upper()} | tag={INSTANCE_TAG}"
    )

    last_remaining_minute_printed: Optional[int] = None
    if stop_ts is not None:
        remaining = int(stop_ts - time.time())
        console_event(f"⏳ Tempo restante: {_fmt_hms(remaining)}")
        last_remaining_minute_printed = remaining // 60

    last_idle_candle_id = None

    while True:
        if stop_ts is not None:
            remaining = int(stop_ts - time.time())
            if remaining <= 0:
                console_event("⏱️ Tempo de execução atingido. Encerrando...")
                break
            cur_min = remaining // 60
            if last_remaining_minute_printed is None or cur_min != last_remaining_minute_printed:
                console_event(f"⏳ Tempo restante: {_fmt_hms(remaining)}")
                last_remaining_minute_printed = cur_min

        bal = get_available_balance()
        if stop_loss_threshold and bal is not None and bal <= stop_loss_threshold:
            console_event("🛑 STOP LOSS atingido. Encerrando...")
            break
        if stop_win_threshold and bal is not None and bal >= stop_win_threshold:
            console_event("🎯 STOP WIN atingido. Encerrando...")
            break

        now_server = int(API.get_server_timestamp())
        candle_id = now_server // period

        if pending is None and candle_id != last_idle_candle_id:
            last_idle_candle_id = candle_id
            console_event(f"⏳ Aguardando... (Ativo: {display_asset_name(ativo)} | TF: M{tf_min})")

        if not ativo_aberto(ativo, chave_preferida=ativo_chave):
            _log_blocked("asset_closed", f"tf={tf_min}")
            time.sleep(IDLE_SLEEP_S_M5 if tf_min == 5 else IDLE_SLEEP_S_M1)
            continue

        velas = get_candles_safe(ativo, period, CANDLES_LOOKBACK)
        if not velas or len(velas) < MIN_CANDLES_REQUIRED:
            time.sleep(IDLE_SLEEP_S_M5 if tf_min == 5 else IDLE_SLEEP_S_M1)
            continue

        if not passes_atr_filter(tf_min, velas) or not passes_trend_strength_filter(tf_min, velas):
            time.sleep(IDLE_SLEEP_S_M5 if tf_min == 5 else IDLE_SLEEP_S_M1)
            continue

        if pending is not None and pending_id_active is not None:
            if _last_pending_status_printed_for_id != pending_id_active:
                _last_pending_status_printed_for_id = pending_id_active
                console_event(f"🕯️ {pending['pattern_name']} pendente (aguardando confirmação)")

            status, direction = confirm_pending(tf_min, pending, velas)

            if status == "waiting":
                time.sleep(PENDING_SLEEP_S_M5 if tf_min == 5 else PENDING_SLEEP_S_M1)
                continue

            if status in ("expired", "rejected", "error"):
                pending = None
                pending_id_active = None
                pending_lock_until_ts = now_server + (period * 1)
                time.sleep(0.5)
                continue

            if status == "confirmed" and direction in ("call", "put"):
                patt = pending["pattern_name"]

                ok_win, sec, win = within_entry_window(tf_min)
                if not ok_win:
                    _log_blocked("missed_early_entry", f"tf={tf_min}")
                    pending = None
                    pending_id_active = None
                    pending_lock_until_ts = now_server + (period * 1)
                    continue

                if BUY_LATENCY_AVG + BUY_LATENCY_MARGIN >= (win - sec):
                    _log_blocked("latency_guard", f"tf={tf_min}")
                    pending = None
                    pending_id_active = None
                    pending_lock_until_ts = now_server + (period * 1)
                    continue

                if not can_purchase_now(ativo, period_minutes=tf_min, chave_preferida=ativo_chave):
                    _log_blocked("purchase_buffer", f"tf={tf_min}")
                    pending = None
                    pending_id_active = None
                    pending_lock_until_ts = now_server + (period * 1)
                    continue

                saldo_before = get_available_balance() or 0.0
                amount_to_use = compute_amount(saldo_before)
                secs_left = seconds_left_in_period(tf_min)

                console_event(
                    f"🕯️ [{server_hhmmss()}] Sinal confirmado: {patt} | "
                    f"Entrada: {direction.upper()} | ${amount_to_use:.2f} | secs_left={secs_left}"
                )

                result_container = {}
                ev = threading.Event()
                if USE_BUY_THREAD:
                    t = threading.Thread(
                        target=_buy_worker,
                        args=(direction, ativo, amount_to_use, expiration, result_container, ev),
                        daemon=True
                    )
                    t.start()
                    ev.wait(timeout=25.0)
                else:
                    status_b, info_b = _do_buy_minimal(amount_to_use, ativo, direction, expiration)
                    result_container["res"] = {"success": bool(status_b), "order_id": info_b if status_b else None, "info": info_b}

                res = result_container.get("res", {})
                if not res.get("success"):
                    console_event(f"❌ [{server_hhmmss()}] Falha ao enviar ordem.")
                    pending = None
                    pending_id_active = None
                    pending_lock_until_ts = now_server + (period * 1)
                    continue

                order_id = res.get("order_id")
                console_event(f"✅ [{server_hhmmss()}] Ordem aceita | ID: {order_id}")
                console_event(f"⏳ [{server_hhmmss()}] Aguardando resultado...")

                t0 = time.time()
                min_wait = expiration * 60 + RESULT_DELAY_AFTER_EXPIRY_SECONDS
                while time.time() - t0 < min_wait:
                    time.sleep(0.5)

                timeout = M5_RESULT_TIMEOUT if expiration == 5 else M1_RESULT_TIMEOUT
                timeout = max(35, timeout)

                result = check_order_result(
                    order_id, amount_to_use,
                    saldo_before=saldo_before,
                    timeout_seconds=timeout,
                    poll_interval=2.0 if expiration == 5 else 1.5
                )

                label = result.get("result", "unknown")
                profit = result.get("profit")
                bal_after = result.get("balance_after")
                method = result.get("method")

                if label == "win":
                    console_event(f"✅ [{server_hhmmss()}] {fmt_result_line(label, profit, method)}")
                elif label == "loss":
                    console_event(f"❌ [{server_hhmmss()}] {fmt_result_line(label, profit, method)}")
                else:
                    console_event(f"❓ [{server_hhmmss()}] {fmt_result_line(label, profit, method)}")

                try:
                    if TRADES_CSV is not None:
                        with TRADES_CSV.open('a', newline='', encoding='utf-8') as f:
                            csv.writer(f).writerow([
                                now_iso(), INSTANCE_TAG,
                                ativo, tf_min, ENTRY_MODE, RIGIDEZ_MODE,
                                direction, order_id,
                                method, label, float(profit) if profit is not None else "",
                                saldo_before, bal_after if bal_after is not None else "",
                                amount_to_use, f"{BUY_LATENCY_AVG:.6f}",
                                patt, pending.get("pattern_from"),
                                secs_left
                            ])
                except Exception:
                    pass

                pending = None
                pending_id_active = None
                pending_lock_until_ts = now_server + (period * 1)
                time.sleep(0.8)
                continue

        if now_server < pending_lock_until_ts:
            time.sleep(IDLE_SLEEP_S_M5 if tf_min == 5 else IDLE_SLEEP_S_M1)
            continue

        sig = check_patterns(tf_min, velas)
        if not sig:
            time.sleep(IDLE_SLEEP_S_M5 if tf_min == 5 else IDLE_SLEEP_S_M1)
            continue

        patt = sig["pattern_name"]
        patt_from = int(sig["pattern_from"])
        pend_id = (patt, patt_from, ativo, tf_min)

        lastp = last_pending_print_ts_by_id.get(pend_id, 0.0)
        nowt = time.time()
        if nowt - lastp >= PENDING_PRINT_THROTTLE_S:
            console_event(f"🕯️ Sinal detectado: {patt}. Aguardando confirmação...")
            last_pending_print_ts_by_id[pend_id] = nowt

        pending = sig
        pending_id_active = pend_id
        pending_lock_until_ts = int(sig["expected_confirm_from"]) + (period * 1)
        time.sleep(PENDING_SLEEP_S_M5 if tf_min == 5 else PENDING_SLEEP_S_M1)

    print("✅ Loop finalizado.")


# =========================
# LOOP MULTI-ATIVO (M5)
# =========================
def loop_patterns_multi(ativos: List[Tuple[str, str]], tf_min: int):
    """Orquestra múltiplos ativos em único ciclo (M5). Stops globais pelo saldo."""

    period = tf_min * 60
    expiration = tf_min  # M5 -> expiração 5 min

    # Estado por ativo
    per_asset_pending: Dict[str, Optional[Dict[str, Any]]] = {a: None for a, _ in ativos}
    per_asset_pending_id: Dict[str, Any] = {a: None for a, _ in ativos}
    per_asset_lock_until: Dict[str, int] = {a: 0 for a, _ in ativos}
    per_asset_last_idle_cid: Dict[str, Any] = {a: None for a, _ in ativos}
    per_asset_last_pend_status_id: Dict[str, Any] = {a: None for a, _ in ativos}
    asset_last_pending_print: Dict[Any, float] = {}

    initial_bal = get_available_balance()
    stop_loss_threshold = None
    stop_win_threshold = None
    if initial_bal:
        if STOP_LOSS_PCT > 0:
            stop_loss_threshold = initial_bal * (1.0 - STOP_LOSS_PCT / 100.0)
        if STOP_WIN_PCT > 0:
            stop_win_threshold = initial_bal * (1.0 + STOP_WIN_PCT / 100.0)

    console_event(
        f"🚀 Loop M{tf_min} multi-ativo | {len(ativos)} ativo(s) | "
        f"Modo: REVERSÃO | tag={INSTANCE_TAG}"
    )
    for a, ak in ativos:
        console_event(f"  📊 {display_asset_name(a)} ({ak})")

    while True:
        # Verificação global de stop
        bal = get_available_balance()
        if stop_loss_threshold and bal is not None and bal <= stop_loss_threshold:
            console_event("🛑 STOP LOSS global atingido. Encerrando ciclo...")
            break
        if stop_win_threshold and bal is not None and bal >= stop_win_threshold:
            console_event("🎯 STOP WIN global atingido. Encerrando ciclo...")
            break

        now_server = int(API.get_server_timestamp())
        candle_id = now_server // period

        for ativo, ativo_chave in ativos:
            pend = per_asset_pending[ativo]
            pend_id = per_asset_pending_id[ativo]
            lock_until = per_asset_lock_until[ativo]

            if pend is None and candle_id != per_asset_last_idle_cid[ativo]:
                per_asset_last_idle_cid[ativo] = candle_id
                console_event(f"⏳ Aguardando... (Ativo: {display_asset_name(ativo)} | TF: M{tf_min})")

            if not ativo_aberto(ativo, chave_preferida=ativo_chave):
                _log_blocked("asset_closed", f"ativo={ativo} tf={tf_min}")
                continue

            velas = get_candles_safe(ativo, period, CANDLES_LOOKBACK)
            if not velas or len(velas) < MIN_CANDLES_REQUIRED:
                continue

            if not passes_atr_filter(tf_min, velas) or not passes_trend_strength_filter(tf_min, velas):
                continue

            if pend is not None and pend_id is not None:
                if per_asset_last_pend_status_id[ativo] != pend_id:
                    per_asset_last_pend_status_id[ativo] = pend_id
                    console_event(
                        f"🕯️ [{display_asset_name(ativo)}] {pend['pattern_name']} pendente "
                        f"(aguardando confirmação)"
                    )

                status, direction = confirm_pending(tf_min, pend, velas)

                if status == "waiting":
                    continue

                if status in ("expired", "rejected", "error"):
                    per_asset_pending[ativo] = None
                    per_asset_pending_id[ativo] = None
                    per_asset_lock_until[ativo] = now_server + period
                    continue

                if status == "confirmed" and direction in ("call", "put"):
                    patt = pend["pattern_name"]

                    ok_win, sec, win = within_entry_window(tf_min)
                    if not ok_win:
                        _log_blocked("missed_early_entry", f"ativo={ativo} tf={tf_min}")
                        per_asset_pending[ativo] = None
                        per_asset_pending_id[ativo] = None
                        per_asset_lock_until[ativo] = now_server + period
                        continue

                    if BUY_LATENCY_AVG + BUY_LATENCY_MARGIN >= (win - sec):
                        _log_blocked("latency_guard", f"ativo={ativo} tf={tf_min}")
                        per_asset_pending[ativo] = None
                        per_asset_pending_id[ativo] = None
                        per_asset_lock_until[ativo] = now_server + period
                        continue

                    if not can_purchase_now(ativo, period_minutes=tf_min, chave_preferida=ativo_chave):
                        _log_blocked("purchase_buffer", f"ativo={ativo} tf={tf_min}")
                        per_asset_pending[ativo] = None
                        per_asset_pending_id[ativo] = None
                        per_asset_lock_until[ativo] = now_server + period
                        continue

                    saldo_before = get_available_balance() or 0.0
                    amount_to_use = compute_amount(saldo_before)
                    secs_left = seconds_left_in_period(tf_min)

                    console_event(
                        f"🕯️ [{server_hhmmss()}] [{display_asset_name(ativo)}] Sinal confirmado: {patt} | "
                        f"Entrada: {direction.upper()} | ${amount_to_use:.2f} | secs_left={secs_left}"
                    )

                    result_container: Dict[str, Any] = {}
                    ev = threading.Event()
                    if USE_BUY_THREAD:
                        t = threading.Thread(
                            target=_buy_worker,
                            args=(direction, ativo, amount_to_use, expiration, result_container, ev),
                            daemon=True
                        )
                        t.start()
                        ev.wait(timeout=25.0)
                    else:
                        status_b, info_b = _do_buy_minimal(amount_to_use, ativo, direction, expiration)
                        result_container["res"] = {
                            "success": bool(status_b),
                            "order_id": info_b if status_b else None,
                            "info": info_b,
                        }

                    res = result_container.get("res", {})
                    if not res.get("success"):
                        console_event(
                            f"❌ [{server_hhmmss()}] [{display_asset_name(ativo)}] Falha ao enviar ordem."
                        )
                        per_asset_pending[ativo] = None
                        per_asset_pending_id[ativo] = None
                        per_asset_lock_until[ativo] = now_server + period
                        continue

                    order_id = res.get("order_id")
                    console_event(
                        f"✅ [{server_hhmmss()}] [{display_asset_name(ativo)}] Ordem aceita | ID: {order_id}"
                    )
                    console_event(
                        f"⏳ [{server_hhmmss()}] [{display_asset_name(ativo)}] Aguardando resultado..."
                    )

                    t0 = time.time()
                    min_wait = expiration * 60 + RESULT_DELAY_AFTER_EXPIRY_SECONDS
                    while time.time() - t0 < min_wait:
                        time.sleep(0.5)

                    timeout = M5_RESULT_TIMEOUT if expiration == 5 else M1_RESULT_TIMEOUT
                    timeout = max(35, timeout)

                    result = check_order_result(
                        order_id, amount_to_use,
                        saldo_before=saldo_before,
                        timeout_seconds=timeout,
                        poll_interval=2.0,
                    )

                    label = result.get("result", "unknown")
                    profit = result.get("profit")
                    bal_after = result.get("balance_after")
                    method = result.get("method")

                    if label == "win":
                        console_event(
                            f"✅ [{server_hhmmss()}] [{display_asset_name(ativo)}] "
                            f"{fmt_result_line(label, profit, method)}"
                        )
                    elif label == "loss":
                        console_event(
                            f"❌ [{server_hhmmss()}] [{display_asset_name(ativo)}] "
                            f"{fmt_result_line(label, profit, method)}"
                        )
                    else:
                        console_event(
                            f"❓ [{server_hhmmss()}] [{display_asset_name(ativo)}] "
                            f"{fmt_result_line(label, profit, method)}"
                        )

                    try:
                        if TRADES_CSV is not None:
                            with TRADES_CSV.open('a', newline='', encoding='utf-8') as f:
                                csv.writer(f).writerow([
                                    now_iso(), INSTANCE_TAG,
                                    ativo, tf_min, ENTRY_MODE, RIGIDEZ_MODE,
                                    direction, order_id,
                                    method, label, float(profit) if profit is not None else "",
                                    saldo_before, bal_after if bal_after is not None else "",
                                    amount_to_use, f"{BUY_LATENCY_AVG:.6f}",
                                    patt, pend.get("pattern_from"),
                                    secs_left,
                                ])
                    except Exception:
                        pass

                    per_asset_pending[ativo] = None
                    per_asset_pending_id[ativo] = None
                    per_asset_lock_until[ativo] = now_server + period
                    continue

            if now_server < lock_until:
                continue

            sig = check_patterns(tf_min, velas)
            if not sig:
                continue

            patt = sig["pattern_name"]
            patt_from = int(sig["pattern_from"])
            new_pend_id = (patt, patt_from, ativo, tf_min)

            last_p = asset_last_pending_print.get(new_pend_id, 0.0)
            nowt = time.time()
            if nowt - last_p >= PENDING_PRINT_THROTTLE_S:
                console_event(
                    f"🕯️ [{display_asset_name(ativo)}] Sinal detectado: {patt}. "
                    f"Aguardando confirmação..."
                )
                asset_last_pending_print[new_pend_id] = nowt

            per_asset_pending[ativo] = sig
            per_asset_pending_id[ativo] = new_pend_id
            per_asset_lock_until[ativo] = int(sig["expected_confirm_from"]) + period

        time.sleep(IDLE_SLEEP_S_M5)

    print("✅ Loop multi-ativo finalizado.")


# =========================
# MAIN
# =========================
if __name__ == '__main__':
    _mkdirp(LOG_DIR)
    _mkdirp(STATE_DIR)
    _mkdirp(PRESETS_DIR)

    connect()

    print("\n" + "=" * 70)
    print(f"🤖 BOT DINVELAS M5  |  v{BOTDIN_VERSION}")
    print("=" * 70)

    # Conta
    print("\n" + "=" * 70)
    print("💼 CONTA")
    print("=" * 70)
    while True:
        escolha = input('\n👉 Selecione o tipo de conta (demo ou real): ').strip().lower()
        if escolha in ('demo', 'real'):
            break
        print('❌ Opção inválida! Digite "demo" ou "real"')
    conta = 'PRACTICE' if escolha == 'demo' else 'REAL'
    API.change_balance(conta)
    print(f"✅ Conta selecionada: {'DEMO' if conta == 'PRACTICE' else 'REAL'}")

    # Tipo de mercado
    use_otc = ask_market_type()
    market_label = "OTC" if use_otc else "Mercado Aberto"

    # Número de ativos simultâneos
    max_ativos = ask_num_assets()

    # Stops
    ask_stop_loss_win()

    # Valor por operação
    ask_amount_menu()

    # Configurações fixas para esta versão
    TIMEFRAME_MINUTES = 5
    ENTRY_MODE = "reversal"
    RIGIDEZ_MODE = "normal"
    _apply_rigidez()

    # Montar lista de ativos
    print(f"\n🔍 Buscando ativos {market_label} disponíveis...")
    ativos_lista = build_asset_list(use_otc=use_otc, max_count=max_ativos)
    if not ativos_lista:
        print(
            f"❌ Nenhum ativo {market_label} aberto encontrado. "
            "Verifique o horário de mercado ou o arquivo favoritos.txt."
        )
        sys.exit(1)

    # Inicializar paths de log com tag automática
    market_tag = "otc" if use_otc else "op"
    auto_tag = f"m5_{market_tag}_{len(ativos_lista)}ativos"
    _init_paths_with_tag(auto_tag)
    _ensure_csv_headers()

    nome = get_profile_name()

    print('\n' + '=' * 70)
    print('📋 RESUMO')
    print('=' * 70)
    if nome:
        print(f'👤 Usuário: {nome}')
    print(f'InstanceTag: {INSTANCE_TAG}')
    print(f'Conta: {"DEMO" if conta == "PRACTICE" else "REAL"} | Mercado: {market_label}')
    print(f'Timeframe: M5 | Modo: REVERSÃO | Ativos: {len(ativos_lista)}/{max_ativos}')
    print('Ativos selecionados:')
    for a, ak in ativos_lista:
        print(f'  - {display_asset_name(a)} ({ak})')
    if AMOUNT_MODE == "fixed":
        print(f'Valor por operação: ${AMOUNT_FIXED:.2f} (fixo)')
    else:
        print(f'Valor por operação: {AMOUNT_PERCENT:.2f}% do saldo')
    print(f'StopLoss: {STOP_LOSS_PCT:.2f}% | StopWin: {STOP_WIN_PCT:.2f}%')
    print(f'Logs: {LOG_DIR.as_posix()}/ | State: {STATE_DIR.as_posix()}/')
    print('=' * 70)
    print('\n🚀 Iniciando...\n')

    try:
        loop_patterns_multi(ativos_lista, tf_min=TIMEFRAME_MINUTES)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
    except Exception as e:
        _log_error("Erro inesperado no loop principal.", e)
        print("\n❌ Ocorreu um erro. Veja logs/runtime_errors_*.log")
    finally:
        print("✅ Bot finalizado.")