---
title: Extrator PDF Jurídico
emoji: ⚖️
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 5.49.1
python_version: '3.12'
app_file: space.py
pinned: false
short_description: 'Extrator PDF Jurídico → Markdown (OCR local, PJe)'
---

# Extrator PDF Jurídico → Markdown

Aplicação web local que converte PDFs de processos judiciais (PJe/TJBA e
similares) em Markdown limpo, estruturado e fiel — sem perder conteúdo
jurídico e removendo todo o boilerplate de digitalização e assinatura
eletrônica.

**100% local**: os autos nunca saem da máquina.

## Como executar

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8077
```

Abra <http://localhost:8077>, arraste os PDFs e baixe os `.md`.

Para usuários sem Python: `instalar.bat` + `iniciar.bat` (ver
`COMO-INSTALAR.txt`), ou gere um executável único com `build.bat`
(`dist\Extrator.exe`, nada a instalar).

**OCR com segundo motor (opcional):** `pip install -r requirements-ocr2.txt`
no lugar do `requirements.txt` acrescenta um motor de rede neural
(RapidOCR), que relê cada página escaneada e substitui a leitura do
Tesseract só quando é claramente melhor. Também é local, pesa cerca de
300 MB e deixa a conversão 2,6 vezes mais lenta. O `instalar.bat` já o
instala num `.venv` próprio; o `Extrator.exe` e a imagem Docker não o
incluem. Detalhes e medição em [API.md](API.md), seção 5.

## O que a aplicação faz

1. **Extração de texto** com PyMuPDF, em ordem de leitura (`sort=True`).
   Páginas escaneadas (camada de texto ínfima + imagem grande, o caso dos
   processos antigos) passam por **OCR local** — Tesseract embutido no
   PyMuPDF, idioma `por`, 300 dpi, deskew automático até ±6°, uma
   página por núcleo da CPU em paralelo. Páginas nativas não pagam nada.
   Peças OCRizadas levam a nota `[peça reconhecida por OCR]` no `.md`;
   páginas com OCR de qualidade baixa geram aviso no card;
2. **Parse da capa do PJe** — metadados (número CNJ, classe, órgão,
   partes...) e a tabela "Documentos", com reconstituição dos Ids
   quebrados em duas linhas pela extração;
3. **Segmentação por âncora de Id** — o rodapé `Num. XXXXXXXXX - Pág. Y`
   de cada página é cruzado com o índice da capa: cada peça é delimitada
   com exatidão e recebe o tipo e a data oficiais do PJe;
4. **Limpeza de ruído** — rodapés de autenticação, `Fls/Visto`, blocos
   Sinesp/PPe, cabeçalhos institucionais repetidos, hifenização de
   quebra de linha (padrões observados em processos reais);
5. **Classificação** — tipo oficial da capa; fallback por scoring
   ponderado sobre o conteúdo (nunca substring simples);
6. **Markdown estruturado** — frontmatter YAML, Alertas, Índice completo
   da capa (mais recente primeiro), todas as peças na íntegra em ordem
   cronológica única e Inventário de Descarte;
7. **Prompts embutidos para análise por IA** — todo `.md` exportado
   começa com um bloco de proteção contra prompt injection (sempre
   incluído): o conteúdo dos autos é envelopado entre marcadores com
   token aleatório por arquivo, e o assistente de IA é instruído a
   tratar tudo dentro do envelope como dado, sinalizando comandos
   suspeitos com `[POSSÍVEL PROMPT INJECTION]`. Opcionalmente, o usuário
   escolhe um prompt especializado no catálogo em dois níveis
   (área → categoria → prompt): **Cartórios** (documentação, escrituras,
   registro de imóveis, inventários, procurações, certidões),
   **Assessoria Jurídica** (contratos, processos, petições, recursos,
   pareceres, pesquisa, gestão), **Magistrados** (organização, análise,
   provas, fundamentação, apoio à decisão, gabinetes) e **Prompts
   Universais** — 128 prompts no total (ver `app/pipeline/prompts.py`).
   O card de cada arquivo processado exibe qual prompt foi embutido.

## Garantias de fidelidade

- Peça com conteúdo jurídico **nunca é resumida** — em dúvida sobre a
  categoria, o texto integral é preservado;
- Toda página do PDF é contabilizada: pertence a uma peça, à capa ou ao
  Inventário de Descarte (somente boilerplate);
- Divergências (página órfã, documento do índice sem conteúdo, peça do
  corpo fora do índice) viram avisos exibidos no card do arquivo e na
  seção "Alertas" do próprio `.md` — nada falha em silêncio.

## Como funciona (visão técnica)

### Arquitetura

Três camadas, sem banco de dados e sem disco: o PDF entra como bytes,
é processado em memória e o Markdown volta na mesma resposta HTTP.

```
navegador (static/index.html)
   │  POST /api/converter  (multipart: arquivo + perfil)
   ▼
FastAPI (app/main.py)  ──run_in_threadpool──▶  processar()  (app/pipeline/processador.py)
   │  NDJSON: {"progresso"} … {"resultado"} | {"erro"}        │
   ▼                                                          ▼
navegador monta o card, guarda o .md               ocr → capa → segmentação → limpeza
   │  POST /api/zip  |  POST /api/lotes                → classificação → markdown → prompts
   ▼
download .zip
```

- **Dependências**: FastAPI + uvicorn (servidor), PyMuPDF (extração,
  renderização e Tesseract embutido), numpy (deskew). Nada mais.
- **Stateless**: cada requisição é independente; nenhum arquivo é
  gravado. Isso permite rodar com vários workers e em contêiner.
- **Concorrência**: `processar()` é CPU-bound e roda em `run_in_threadpool`
  para não travar o event loop. Dentro dele, o OCR abre um
  `ProcessPoolExecutor` com até `cpu_count - 1` processos (máx. 8), cada
  um com sua própria cópia do PDF aberta e `OMP_THREAD_LIMIT=1`.

### API de integração (/v1, Fórum Hub)

Para sistemas que convertem PDFs automaticamente há uma API separada em
`/v1`: conversão síncrona (`POST /v1/convert`), lotes assíncronos de até
500 PDFs com polling e callback (`/v1/batches`), autenticação Bearer,
CORS, cache por SHA-256 com teto de tamanho e retenção de 24 h. A saída é
o texto fiel página a página (`## Página N`), sem os prompts da
interface. Manual completo: [API.md](API.md); passo a passo de integração: [INTEGRACAO.md](INTEGRACAO.md). Contrato OpenAPI 3.1 em
`/v1/openapi.json`, documentação interativa em `/docs`.

```bash
API_CHAVES="forumhub:$(openssl rand -hex 24)" docker compose up -d --build
```

### API da interface web

| Rota | Entrada | Saída |
|------|---------|-------|
| `GET /` | — | `static/index.html` (sem cache) |
| `GET /api/perfis` | — | catálogo de prompts aninhado (área → categoria → prompt), usado para montar os seletores |
| `POST /api/converter` | multipart: `arquivo` (.pdf, ≤ 200 MB, assinatura `%PDF`) e `perfil` (id `area.categoria.prompt`, opcional) | `application/x-ndjson`: linhas `{"progresso":{"feitas","total"}}` durante o OCR e, por último, `{"resultado":{...}}` ou `{"erro":"..."}` |
| `POST /api/zip` | JSON `[{nome, markdown}]` | `.zip` com um `.md` por item |
| `POST /api/lotes` | JSON `{limite_kb, processos:[{autos, perfil}]}` | `.zip` com `MDemlote1.md`, `MDemlote2.md`… |

O `resultado` traz `nome`, `numero` (CNJ), `markdown` (documento final
com prompts), `autos` (só o conteúdo, usado para montar lotes), `stats`
(páginas, páginas OCR, peças principais/expediente, páginas descartadas,
chars bruto/limpo), `avisos` e `perfil` (prompt aplicado ou `null`).

O progresso é entregue como NDJSON porque o OCR é a única etapa lenta:
a thread de processamento chama `progresso(feitas, total)`, que empurra
a mensagem para uma `asyncio.Queue` via `call_soon_threadsafe`; o
gerador da `StreamingResponse` consome a fila e encerra ao ver
`resultado` ou `erro`. No navegador, `lerNdjson()` lê o corpo com
`ReadableStream` e atualiza a barra de cada card.

**Autenticação**: com `EXTRATOR_USUARIO` e `EXTRATOR_SENHA` definidas,
um middleware exige HTTP Basic em todas as rotas (comparação em tempo
constante com `secrets.compare_digest`). Sem as variáveis, roda aberto —
só para uso local. Ver `DEPLOY.md`.

### Pipeline (`app/pipeline/`)

Ordem de execução em `processador.processar()`:

1. **Extração** (`processador.extrair_com_ocr`) — `page.get_text("text",
   sort=True)` em todas as páginas. `sort=True` ordena os blocos por
   posição, o que é essencial nos layouts de duas colunas do PJe.
2. **Detecção de página escaneada** (`ocr.precisa_ocr`) — camada de texto
   com menos de 200 caracteres (o rodapé do PJe sozinho tem ~40) **e**
   imagens cobrindo ≥ 20% da página. Só essas páginas vão ao OCR; as
   nativas não pagam nada.
3. **OCR** (`ocr.ocr_paginas`) — Tesseract embutido no PyMuPDF, idioma
   `por` (`tessdata/por.traineddata`, variante *fast*), 300 dpi. Antes,
   `estimar_angulo` mede a inclinação por perfil de projeção horizontal
   (busca grossa de 0,5° em ±6°, refino de 0,1°). Abaixo de 1° o OCR roda
   direto com `full=False`, preservando a camada de texto original (o
   rodapé com o Id); acima disso a página é re-renderizada rotacionada,
   OCRizada com `full=True` e o rodapé original é concatenado ao fim
   (a segmentação usa a última ocorrência). `qualidade()` devolve a
   proporção de tokens com cara de palavra/número; abaixo de 0,7 vira
   aviso no card.
4. **Detecção da capa** (`processador._detectar_capa`,
   `capa.eh_capa`/`eh_continuacao_capa`) — a primeira página é capa
   quando soma ≥ 2 sinais do cabeçalho do PJe ("PJe - Processo Judicial",
   "Órgão julgador:", "Classe:", linha "Documentos") **e não tem** rodapé
   `Num. X - Pág. Y`. Páginas seguintes continuam a capa enquanto > 60%
   das linhas forem linhas da tabela de documentos (teto de 30 páginas).
5. **Parse da capa** (`capa.parsear_capa`) — regex para número, classe,
   órgão, assuntos, distribuição, valor da causa, segredo de justiça,
   justiça gratuita e partes com papel (`NOME (AUTOR)` etc.). Na tabela
   "Documentos" o Id costuma vir quebrado em duas linhas pela extração
   linear (`46439 … / 7941 …`); o parser concatena os fragmentos. A
   coluna "Tipo" vem colada ao fim de "Documento" e é separada por
   sufixo conhecido (lista ordenada do mais longo ao mais curto) ou por
   deduplicação de cauda (`Despacho Despacho`).
6. **Segmentação** (`segmentacao.segmentar`) — cada página do corpo tem
   o rodapé `Num. <Id> - Pág. N`; páginas consecutivas com o mesmo Id
   formam uma peça. Página sem rodapé herda a peça anterior — nunca se
   descarta página por falta de âncora. Em seguida
   `capa.casar_indice_com_rodapes` confirma os Ids do índice contra os
   Ids reais dos rodapés (um Id de capa incompleto casa com o rodapé do
   qual é prefixo, se houver um único candidato).
7. **Limpeza** (`limpeza.limpar_pagina`) — lista de regex de boilerplate
   removida de todas as páginas (rodapés de autenticação, `Fls/Visto`,
   blocos Sinesp/PPe, URLs, "Página N de M"…). Cabeçalhos institucionais
   ("PODER JUDICIÁRIO / TRIBUNAL DE JUSTIÇA…") são removidos apenas das
   páginas de continuação; a primeira página de cada peça os mantém.
   Hifenização de quebra de linha é desfeita (`investi-\ngado`).
   Uma peça cujo texto limpo tem menos de 30 caracteres alfanuméricos vai
   ao Inventário de Descarte com o motivo (boilerplate, página em branco
   ou, se passou por OCR, "imagem sem texto legível" + aviso).
8. **Classificação** (`classificacao.ClassificadorPecas`) — primeiro
   pelo tipo oficial da capa (`PREFIXOS_TIPO`: normalização sem acentos,
   sem "(Outros)" e sem a raiz dos nomes hierárquicos do padrão CNJ, como
   `Decisão\Acórdão`; vence o prefixo mais longo). A tabela cobre a
   nomenclatura antiga e a do CNJ publicada pelo TJPE, o catálogo do
   PJe/TJBA e os tipos vistos em capas reais. Tipo genérico ("Outros
   documentos") cai no título do documento, sem a numeração
   (`07-DESPACHO`), e depois no scoring por conteúdo: cada categoria tem
   regex com peso (10 para cabeçalho literal ou expressão decisória, 1
   para palavra ambígua), avaliadas nos primeiros 3 000 caracteres; sinal
   nos primeiros 400 dobra o peso; a categoria precisa atingir a
   pontuação mínima. Sem vencedor, a peça vira `DOCUMENTO`. Cada
   categoria tem uma **natureza** (juiz, tribunal, parte, cartório,
   prova ou anexo), mostrada no índice do `.md`: a primeira linha "juiz"
   é o último pronunciamento do juízo; "tribunal" marca acórdãos e
   comunicações do 2º grau. Quando tipo e título apontam autores
   diferentes ("Petição Inicial" cadastrada como "CERTIDÃO"), vale o
   tipo e sai um alerta de cadastro divergente. A natureza define o grupo dos
   contadores: **B** para cartório e anexo, **A** para o resto.
9. **Cobertura** — toda página do PDF deve estar na capa ou em alguma
   peça; páginas fora e documentos do índice sem conteúdo no corpo
   geram avisos.
10. **Markdown** (`markdown_gen.GeradorMarkdown`) — frontmatter YAML,
    `# Processo <número>`, "Alertas" (os mesmos avisos da API), tabela
    "Índice (mais recente primeiro)" com todas as entradas da capa (Id,
    data, hora, documento, tipo, categoria, natureza, páginas; "não localizadas" ou
    "(sem texto)" quando for o caso; documento sigiloso leva
    `[sigiloso]` no índice, `· sigiloso` no cabeçalho da peça e conta em
    `documentos_sigilosos` no frontmatter), "Peças (mais recente primeiro)" com
    cabeçalho `### Id · data hora · Documento [Tipo] · págs. X–Y` e
    "Inventário de Descarte". Certidões, intimações e demais expedientes
    ficam na ordem cronológica, junto das outras peças. Peças com OCR
    recebem a nota `[peça reconhecida por OCR]`.
11. **Prompts** (`prompts.montar_documento`) — gera um token aleatório
    (`secrets.token_hex(4)`), monta o bloco de segurança com os
    marcadores `<<AUTOS_token>>`…`<</AUTOS_token>>`, acrescenta o prompt
    especializado se escolhido e envelopa o Markdown dos autos. O
    `Resultado` guarda os dois: `markdown` (com prompts) e `autos` (sem).

### Lotes

`prompts.montar_lotes` agrupa vários processos em um só `.md` para
análise por IA com janela grande: concatena `autos` consecutivos até
`limite_kb` (100–10 240 KB, padrão 2 048 ≈ 500 mil tokens), um processo
nunca é cortado (se sozinho excede, vira lote próprio) e troca de prompt
abre novo lote. Cada lote tem um único bloco de segurança (com a regra
extra de não misturar processos) e um envelope por processo, precedido
de `<!-- PROCESSO i de n -->`.

### Pontos de entrada

| Arquivo | Uso |
|---------|-----|
| `uvicorn app.main:app` | desenvolvimento / Docker |
| `launcher.py` | `Extrator.exe` gerado por `build.bat`: `multiprocessing.freeze_support()` (workers do OCR em modo *spawn*), sobe uvicorn em `127.0.0.1:8077` e abre o navegador |
| `space.py` | Hugging Face Space (SDK Gradio/ZeroGPU): serve o FastAPI na porta 7860 e lança um Gradio mínimo numa porta lateral só para o ZeroGPU não encerrar o contêiner |

### Testes

Os módulos com lógica não trivial têm autoverificação no `__main__`:

```bash
python -m app.pipeline.ocr
```

```bash
python -m app.pipeline.prompts
```

O primeiro gera uma página sintética inclinada 5°, roda o OCR e confere
ângulo, qualidade e similaridade; o segundo confere a montagem dos
lotes. Ambos imprimem `ok`.

## Estrutura

```
app/
  main.py                 # API FastAPI da interface (rotas, NDJSON, HTTP Basic)
  api_v1.py               # API /v1 do Fórum Hub (lotes, cache, Bearer, CORS)
  conversor.py            # PDF -> Markdown página a página (usado pela /v1)
  pipeline/
    processador.py        # orquestrador PDF -> Markdown
    ocr.py                # OCR local de páginas escaneadas (+ deskew e giro)
    ocr2.py               # segundo motor de OCR, opcional (RapidOCR)
    tessdata/             # idioma português do Tesseract (por.traineddata)
    capa.py               # parse da capa do PJe (fonte de verdade)
    segmentacao.py        # segmentação por Id de rodapé
    limpeza.py            # remoção de boilerplate
    classificacao.py      # categorias e scoring
    markdown_gen.py       # geração do documento final
    prompts.py            # bloco anti-injection, catálogo de prompts, lotes
static/index.html         # interface web (fila de arquivos, NDJSON, zip)
launcher.py               # entrada do Extrator.exe
space.py                  # entrada do Hugging Face Space
Dockerfile, docker-compose.yml, DEPLOY.md   # publicação
API.md                    # manual da API /v1
INTEGRACAO.md             # guia de integração (passo a passo)
exemplos/                 # PDFs de exemplo, Markdown esperado, cliente TypeScript
tests/                    # testes da /v1 (python -m unittest discover -s tests)
```

## Limitações atuais

- A "qualidade" do OCR é uma heurística (proporção de tokens com cara de
  palavra), não a confiança real do Tesseract — serve para apontar páginas
  a conferir, não para medir precisão;
- Manuscritos e carimbos sobrepostos ao texto não são lidos com
  confiabilidade por OCR clássico;
- Padrões de limpeza calibrados para PJe/TJBA e Sinesp/PPe; outros
  tribunais podem exigir novos padrões em `limpeza.py`.
