# MetodoBOTDIN

Bot de sinais para IQ Option baseado na estratégia QuadCode (EMAA/EMAB/EMAC/EMAD + Donchian + TA/TB + ENC/ENV).

## Estrutura do projeto

```
Edson/
├── MetodoBOTDIN.py      # Script principal
├── config.txt           # Suas credenciais e parâmetros (criado a partir do example; NÃO versionar)
├── config.example.txt   # Modelo sem credenciais (versionar; copiar para config.txt)
├── Ativos.txt           # Lista de ativos por seção (edite conforme desejado)
├── requirements.txt     # Dependências (configobj, websocket-client)
├── iqoptionapi/         # Pasta da API local (coloque aqui a sua cópia)
├── logs/                # Criada em runtime; excluída do git
│   └── YYYY-MM-DD/
│       ├── signals_YYYY-MM-DD_HH-MM-SS_<tag>.csv
│       └── events_YYYY-MM-DD_HH-MM-SS_<tag>.log
├── state/               # Criada em runtime; excluída do git
└── presets/             # Criada em runtime; excluída do git
```

## Requisitos

- Python **3.11** (recomendado; não misture com Python 3.13)
- `websocket-client==1.9.0`
- `configobj>=5.0.8`
- Pasta `iqoptionapi/` na raiz do projeto (API local vendorizada)

## Instalação (recomendado: usar venv)

Abra o terminal do VS Code na pasta do repositório e execute:

```bat
:: Cria ambiente virtual
python -m venv .venv

:: Ativa no Windows
.\.venv\Scripts\activate

:: Atualiza pip e instala dependências
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

> **Sobre `iqoptionapi`**: a biblioteca é carregada da pasta `iqoptionapi/`
> localizada na raiz do projeto, sem necessidade de instalar via pip.
> Coloque sua cópia da API nessa pasta antes de rodar.

## Configuração

### 1. Credenciais (uma única vez)

Copie o modelo e preencha:

```bat
copy config.example.txt config.txt
```

Abra `config.txt` e preencha a seção `[LOGIN]`:

```ini
[LOGIN]
email = seu_email@iqoption.com
senha = sua_senha
```

> ⚠️ **`config.txt` está no `.gitignore`** — nunca será comitado acidentalmente.

### 2. Parâmetros da estratégia

Ainda em `config.txt`, ajuste a seção `[ESTRATEGIA]`:

```ini
[ESTRATEGIA]
ema_a = 3
ema_b = 7
ema_c = 17
ema_d = 34
donchian = 20
ta = 5
tb = 5
enc_percent = 0.001
```

### 3. Lista de ativos

Edite `Ativos.txt` conforme sua preferência.  
Organize por seções (`[DIGITAL M1]`, `[BINARIA M1]`, `[DIGITAL M5]`, `[BINARIA M5]`) e use **sufixo obrigatório**:

| Sufixo | Mercado          |
|--------|------------------|
| `-op`  | Mercado aberto   |
| `-OTC` | Fora de horário  |

Exemplo:

```
[DIGITAL M1]
EURJPY-op
EURJPY-OTC
EURUSD-op

[BINARIA M1]
EURUSD-op
EURUSD-OTC
```

> O bot **não reescreve** o arquivo — ele normaliza em memória e filtra
> pelo mercado escolhido no menu.

## Executando

```bat
:: Ative o venv (se usar)
.\.venv\Scripts\activate

:: Execute
python MetodoBOTDIN.py
```

### Menu de inicialização (aparece a cada execução)

```
============================================================
        MetodoBOTDIN  —  Menu de Inicialização
============================================================
  Usuário : seu_email@iqoption.com

  1. Conta        [demo/real]:       demo
  2. Timeframe    [M1/M5]:           M1
  3. Mercado      [OP/OTC]:          OP
  4. Ativos simultâneos [1-4]:       1
  5. Valor por entrada:              1.0
  6. Stop Win  (0 = desabilitado):   0.0
  7. Stop Loss (0 = desabilitado):   0.0
  8. Max entradas (0 = sem limite):  10
```

### Seleção de ativos

- O bot lê as seções correspondentes ao timeframe escolhido (ex.: `[DIGITAL M1]` e `[BINARIA M1]`).
- Filtra pelo mercado selecionado (`-op` para OP, `-OTC` para OTC).
- Verifica abertura via `get_all_open_time()` e monta o pool de N ativos.
- Prioridade: **DIGITAL** primeiro → completa com **BINARIA** se necessário.
- Se nenhum ativo estiver aberto, aguarda 60 s e tenta novamente.

### Endpoints de ordem

| Tipo   | Função API                 | api_asset (OP)  | api_asset (OTC)  |
|--------|---------------------------|-----------------|------------------|
| DIGITAL | `buy_digital_spot_v2`    | `EURJPY`        | `EURJPY-OTC`     |
| BINARIA | `buy`                    | `EURJPY`        | `EURJPY-OTC`     |

## Logs

Somente **sinais confirmados** aparecem no console.  
Todo o detalhe fica nos arquivos de log:

- `logs/YYYY-MM-DD/signals_*.csv`  → CSV com todos os sinais avaliados
- `logs/YYYY-MM-DD/events_*.log`   → Log textual de eventos da sessão

## Nota sobre estratégia

O motor de sinais está **funcional** com os filtros base (alinhamento de EMAs,
Donchian breakout, ENC/ENV, TA/TB). Procure os marcadores `# TODO` em
`MetodoBOTDIN.py` para integrar a lógica definitiva do QuadCode.

## Segurança

- **Nunca** versione `config.txt` com senhas reais — ele está no `.gitignore`.
- Use sempre `config.example.txt` como referência para o formato.
- As pastas `logs/`, `state/` e `presets/` são excluídas do git automaticamente.
