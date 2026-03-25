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
- A lista inicial de ativos já é montada priorizando **todos os digitais abertos**, incluindo
  índices como **Dollar Index (DXY), CXY (Canadian Dollar Index), BXY (Pound Index)** e
  quaisquer outros que a API IQ Option disponibilize na categoria `'digital'`.
- Índices e ativos sem sufixo `-OTC`/`-OP` que existam somente no book digital **sempre entram como digital**.  
- **Binária só é adicionada** se não houver equivalente digital aberto para o ativo.  
- Nenhum ativo aparece duplicado: cada par é listado **uma única vez**, sempre como digital quando possível.  
- Se o mercado digital estiver fechado, cai automaticamente para **BINÁRIA** como fallback.  
- **Antes de cada entrada**, o status digital/binária é re-verificado via API (sem cache).  
- Se a digital reabrir durante a sessão, o bot volta a usá-la automaticamente.  
- Usa `API.buy_digital_spot_v2()` para digital e `API.buy()` para binária.  
- Se a ordem digital falhar, tenta fallback para binária automaticamente.  
- Controlado pelo flag global `PREFER_DIGITAL = True`.

### 1a. Aliases para nomes de ativos

O bot aceita abreviações populares em `favoritos.txt` e as resolve automaticamente para o
nome real da IQ Option ao buscar no book digital:

| Alias (favoritos.txt) | Nome real na IQ Option |
|-----------------------|------------------------|
| `DXY`                 | Dollar Index           |
| `POUNDINDEX`          | BXY (nome canônico)    |
| `DOLLARINDEX`         | Dollar Index           |
| `GBPINDEX`            | BXY (nome canônico)    |
| `CANADIANDOLLARINDEX` | CXY (nome canônico)    |

> **Nota:** `BXY` e `CXY` já são os nomes canônicos da API IQ Option — escreva-os
> diretamente em `favoritos.txt` ou use os aliases acima.

### 1b. Filtragem de ativos por tipo de mercado

O book inicial é filtrado rigorosamente conforme o mercado selecionado:

- **Mercado Aberto**: inclui somente ativos `-OP` e índices sem sufixo. Ativos com `-OTC`
  em qualquer parte do nome (ex: `BTCUSD-OTC-OP`) são **sempre excluídos**.
- **Mercado OTC**: inclui somente ativos com `-OTC` no nome. Ativos `-OP` não são incluídos.

Essa filtragem elimina a possibilidade de um ativo OTC aparecer misturado no book de Mercado Aberto.

### 2. Timeframe Selecionável: M1 ou M5

- Menu de seleção de timeframe ao iniciar o bot.  
- Todos os parâmetros se ajustam automaticamente:  
  - Janelas de sleep (`IDLE_SLEEP_S_M1`, `IDLE_SLEEP_S_M5`)
  - Expirações das ordens (1 min para M1, 5 min para M5)  
  - Períodos dos indicadores (ATR, ADX, BB, EMA slope)  
  - Janelas de entrada (`ENTRY_WINDOW_SECONDS_M1/M5`)  
  - Timeouts de resultado (`M1_RESULT_TIMEOUT`, `M5_RESULT_TIMEOUT`)

### 3. Perfis do M1: Conservador e Extra Rígido

Ao selecionar **M1**, o bot pergunta qual **perfil** usar:

#### M1 Conservador ✅ (recomendado para uso operacional)

Parâmetros equilibrados para frequência saudável de entradas, mantendo qualidade técnica:

| Parâmetro | Valor |
|-----------|-------|
| `V15_SCORE_MIN` | **84** |
| `V15_CONFIRM_POLLS` | **2** |
| `ADX_MIN_M1` | **18** |
| `BB_WIDTH_MIN_M1` | **0.00050** |
| `SLOPE_MIN_M1` | **0.00009** |
| `ENTRY_WINDOW_SECONDS_M1` | **5s** |

#### M1 Extra Rígido 🔬 (laboratório/apresentação)

Parâmetros ultra-seletivos para demonstração de robustez — pouquíssimas entradas:

| Parâmetro | M5 Normal | M5 Rígido | M1 Extra-Rígido |
|-----------|-----------|-----------|-----------------|
| `V15_SCORE_MIN` | 80 | 80 | **90** |
| `V15_CONFIRM_POLLS` | 3 | 3 | **4** |
| `ADX_MIN_M1` | 17 | 19 | **21** (base+4) |
| `BB_WIDTH_MIN_M1` | 0.00045 | ×1.20 | **×1.35** |
| `SLOPE_MIN_M1` | 0.00008 | ×1.25 | **×1.50** |
| `ENTRY_WINDOW_SECONDS_M1` | 8s | 6s | **4s** |
| `ATR_ADAPTIVE_FACTOR` | 0.70 | 0.85 | **0.90** |

- **Bloqueio por candle**: `pending_lock_until` garante apenas 1 sinal ativo por ativo/candle  
- Para calibração: ajuste as constantes `V15_SCORE_MIN`, `ADX_MIN_M1`, etc.

### 4. Filtro Estrutural M1 (v15.2)

Complementa o motor V15 no timeframe M1 com localização estrutural no **micro-range recente**:

- **Janela**: últimas **8 velas** (ajustável via `M1_STRUCTURAL_CANDLES`)
- **CALL**: aceito somente se o fechamento da vela de sinal está no **1/3 inferior** do micro-range → zona de suporte recente
- **PUT**: aceito somente se o fechamento da vela de sinal está no **1/3 superior** do micro-range → zona de resistência recente
- Sinais no **meio do range** são descartados automaticamente (zonas ruidosas)
- Se não houver velas suficientes, o filtro não bloqueia (fail-safe)
- **Fallback M1**: os padrões Harami e Hammer também passam por este filtro no M1. Fallback sem filtro estrutural só é permitido no M5.

Este filtro melhora a qualidade das entradas M1 sem precisar endurecer os filtros quantitativos.

### 5. Confirmação M1 com Margem Dinâmica (ATR)

Para sinais V15 no **M1**, a confirmação usa uma **margem proporcional ao ATR** em vez de comparação direta com o fechamento, reduzindo falsos positivos causados por micro-oscilações:

- **CALL confirmado**: `preço atual > fechamento da vela de sinal + (ATR × 0.1)`
- **PUT confirmado**: `preço atual < fechamento da vela de sinal − (ATR × 0.1)`

Para o M5, a confirmação continua usando comparação direta com o fechamento (comportamento original).

**Preset documentado (M1 Conservador):**

```python
V15_SCORE_MIN = 84
V15_CONFIRM_POLLS = 2
ADX_MIN_M1 = 18
BB_WIDTH_MIN_M1 = 0.00050
SLOPE_MIN_M1 = 0.00009
ENTRY_WINDOW_SECONDS_M1 = 5
# + filtro estrutural leve (1/3 micro-range, janela 8 velas)
# + confirmação com buffer ATR×0.1
```

### 6. Número Máximo de Entradas

- Menu para definir quantas **ordens aceitas** o bot deve executar.  
- Contagem baseada em **ordens confirmadas pela IQ Option** (com `order_id` válido).  
- Ordens rejeitadas, erros ou tentativas não contam.  
- Ao atingir o limite: **bot encerra automaticamente** exibindo o total.  
- `0` = ilimitado (bot opera até Stop/Temporizador/interrupção manual).

### 7. Logs e Prints Detalhados

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
| `patterns_log_<tag>.csv` | Sinais detectados/confirmados com componentes de score detalhados |
| `blocked_reasons_<tag>.log` | Motivos de bloqueio (ATR baixo, ADX fraco, etc.) |
| `runtime_errors_<tag>.log` | Erros em tempo de execução |

#### Colunas do `patterns_log_<tag>.csv`

| Coluna | Descrição |
|--------|-----------|
| `ts_iso` | Timestamp ISO 8601 do evento |
| `instance_tag` | Tag da instância do bot |
| `ativo` | Nome do ativo |
| `tf_min` | Timeframe em minutos (1 ou 5) |
| `event` | Tipo de evento: `detected`, `confirmed`, `expired`, `rejected`, `error` |
| `pattern_name` | Nome do padrão detectado (ex: `ReversalV15_CALL`, `HaramiBearish`) |
| `pattern_mode` | Origem do padrão: `v15` (motor principal) ou `fallback` (Harami/Hammer) |
| `pattern_from` | Timestamp de abertura da vela de sinal |
| `expected_confirm_from` | Timestamp esperado de início da confirmação |
| `direction_hint` | Direção prevista: `call` ou `put` |
| `confirmed` | `1` se confirmado, `0` caso contrário |
| `confirm_from` | Timestamp de início da confirmação (se confirmado) |
| `rsi_pts` | Pontuação RSI contribuída para o score (0–25) |
| `bb_pts` | Pontuação Bollinger Bands contribuída (0–25) |
| `wick_pts` | Pontuação Wick (sombra) contribuída (0–25) |
| `imp_pts` | Pontuação Impulso+Contexto contribuída (0–25) |
| `call_score` | Score total da direção CALL |
| `put_score` | Score total da direção PUT |
| `block_reason` | Motivo do bloqueio, se aplicável (ex: `expired`, `rejected`) |
| `details` | Informações adicionais opcionais |

### 8. Stops Globais

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

- **Sinal disparado** quando score ≥ `V15_SCORE_MIN` **E** a diferença entre o score vencedor e o oposto ≥ `V15_SCORE_GAP_MIN` (padrão: **10 pontos**). Isso evita entradas baseadas em empates técnicos:
  - call=85, put=84 → diferença = 1 → **não entra** (abaixo do gap mínimo)
  - call=90, put=75 → diferença = 15 → **entra** (acima do gap mínimo, sinal confiável)
- **Confirmação**: V15_CONFIRM_POLLS polls consecutivos confirmando a direção.  
- **Fallback v14**: Harami Bearish/Bullish e Hammer quando V15 não atinge pontuação mínima. No M1, fallback também exige aprovação pelo filtro estrutural.  
- **Filtro estrutural M5**: sinal M5 só é aceito se a vela candidata estiver no extremo do range (20% mais baixo para CALL, 20% mais alto para PUT).  
- **Filtro estrutural M1**: sinal M1 só é aceito se a vela candidata estiver no 1/3 inferior (CALL) ou 1/3 superior (PUT) do micro-range das últimas 8 velas. Aplicado também ao fallback no M1.  
- **Confirmação M1 com margem ATR**: no timeframe M1, a confirmação V15 usa buffer `ATR × 0.1` para filtrar ruído de micro-oscilação.

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
| `V15_SCORE_MIN` | `80` | Score mínimo M5 (84 para M1 conservador, 90 para M1 extra rígido) |
| `V15_SCORE_GAP_MIN` | `10` | Diferença mínima entre call_score e put_score para validar sinal V15 |
| `CANDLES_LOOKBACK` | `320` | Candles buscados por ciclo |
| `MIN_CANDLES_REQUIRED` | `120` | Mínimo para análise |
| `PURCHASE_BUFFER_SECONDS` | `8` | Buffer antes do fechamento do candle |
| `ATR_MIN_RATIO_ABS_M1/M5` | variável | Limiar mínimo de ATR |
| `M5_EXTREME_FRAC` | `0.20` | Tolerância do filtro estrutural M5 (20%) |
| `M1_STRUCTURAL_CANDLES` | `8` | Janela do filtro estrutural M1 (velas) |

---

## Versão

`2026-03-25-m1m5-digital-v4`

