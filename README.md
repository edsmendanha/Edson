# MetodoBOTDIN

Bot de sinais para IQ Option baseado na estratégia QuadCode (EMAA/EMAB/EMAC/EMAD + Donchian + TA/TB + ENC/ENV).

## Estrutura do projeto

```
MetodoBOTDIN/
├── MetodoBOTDIN.py      # Script principal
├── config.txt           # Configurações (preencha com suas credenciais)
├── Ativos.txt           # Lista de ativos (um por linha)
├── requirements.txt     # Dependências externas (sem iqoptionapi)
├── iqoptionapi/         # Pasta da API local (coloque aqui a sua cópia)
├── logs/                # Criada em runtime; .gitignore exclui do git
│   └── YYYY-MM-DD/
│       ├── signals_YYYY-MM-DD_HH-MM-SS_<tag>.csv
│       └── events_YYYY-MM-DD_HH-MM-SS_<tag>.log
├── state/               # Criada em runtime; .gitignore exclui do git
└── presets/             # Criada em runtime; .gitignore exclui do git
```

## Requisitos

- Python **3.11** (recomendado; não misture com Python 3.13)
- `websocket-client==1.9.0`
- Pasta `iqoptionapi/` na raiz do projeto (API local vendorizada)

## Instalação (recomendado: usar venv)

```bat
cd D:\MetodoBOTDIN

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

1. Abra `config.txt` e preencha:
   - `email` e `senha` com suas credenciais IQ Option.
   - `tipo_conta = demo` para testar, `real` para operar.
   - `modo_operacao = op` (normal) ou `otc` (somente OTC).
   - Demais parâmetros conforme comentários no arquivo.

2. Edite `Ativos.txt` com os ativos desejados (um por linha):
   - Ativos OP:  `EURUSD`, `GBPUSD`, …
   - Ativos OTC: `EURUSD-OTC`, `GBPUSD-OTC`, …
   - OTC e OP **não** são misturados; o menu deixa você escolher antes de rodar.

## Executando

```bat
:: Ative o venv primeiro (se usar)
.\.venv\Scripts\activate

:: Execute
python MetodoBOTDIN.py
```

O bot apresenta um menu de inicialização onde você confirma ou altera o modo
(OP ou OTC) antes de começar.

## Logs

- `logs/YYYY-MM-DD/signals_*.csv`  → CSV com todos os sinais avaliados.
- `logs/YYYY-MM-DD/events_*.log`   → Log textual de eventos da sessão.

Somente sinais **confirmados** aparecem no console.
Sinais detectados (parciais), bloqueados e rejeitados ficam apenas no arquivo de log.

## Nota sobre execução de ordens

A execução de ordens está atualmente em modo **STUB** (simulação).
Para ativar ordens reais, substitua a função `execute_order_stub` em
`MetodoBOTDIN.py` pela chamada real à IQ Option API.

## Segurança

- **Nunca** versione `config.txt` com senhas reais. Use `config.local.txt`
  (já no `.gitignore`) para sua cópia local com credenciais.
- As pastas `logs/`, `state/` e `presets/` são excluídas do git por `.gitignore`.
