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

## O que a aplicação faz

1. **Extração de texto** com PyMuPDF, em ordem de leitura (`sort=True`);
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
6. **Markdown estruturado** — frontmatter YAML, Linha do Tempo, peças
   principais transcritas na íntegra, peças de expediente em seção
   própria (também íntegras) e Inventário de Descarte;
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
- Divergências (página órfã, documento do índice sem conteúdo) viram
  avisos exibidos no card do arquivo — nada falha em silêncio.

## Estrutura

```
app/
  main.py                 # API FastAPI
  pipeline/
    processador.py        # orquestrador PDF -> Markdown
    capa.py               # parse da capa do PJe (fonte de verdade)
    segmentacao.py        # segmentação por Id de rodapé
    limpeza.py            # remoção de boilerplate
    classificacao.py      # categorias e scoring
    markdown_gen.py       # geração do documento final
static/index.html         # interface web
```

## Limitações atuais

- PDFs 100% escaneados (sem camada de texto) ainda não passam por OCR —
  próxima evolução planejada (Tesseract nativo);
- Padrões de limpeza calibrados para PJe/TJBA e Sinesp/PPe; outros
  tribunais podem exigir novos padrões em `limpeza.py`.
