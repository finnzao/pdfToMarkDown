# Manual da API /v1 — conversão de PDF em Markdown (Fórum Hub)

A API converte PDFs de processos judiciais em Markdown, um a um ou em lote,
para análise posterior por IA. Faz só a conversão: não resume, não
classifica e não extrai entidades. Tudo roda localmente (OCR Tesseract
embutido), nenhum documento sai do servidor.

- Contrato OpenAPI 3.1: `GET /v1/openapi.json` (serve para gerar o
  cliente TypeScript, com exemplos de requisição e resposta)
- Documentação interativa: `GET /docs`
- Passo a passo de integração (callback, erros, checklist): [INTEGRACAO.md](INTEGRACAO.md)
- Cliente de exemplo (fetch nativo): [`exemplos/cliente-forumhub.ts`](exemplos/cliente-forumhub.ts)

## 1. Subir o serviço

### Local (desenvolvimento)

```bash
pip install -r requirements.txt
```

```bash
API_CHAVES="forumhub:chave-local" API_CORS_ORIGENS="http://localhost:3000" uvicorn app.main:app --port 8077
```

No PowerShell, defina antes `$env:API_CHAVES="forumhub:chave-local"`.
Sem `API_CHAVES`, a `/v1` roda **sem autenticação** (o log avisa). Use
assim só na sua máquina.

### Produção (Docker)

```bash
API_CHAVES="forumhub:$(openssl rand -hex 24)" API_CORS_ORIGENS="https://forumhub.seudominio.com.br" docker compose up -d --build
```

A imagem já traz tudo o que o OCR precisa (Tesseract embutido no
PyMuPDF, idiomas `por` e `eng`). Coloque HTTPS na frente (Caddy, Nginx;
ver [DEPLOY.md](DEPLOY.md)) e libere corpo de requisição de até 2 GB
no proxy, por causa dos lotes.

**Rode um único processo** (`--workers 1`, já é o padrão da imagem). O
estado dos lotes fica em memória, e o OCR já usa todos os núcleos (um
processo por página). Para mais vazão, aumente `API_CONCORRENCIA`, não
o número de workers.

### Variáveis de ambiente

| Variável | Padrão | Para que serve |
|---|---|---|
| `API_CHAVES` | (vazio = sem autenticação) | `app:chave,app2:chave2`, uma chave por aplicação consumidora |
| `API_CORS_ORIGENS` | `http://localhost:3000,https://pje-services-web-frontend.vercel.app` | Origens liberadas para o navegador, separadas por vírgula. O padrão deixa o Fórum Hub (local ou na Vercel) chamar esta instância direto; o preflight também responde `Access-Control-Allow-Private-Network: true`, exigido pelo Chrome quando uma página pública fala com localhost |
| `API_DIR_TEMP` | temporário do sistema | Pasta de trabalho; a API usa a subpasta `forumhub-api/` |
| `API_LIMITE_SYNC_MB` | 25 | Tamanho máximo do `POST /v1/convert` |
| `API_LIMITE_SYNC_PAGINAS` | 200 | Páginas máximas do `POST /v1/convert` |
| `API_LIMITE_LOTE_MB` | 2048 | Tamanho total máximo de um lote |
| `API_LIMITE_LOTE_ARQUIVOS` | 500 | Arquivos máximos por lote |
| `API_CONCORRENCIA` | 2 | Conversões simultâneas (o resto espera na fila) |
| `API_RETENCAO_HORAS` | 24 | Por quanto tempo resultados e cache ficam guardados |
| `API_CACHE_MAX_MB` | 1024 | Teto do cache; `0` desliga o cache |
| `API_LIMITE_REQ_MIN` | 300 | Requisições por minuto por chave; `0` desliga |
| `PORTA` | 8077 | Porta do contêiner |

## 2. Autenticação, CORS e limites

- Toda rota da `/v1`, exceto `health` e `openapi.json`, exige
  `Authorization: Bearer <chave>`.
- A API funciona tanto do navegador (`fetch` direto, com preflight
  `OPTIONS` respondido para as origens de `API_CORS_ORIGENS`) quanto do
  backend Node. Os headers `X-Paginas`, `X-Ocr`, `X-Id` e `Retry-After`
  ficam expostos ao JavaScript.
- Acima de `API_LIMITE_REQ_MIN` requisições num minuto, a resposta é
  `429 LIMITE_EXCEDIDO` com `Retry-After` em segundos. O polling de lote a
  cada 2,5 s gasta 24 por minuto.

## 3. Endpoints

### `POST /v1/convert` — um PDF, síncrono

Até 25 MB **e** até 200 páginas. Acima disso a resposta é `413
MUITO_GRANDE`, com mensagem orientando usar o lote.

Corpo `multipart/form-data`:

| Campo | Obrigatório | Valor |
|---|---|---|
| `file` | sim | o PDF |
| `processo` | não | número CNJ (se faltar, vem do nome do arquivo) |
| `documento` | não | tipo do documento (se faltar, vem do nome do arquivo) |
| `ocr` | não | `auto` (padrão), `always` ou `never` |
| `formato` | não | `paginas` (padrão) ou `juridico` (ver [§4.1](#41-formato-juridico-e-lotes-para-ia)) |

Ou o PDF puro, com `Content-Type: application/pdf` e os mesmos campos na
query string (mais `arquivo`, o nome do arquivo de origem):

```bash
curl -X POST "http://localhost:8077/v1/convert?processo=8000123-45.2023.8.05.0001&documento=sentenca&arquivo=8000123-45.2023.8.05.0001_sentenca.pdf" -H "Authorization: Bearer $CHAVE" -H "Content-Type: application/pdf" --data-binary @sentenca.pdf
```

O nome no padrão `NUMERO_CNJ.pdf` ou `NUMERO_CNJ_tipo-documento.pdf`
preenche `processo` e `documento` sozinho:

```bash
curl -F "file=@8000123-45.2023.8.05.0001_sentenca.pdf" -H "Authorization: Bearer $CHAVE" http://localhost:8077/v1/convert
```

Resposta `200 application/json`:

```json
{
  "id": "a30417c8f883...",
  "arquivo": "8000123-45.2023.8.05.0001_sentenca.pdf",
  "processo": "8000123-45.2023.8.05.0001",
  "documento": "sentenca",
  "paginas": 3,
  "ocr": false,
  "totalPaginasOcr": 0,
  "paginasOcr": [],
  "markdown": "---\narquivo: ...",
  "avisos": ["Assinatura: Documento assinado eletronicamente por MARIA DE SOUZA, em 10/03/2024 14:00:00."],
  "tempoMs": 84,
  "cache": false
}
```

`id` é o SHA-256 do PDF. `paginasOcr` lista as páginas (base 1) que
passaram por OCR. `cache: true` indica resultado reaproveitado.

Com `Accept: text/markdown`, a resposta é só o Markdown, e os metadados
vêm nos headers `X-Paginas`, `X-Ocr` (`true`/`false`) e `X-Id`:

```bash
curl -F "file=@sentenca.pdf" -H "Authorization: Bearer $CHAVE" -H "Accept: text/markdown" -o sentenca.md http://localhost:8077/v1/convert
```

### `POST /v1/batches` — lote, assíncrono

`multipart/form-data` com vários campos `files[]` **ou** um único campo
`zip` (as subpastas do zip são mantidas no resultado). Campos opcionais:
`ocr`, `formato` e `callbackUrl`.

- Até 500 arquivos e 2 GB no total.
- Nunca é recusado por ocupação: entra na fila e é processado com
  concorrência limitada.
- No zip, só entram arquivos `.pdf`. Caminhos com `..`, absolutos ou
  ocultos são ignorados.
- Nomes repetidos em `files[]` viram `nome (2).pdf`.

```bash
curl -F "files[]=@a.pdf" -F "files[]=@b.pdf" -F "callbackUrl=https://forumhub.exemplo/callback" -H "Authorization: Bearer $CHAVE" http://localhost:8077/v1/batches
```

Resposta `202`: `{ "batchId": "3f6d2c1a…", "total": 2, "status": "queued" }`

### `GET /v1/batches/{batchId}` — progresso

Feito para polling a cada 2 a 3 s; responde em poucos milissegundos
mesmo com 500 itens.

```json
{
  "batchId": "3f6d2c1a…",
  "status": "running",
  "total": 37, "feitos": 12, "falhas": 1,
  "atual": "pasta/8000999-11.2022.8.05.0001.pdf",
  "itens": [
    { "arquivo": "pasta/8000123-45.2023.8.05.0001_sentenca.pdf", "status": "done", "paginas": 12, "ocr": true, "erro": null },
    { "arquivo": "pasta/8000555-00.2021.8.05.0001.pdf", "status": "failed", "paginas": null, "ocr": null,
      "erro": { "codigo": "PDF_PROTEGIDO", "mensagem": "O PDF está protegido por senha. Remova a senha e envie novamente." } }
  ]
}
```

- `status` do lote: `queued`, `running`, `done`, `failed` ou `cancelled`.
  Fica `done` se ao menos um item converteu; `failed` só se todos falharam.
- `feitos` conta os itens já processados (`done` + `failed`); `falhas`
  conta só os `failed`. A barra de progresso é `feitos / total`.
- O erro de um item não interrompe o lote.

### `GET /v1/batches/{batchId}/result` — zip com os Markdowns

Disponível quando o lote termina com ao menos um item pronto (antes
disso: `409 RESULTADO_INDISPONIVEL`). O zip tem um `.md` por PDF, com o
mesmo nome e a mesma estrutura de pastas da entrada, mais um
`manifest.json`:

```json
{
  "batchId": "3f6d2c1a…", "status": "done", "total": 2, "falhas": 1,
  "itens": [
    { "arquivo": "pasta/a.pdf", "markdown": "pasta/a.md", "status": "done", "id": "…sha256…",
      "paginas": 12, "ocr": true, "totalPaginasOcr": 3, "paginasOcr": [4, 5, 9],
      "avisos": ["página 9: OCR de baixa qualidade (62%), conferir com o original"], "erro": null },
    { "arquivo": "b.pdf", "markdown": null, "status": "failed", "id": null, "paginas": null,
      "ocr": null, "totalPaginasOcr": null, "paginasOcr": null, "avisos": [],
      "erro": { "codigo": "PDF_CORROMPIDO", "mensagem": "O PDF está corrompido ou não pôde ser aberto." } }
  ]
}
```

### `GET /v1/batches/{batchId}/items/{arquivo}` — um item do lote

`arquivo` é o caminho do PDF dentro do lote, com subpastas, codificado
para URL (`encodeURIComponent` em cada segmento). A resposta é o mesmo
JSON do `/v1/convert`, ou só o Markdown com `Accept: text/markdown`. Se o
item falhou, a resposta é `422` com o erro dele; se ainda não foi
processado, `409 ITEM_INDISPONIVEL`.

### `DELETE /v1/batches/{batchId}` — cancelar e apagar

Cancela o que ainda não começou e apaga os resultados. É idempotente:
sempre responde `204`. Depois disso, `result` e `items` respondem `404`.
O `GET` do lote continua mostrando `status: "cancelled"` até a retenção
expirar.

### Callback

Com `callbackUrl`, ao terminar o lote a API envia um `POST` para essa URL
com o mesmo JSON do `GET /v1/batches/{batchId}`. O header `X-Signature`
traz o HMAC-SHA256 (hexadecimal) do corpo, calculado com a chave da
aplicação. São 3 tentativas, com esperas de 2 s e 8 s. O polling
continua funcionando com ou sem callback. Para validar no Fórum Hub:

```ts
import { createHmac, timingSafeEqual } from "node:crypto";
const esperado = createHmac("sha256", CHAVE).update(corpoBruto).digest("hex");
const ok = timingSafeEqual(Buffer.from(esperado), Buffer.from(req.headers["x-signature"] ?? ""));
```

Use o corpo **bruto** (bytes recebidos), não um `JSON.stringify` do
objeto já parseado.

### `POST /v1/lotes-md` — monta os MDemlote para análise por IA

Corpo JSON: `{ "processos": [{ "autos": "…" }, …], "limiteKb": 2048 }`, com o
campo `autos` de itens convertidos em `formato=juridico`. Resposta: zip com
`MDemlote1.md`, `MDemlote2.md`… de até `limiteKb` cada (um processo nunca é
cortado). Cada arquivo traz o bloco de proteção uma vez e um processo por
envelope `<<AUTOS_token>>`, igual ao botão "Lotes" da interface web.

### `GET /v1/health` (sem autenticação)

`{ "ok": true, "versao": "1.0.0", "fila": 3, "ocrDisponivel": true, "ocrNeural": true }`.
`ocrNeural` diz se o segundo motor de OCR está instalado (ver seção 5).
`fila` soma os itens de lote pendentes e as conversões esperando vaga.

## 4. Formato do Markdown

```markdown
---
arquivo: 8000123-45.2023.8.05.0001_sentenca.pdf
processo: 8000123-45.2023.8.05.0001
documento: sentenca
paginas: 3
ocr: false
geradoEm: 2026-10-30T14:03:00-03:00
---

## Página 1

### SENTENÇA

Processo nº 8000123-45.2023.8.05.0001

### RELATÓRIO

Trata-se de ação de indenização proposta por JOÃO DA SILVA em face do BANCO EXEMPLO S.A., ...

## Página 3

|Parte|Papel|Valor|
|---|---|---|
|JOÃO DA SILVA|Autor|R$ 4.350,00|
```

- Toda página vira `## Página N`, na ordem do PDF, inclusive as vazias.
  Elas recebem `_[sem texto reconhecível]_` e geram um aviso.
- Páginas em duas colunas são lidas coluna por coluna.
- Só viram `###` as linhas inteiras em maiúsculas que começam com um
  título jurídico conhecido (SENTENÇA, RELATÓRIO, DISPOSITIVO, DECISÃO,
  DESPACHO, CERTIDÃO, DOS FATOS…). Nenhum título é inventado.
- Tabelas com grade detectável viram tabelas Markdown.
- Saem: cabeçalhos e rodapés repetidos (blocos na faixa de 12% do topo
  ou do rodapé que se repetem em pelo menos 60% das páginas), número de
  página, carimbo de assinatura eletrônica, URL de validação e rodapé
  `Num. … - Pág. …` do PJe. Uma cópia do texto da assinatura vai para
  `avisos` (`Assinatura: …`).
- O texto não é resumido, corrigido nem traduzido. A hifenização de fim de
  linha é desfeita, os espaços são normalizados, os parágrafos ficam
  separados por linha em branco, e caracteres de controle são removidos.
- O `.md` exportado é o mesmo para quem consome por IA: a `/v1` não
  embute prompts (isso é só da interface web).

### 4.1 Formato `juridico` e lotes para IA

Com `formato=juridico`, o PDF passa pelo pipeline do Extrator em vez da
conversão página a página: detecção da capa do PJe, segmentação por Id de
peça, linha do tempo, peças de expediente e inventário de descarte. O
`markdown` devolvido é o mesmo da interface web (bloco de proteção contra
prompt injection, front-matter do processo, um envelope `<<AUTOS_token>>`), e
a resposta ganha o campo `autos`: o conteúdo sem envelope, para juntar vários
processos num só arquivo com `POST /v1/lotes-md`. `paginasOcr` vem vazio
nesse formato; `totalPaginasOcr` continua preenchido. O cache é separado por
formato.

Fluxo no Fórum Hub para análise em lote: converter cada PDF com
`formato=juridico`, guardar o `autos` de cada um e, no fim do download,
chamar `/v1/lotes-md` com todos.

## 5. OCR

- `ocr=auto` (padrão): a página vai ao OCR quando sobra pouco texto depois
  de tirar o carimbo do PJe e há imagem cobrindo a página, quando a camada
  de texto é lixo (caracteres inválidos ou palavras sem forma), ou quando o
  texto foi desenhado como curvas. Antes do OCR, o carimbo que o PJe
  imprime sobre os scans antigos é apagado da página (só na cópia em
  memória), para não virar lixo no texto.
- `ocr=always` faz OCR em todas as páginas; `ocr=never`, em nenhuma.
- Idioma `por`, com nova tentativa em `por+eng` quando a qualidade fica
  abaixo de 70%. Há correção automática de inclinação até ±6°.
- Página escaneada de lado ou de cabeça para baixo é detectada (quase não
  tem palavras comuns do português) e refeita desvirada em 90°, 180° ou
  270°. A troca só vale se a nova leitura render claramente mais palavras.
- **Segundo motor (opcional, só local):** com `pip install -r
  requirements-ocr2.txt` (o `instalar.bat` já faz isso num `.venv`), cada
  página de OCR também é lida por um motor de rede neural (RapidOCR, CPU,
  modelos dentro do pacote, nada sai da máquina). A leitura do Tesseract é
  mantida, a não ser que a outra tenha claramente mais palavras e não perca
  palavras comuns. Em 477 páginas reais: 28 melhoraram, nenhuma piorou,
  com 2,6 vezes o tempo de conversão. O `GET /v1/health` mostra
  `ocrNeural: true` quando ele está ativo.
- Linhas de baixa confiança (pontuação solta, letras isoladas, tokens sem
  forma de palavra, típicos de carimbo e rubrica) são descartadas e
  contadas em `avisos`. Páginas com qualidade abaixo de 70% também geram
  aviso.
- As páginas com OCR estão em `paginasOcr`.

## 6. Identidade, cache e retenção

- O `id` de cada conversão é o SHA-256 do PDF. Reenviar o mesmo PDF (com o
  mesmo `ocr`) reaproveita o resultado sem reprocessar (`cache: true`). O
  front-matter é refeito a cada pedido, então nome, processo e documento
  seguem o pedido atual.
- **Teto do cache** (`API_CACHE_MAX_MB`): ao atingir o limite, a API para
  de gravar no cache e segue convertendo normalmente, só sem guardar.
  Quando a retenção apaga entradas antigas e libera espaço, volta a
  gravar sozinha. Falha de disco ao gravar também só desliga o cache
  naquela vez. Os dois casos aparecem no log.
- Retenção: o PDF é apagado assim que é convertido. O Markdown, o
  manifesto e o cache ficam por `API_RETENCAO_HORAS` (24 h) e depois são
  apagados por uma faxina que roda a cada 10 minutos.
- O log nunca recebe conteúdo: só id (sha256 truncado), bytes, páginas,
  tempo e tipo de erro. O access log do uvicorn fica desligado na imagem,
  porque a URL de um item contém o nome do arquivo.

## 7. Erros

Formato único:
`{ "erro": { "codigo": "...", "mensagem": "...", "detalhes": {} } }`. A
mensagem é em português e pode ir direto para a tela.

| HTTP | código | quando |
|---|---|---|
| 400 | `ARQUIVO_INVALIDO` | não é PDF, campo `file` ausente, zip inválido ou sem PDFs |
| 400 | `PARAMETRO_INVALIDO` | `ocr` fora de auto/always/never, `callbackUrl` inválida |
| 401 | `NAO_AUTORIZADO` | chave ausente ou inválida |
| 404 | `LOTE_NAO_ENCONTRADO` | id desconhecido, expirado, cancelado ou de outra chave |
| 404 | `ITEM_NAO_ENCONTRADO` | arquivo inexistente no lote |
| 409 | `RESULTADO_INDISPONIVEL` / `ITEM_INDISPONIVEL` | ainda em processamento |
| 413 | `MUITO_GRANDE` | acima do limite síncrono ou do lote |
| 422 | `PDF_PROTEGIDO` / `PDF_CORROMPIDO` | não dá para abrir |
| 429 | `LIMITE_EXCEDIDO` | limite por chave; ver `Retry-After` |
| 500 | `ERRO_INTERNO` | falha não prevista; `detalhes.correlacao` liga ao log |

Um PDF protegido ou corrompido nunca devolve Markdown parcial. Página
ilegível dentro de um PDF válido vira `_[falha ao ler a página]_` com
aviso.

## 8. Fluxo no Fórum Hub

1. O download termina com N PDFs no navegador.
2. **N = 1:** `POST /v1/convert` com `Accept: text/markdown`, e o `.md`
   é gravado ao lado do PDF. Se vier `413`, mande pelo lote.
3. **N > 1:** monte um zip no navegador (mantendo as pastas), faça
   `POST /v1/batches`, consulte `GET /v1/batches/{id}` a cada 2,5 s
   mostrando `feitos/total` e `atual`, baixe `/result` e grave cada `.md`
   na pasta do PDF correspondente (o caminho é o mesmo do zip de entrada).
4. Mostre ao usuário os itens com `erro` (a `mensagem` já é amigável).
5. Opcional: `DELETE /v1/batches/{id}` depois de baixar, para não esperar
   as 24 h.

O script [`exemplos/cliente-forumhub.ts`](exemplos/cliente-forumhub.ts)
implementa os passos 2 a 5 com `fetch` nativo:

```bash
API_URL=http://localhost:8077 API_CHAVE=sua-chave node exemplos/cliente-forumhub.ts a.pdf b.pdf
```

## 9. Testes

```bash
pip install -r requirements-dev.txt
```

```bash
python -m unittest discover -s tests -v
```

Cobrem PDF com texto, digitalizado, misto e protegido; PDF corrompido;
modos de OCR; `Accept: text/markdown`; PDF puro no corpo; limite síncrono
e de requisições; lote com zip e subpastas (inclusive tentativa de `../`);
`files[]` com nomes repetidos; callback com HMAC; cache por hash; cache
cheio seguindo sem gravar; expiração; CORS; OpenAPI; e desempenho (20
páginas de texto em menos de 3 s).

Os PDFs de exemplo e o Markdown esperado ficam em `exemplos/`
(`python tests/gerar_exemplos.py` recria tudo). Processos reais, que
são sigilosos e ficam fora do repositório, são testados quando a pasta
`PROCESSOS_REAIS` existe (padrão `~/Downloads/processos`).
