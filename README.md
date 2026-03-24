# BOT DINVELAS M1/M5 — IQ Option

Bot automatizado para operações na **IQ Option** usando a API `iqoptionapi`.  
Estratégia baseada no **Motor de Reversão V15** com suporte a múltiplos ativos simultâneos,  
prioridade de mercado Digital, múltiplos timeframes (M1/M5) e menu completo de configuração.

---

## Requisitos

```
pip install iqoptionapi configobj
```

### Arquivo de configuração `config.txt`

```ini
[LOGIN]
email = seu@email.com
senha = suasenha

[AJUSTES]
tipo = digital
purchase_buffer_seconds = 8
```

---

## Como usar

```bash
python BOTDINVELAS_M1M5.py
```

O bot exibe um menu interativo completo ao iniciar.

---

## Menu Inicial (passo a passo)

| Passo | Opção | Descrição |
|-------|-------|-----------|
| 1 | **Conta** | `demo` (PRACTICE) ou `real` (REAL) |
| 2 | **Timeframe** | `M1` (1 minuto) ou `M5` (5 minutos) |
| 3 | **Tipo de mercado** | Mercado Aberto (`-OP`) ou OTC |
| 4 | **Ativos simultâneos** | 1 a 8 ativos operados em paralelo |
| 5 | **Número máximo de entradas** | Quantidade de ordens aceitas (0 = ilimitado) |
| 6 | **Stop Loss** | % do saldo inicial (0 desativa) |
| 7 | **Stop Win** | % do saldo inicial (0 desativa) |
| 8 | **Valor por operação** | Fixo ($) ou percentual do saldo (%) |
| 9 | **Temporizador** | Minutos de operação (0 = ilimitado) |

---

## Funcionalidades

### 1. Prioridade Digital → Binária

- **Sempre tenta DIGITAL primeiro** antes de cada entrada.  
- Se o mercado digital estiver fechado, cai automaticamente para **BINÁRIA**.  
- **Antes de cada entrada**, o status digital/binária é re-verificado via API (sem cache).  
- Se a digital reabrir durante a sessão, o bot volta a usá-la automaticamente.  
- Usa `API.buy_digital_spot()` para digital e `API.buy()` para binária.  
- Se a ordem digital falhar, tenta fallback para binária automaticamente.  
- Controlado pelo flag global `PREFER_DIGITAL = True`.

### 2. Timeframe Selecionável: M1 ou M5

- Menu de seleção de timeframe ao iniciar o bot.  
- Todos os parâmetros se ajustam automaticamente:  
  - Janelas de sleep (`IDLE_SLEEP_S_M1`, `IDLE_SLEEP_S_M5`)
  - Expirações das ordens (1 min para M1, 5 min para M5)  
  - Períodos dos indicadores (ATR, ADX, BB, EMA slope)  
  - Janelas de entrada (`ENTRY_WINDOW_SECONDS_M1/M5`)  
  - Timeouts de resultado (`M1_RESULT_TIMEOUT`, `M5_RESULT_TIMEOUT`)

### 3. Estratégia Extra Rígida para M1

Ao selecionar **M1**, o modo `RIGIDA` é **aplicado automaticamente** com parâmetros ainda mais  
exigentes que o M5 rígido:

| Parâmetro | M5 Normal | M5 Rígido | M1 Extra-Rígido |
|-----------|-----------|-----------|-----------------|
| `V15_SCORE_MIN` | 80 | 80 | **90** |
| `V15_CONFIRM_POLLS` | 3 | 3 | **4** |
| `ADX_MIN_M1` | 17 | 19 | **21** (base+4) |
| `BB_WIDTH_MIN_M1` | 0.00045 | ×1.20 | **×1.35** |
| `SLOPE_MIN_M1` | 0.00008 | ×1.25 | **×1.50** |
| `ENTRY_WINDOW_SECONDS_M1` | 8s | 6s | **4s** |
| `ATR_ADAPTIVE_FACTOR` | 0.70 | 0.85 | **0.90** |

- **Score mínimo mais alto** (90 vs 80): sinal precisa de mais evidência  
- **Filtros de tendência e volatilidade mais exigentes** (ADX, BB width, slope)  
- **Buffer de confirmação reduzido** (janela de 4s ao invés de 8s)  
- **Mais polls de confirmação** (4 ao invés de 3)  
- **Bloqueio por candle**: `pending_lock_until` garante apenas 1 sinal ativo por ativo/candle  
- Para calibração: ajuste as constantes `V15_SCORE_MIN`, `ADX_MIN_M1`, etc.

### 4. Número Máximo de Entradas

- Menu para definir quantas **ordens aceitas** o bot deve executar.  
- Contagem baseada em **ordens confirmadas pela IQ Option** (com `order_id` válido).  
- Ordens rejeitadas, erros ou tentativas não contam.  
- Ao atingir o limite: **bot encerra automaticamente** exibindo o total.  
- `0` = ilimitado (bot opera até Stop/Temporizador/interrupção manual).

### 5. Logs e Prints Detalhados

Todos os passos são exibidos no console com emojis e timestamps:

```
🚀 Loop M5 multi-ativo | 4 ativo(s) | Modo: REVERSÃO
  📊 EURUSD-OP (binary)
  🎯 Limite de entradas: 10
⏳ Aguardando... (Ativo: EURUSD-OP | TF: M5)
🕯️ [14:32:05] [EURUSD-OP] Sinal confirmado: ReversalV15_CALL | Entrada: CALL | $10.00 | Mercado: DIGITAL (EURUSD) | secs_left=22
✅ [14:32:06] [EURUSD-OP] Ordem aceita (DIGITAL) | ID: 20817068286 | Entradas: 1/10
⏳ [14:32:06] [EURUSD-OP] Aguardando resultado...
✅ [14:37:26] [EURUSD-OP] Resultado: WIN | Profit: +8.70
```

Arquivos de log (pasta `logs/`):

| Arquivo | Conteúdo |
|---------|----------|
| `trades_log_<tag>.csv` | Cada operação: ativo, direção, resultado, lucro, tipo (digital/binary) |
| `latency_log_<tag>.csv` | Latência de cada compra |
| `patterns_log_<tag>.csv` | Sinais detectados e confirmados |
| `blocked_reasons_<tag>.log` | Motivos de bloqueio (ATR baixo, ADX fraco, etc.) |
| `runtime_errors_<tag>.log` | Erros em tempo de execução |

### 6. Stops Globais

- **Stop Loss**: encerra quando saldo cai abaixo de `saldo_inicial × (1 - SL%)`.  
- **Stop Win**: encerra quando saldo sobe acima de `saldo_inicial × (1 + SW%)`.  
- Verificados a cada ciclo do loop.

---

## Motor de Reversão V15

O bot usa um sistema de **score composto (0–100 pontos)**:

| Componente | Pontos | Sinal |
|------------|--------|-------|
| RSI | 0–25 | Oversold (≤30) → CALL, Overbought (≥70) → PUT |
| Bollinger Bands | 0–25 | Preço próximo da banda inferior → CALL, superior → PUT |
| Wick (sombra longa) | 0–25 | Sombra inferior → CALL, sombra superior → PUT |
| Impulso + Contexto | 0–25 | Downtrend+queda recente → CALL, Uptrend+alta recente → PUT |

- **Sinal disparado** quando score ≥ `V15_SCORE_MIN` e direção vencedora supera a oposta.  
- **Confirmação**: V15_CONFIRM_POLLS polls consecutivos confirmando a direção.  
- **Fallback v14**: Harami Bearish/Bullish e Hammer quando V15 não atinge pontuação mínima.  
- **Filtro estrutural M5**: sinal M5 só é aceito se a vela candidata estiver no extremo do range (20% mais baixo para CALL, 20% mais alto para PUT).

---

## Estrutura de Arquivos

```
.
├── BOTDINVELAS_M1M5.py   # Bot principal
├── config.txt             # Login e ajustes (criar manualmente)
├── favoritos.txt          # Lista de ativos preferidos (opcional)
├── README.md              # Este arquivo
├── logs/                  # Logs CSV e TXT (criado automaticamente)
├── state/                 # Estado do bot (criado automaticamente)
└── presets/               # Presets de configuração (criado automaticamente)
```

### favoritos.txt

```
# Um ativo por linha. Linhas com # são comentários.
EURUSD-OP
EURJPY-OP
GBPUSD-OP
```

---

## Parâmetros Configuráveis (no código)

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `PREFER_DIGITAL` | `True` | Prioriza mercado digital |
| `MAX_ENTRIES` | `0` | Definido no menu (0 = ilimitado) |
| `V15_SCORE_MIN` | `80` | Score mínimo M5 (90 para M1 rígido) |
| `CANDLES_LOOKBACK` | `320` | Candles buscados por ciclo |
| `MIN_CANDLES_REQUIRED` | `120` | Mínimo para análise |
| `PURCHASE_BUFFER_SECONDS` | `8` | Buffer antes do fechamento do candle |
| `ATR_MIN_RATIO_ABS_M1/M5` | variável | Limiar mínimo de ATR |
| `M5_EXTREME_FRAC` | `0.20` | Tolerância do filtro estrutural M5 (20%) |

---

## Versão

`2026-03-25-m1m5-digital-maxentries`
