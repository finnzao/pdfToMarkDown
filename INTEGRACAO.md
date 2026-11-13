# Guia de integração com a API /v1

Este guia é o passo a passo para ligar um sistema (o Fórum Hub ou outro) à
API de conversão de PDF em Markdown. A referência completa de cada rota,
campo e código de erro está em [API.md](API.md); aqui fica o **como fazer**.

## Visão geral

```
Seu sistema ──PDF──► POST /v1/convert ──────────────► Markdown (na hora)
            ──PDFs─► POST /v1/batches ──► 202 + batchId
                     GET  /v1/batches/{id}   (polling a cada 2,5 s)
                     ◄── POST callbackUrl    (opcional, assinado com HMAC)
                     GET  /v1/batches/{id}/result ──► zip com .md + manifest.json
                     DELETE /v1/batches/{id} (opcional, limpa na hora)
```

Regra de escolha:

| Situação | Use |
|---|---|
| 1 PDF de até 25 MB e 200 páginas | `POST /v1/convert` |
| Vários PDFs, ou 1 PDF maior que isso | `POST /v1/batches` |
| `/v1/convert` respondeu `413 MUITO_GRANDE` | reenvie pelo lote |

## Passo 1 — Obter uma chave

Cada sistema consumidor tem a própria chave, definida por quem administra
o servidor na variável `API_CHAVES` (formato `nome:chave`, separadas por
vírgula):

```bash
API_CHAVES="forumhub:$(openssl rand -hex 24),outro-sistema:$(openssl rand -hex 24)"
```

A chave serve para duas coisas: autenticar as chamadas
(`Authorization: Bearer <chave>`) e assinar o callback do lote.

> **Onde guardar a chave.** Se o `fetch` for feito do navegador, a chave
> fica visível para qualquer usuário (DevTools). Para uso interno, em rede
> fechada, isso pode ser aceitável. Para uso aberto, faça as chamadas pelo
> **backend** do seu sistema e deixe o navegador falar só com ele.

Se as chamadas vierem do navegador, a origem do seu sistema precisa estar
em `API_CORS_ORIGENS` (ex.: `https://forumhub.seudominio.com.br`).

## Passo 2 — Testar a conexão

```bash
curl https://extrator.seudominio.com.br/v1/health
```

Resposta esperada: `{"ok": true, "versao": "1.0.0", "fila": 0, "ocrDisponivel": true}`.
Essa rota não pede chave e serve para monitoramento.

Depois teste a chave convertendo um PDF:

```bash
curl -F "file=@teste.pdf" -H "Authorization: Bearer SUA_CHAVE" -H "Accept: text/markdown" https://extrator.seudominio.com.br/v1/convert
```

`401 NAO_AUTORIZADO` significa chave errada. Um erro de CORS no console
do navegador significa origem fora de `API_CORS_ORIGENS`.

## Passo 3 — Converter um PDF

### TypeScript (navegador ou Node 18+)

```ts
const API = "https://extrator.seudominio.com.br";
const CHAVE = process.env.API_CHAVE!; // no backend

async function converter(pdf: Blob, nome: string): Promise<string> {
  const form = new FormData();
  form.append("file", pdf, nome);           // nome no padrão CNJ preenche processo/documento
  // form.append("processo", "8000123-45.2023.8.05.0001");  // opcional
  // form.append("documento", "sentenca");                  // opcional
  // form.append("ocr", "auto");                            // auto | always | never

  const r = await fetch(`${API}/v1/convert`, {
    method: "POST",
    headers: { Authorization: `Bearer ${CHAVE}`, Accept: "text/markdown" },
    body: form,
  });
  if (!r.ok) {
    const { erro } = await r.json();
    throw Object.assign(new Error(erro.mensagem), { codigo: erro.codigo, status: r.status });
  }
  // metadados nos headers: X-Paginas, X-Ocr, X-Id
  return r.text();
}
```

Sem o header `Accept: text/markdown`, a resposta é um JSON com o Markdown
e os metadados (`paginas`, `paginasOcr`, `avisos`, `tempoMs`…). Use o JSON
quando quiser mostrar os `avisos` ao usuário.

### Python (backend)

```python
import requests

API = "https://extrator.seudominio.com.br"
CHAVE = "SUA_CHAVE"

with open("8000123-45.2023.8.05.0001_sentenca.pdf", "rb") as f:
    r = requests.post(f"{API}/v1/convert",
                      headers={"Authorization": f"Bearer {CHAVE}"},
                      files={"file": f}, data={"ocr": "auto"}, timeout=300)
if r.ok:
    dados = r.json()
    print(dados["paginas"], dados["avisos"])
    open("sentenca.md", "w", encoding="utf-8").write(dados["markdown"])
else:
    print(r.json()["erro"]["mensagem"])
```

> **Timeout.** Um PDF digitalizado leva cerca de 0,5 s por página no OCR.
> Use um timeout de pelo menos 5 minutos no `/v1/convert`, ou mande PDFs
> grandes pelo lote.

## Passo 4 — Converter em lote

### 4.1 Enviar

Duas formas, no mesmo `POST /v1/batches`:

- vários campos `files[]`, um por PDF;
- um único campo `zip`. As subpastas do zip se mantêm no resultado, o que
  facilita gravar cada `.md` ao lado do PDF de origem.

```ts
const form = new FormData();
for (const { blob, nome } of pdfs) form.append("files[]", blob, nome);
// ou: form.append("zip", zipBlob, "lote.zip");
// form.append("callbackUrl", "https://forumhub.seudominio.com.br/api/conversao-pronta");

const r = await fetch(`${API}/v1/batches`, {
  method: "POST", headers: { Authorization: `Bearer ${CHAVE}` }, body: form,
});
const { batchId, total } = await r.json(); // 202
```

O lote nunca é recusado por excesso de trabalho: ele entra na fila. Os
limites são 500 arquivos e 2 GB por lote (acima disso, `413`).

### 4.2 Acompanhar

Escolha **polling**, **callback** ou os dois.

**Polling**, bom para mostrar uma barra de progresso:

```ts
async function aguardar(batchId: string) {
  for (;;) {
    const r = await fetch(`${API}/v1/batches/${batchId}`, {
      headers: { Authorization: `Bearer ${CHAVE}` },
    });
    if (r.status === 429) {                       // limite por minuto
      await espera(Number(r.headers.get("Retry-After") ?? 5) * 1000);
      continue;
    }
    const lote = await r.json();
    mostrarProgresso(lote.feitos, lote.total, lote.atual);
    if (["done", "failed", "cancelled"].includes(lote.status)) return lote;
    await espera(2500);
  }
}
const espera = (ms: number) => new Promise((ok) => setTimeout(ok, ms));
```

**Callback**, bom para backend, sem conexão aberta. Veja o
[Passo 5](#passo-5--receber-o-callback).

### 4.3 Baixar o resultado

```ts
const r = await fetch(`${API}/v1/batches/${batchId}/result`, {
  headers: { Authorization: `Bearer ${CHAVE}` },
});
const zip = await r.arrayBuffer(); // um .md por PDF + manifest.json
```

Cada PDF gera um `.md` no mesmo caminho do arquivo de entrada
(`pasta/a.pdf` → `pasta/a.md`). O `manifest.json` lista cada item com
`status`, `paginas`, `paginasOcr`, `avisos` e `erro`. Use o manifest para
saber quais itens falharam, em vez de procurar `.md` que não existem.

Para buscar só um item (útil para reprocessar ou exibir um documento):

```ts
const caminho = "pasta/8000123-45.2023.8.05.0001_sentenca.pdf"
  .split("/").map(encodeURIComponent).join("/");
await fetch(`${API}/v1/batches/${batchId}/items/${caminho}`, {
  headers: { Authorization: `Bearer ${CHAVE}`, Accept: "text/markdown" },
});
```

### 4.4 Limpar

Os resultados ficam 24 h no servidor. Depois de baixar, apague o lote:

```ts
await fetch(`${API}/v1/batches/${batchId}`, {
  method: "DELETE", headers: { Authorization: `Bearer ${CHAVE}` },
}); // sempre 204, pode repetir sem problema
```

O mesmo `DELETE` cancela um lote em andamento: o que ainda não começou
deixa de ser processado.

## Passo 5 — Receber o callback

Quando o lote termina (`done` ou `failed`), a API faz um `POST` na
`callbackUrl` com o mesmo JSON do `GET /v1/batches/{id}`. O header
`X-Signature` traz o HMAC-SHA256 em hexadecimal do corpo, calculado com
a sua chave.

Regras para o receptor:

1. **Valide a assinatura sobre o corpo bruto**, os bytes exatos recebidos.
   Não use `JSON.stringify` de um objeto já parseado, porque a assinatura
   não bate.
2. Responda **2xx rápido**. Qualquer erro, ou demora acima de 10 s, conta
   como falha. São 3 tentativas no total (a 2ª depois de 2 s, a 3ª depois
   de 8 s). Depois disso a API desiste, e o lote continua disponível por
   polling.
3. Seja **idempotente**: o mesmo `batchId` pode chegar mais de uma vez.
   Isso acontece se a sua resposta se perder.
4. Baixe o `/result` **depois** de responder, em segundo plano.
5. A `callbackUrl` precisa ser alcançável **a partir do servidor da API**,
   não do navegador do usuário.

Exemplo com Express:

```ts
import express from "express";
import { createHmac, timingSafeEqual } from "node:crypto";

const app = express();
const processados = new Set<string>(); // use o banco em produção

app.post("/api/conversao-pronta", express.raw({ type: "application/json" }), (req, res) => {
  const esperado = Buffer.from(createHmac("sha256", process.env.API_CHAVE!).update(req.body).digest("hex"));
  const recebido = Buffer.from(String(req.headers["x-signature"] ?? ""));
  if (esperado.length !== recebido.length || !timingSafeEqual(esperado, recebido)) {
    return res.sendStatus(401);
  }
  const lote = JSON.parse(req.body.toString("utf-8"));
  res.sendStatus(204);                                   // responde primeiro
  if (processados.has(lote.batchId)) return;             // idempotência
  processados.add(lote.batchId);
  void baixarResultado(lote.batchId);                    // GET /result em segundo plano
});
```

Exemplo com Python (FastAPI):

```python
import hashlib, hmac, os
from fastapi import FastAPI, Request, Response, BackgroundTasks

app = FastAPI()

@app.post("/api/conversao-pronta")
async def conversao_pronta(request: Request, tarefas: BackgroundTasks):
    corpo = await request.body()
    esperado = hmac.new(os.environ["API_CHAVE"].encode(), corpo, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, request.headers.get("x-signature", "")):
        return Response(status_code=401)
    lote = await request.json()
    tarefas.add_task(baixar_resultado, lote["batchId"])
    return Response(status_code=204)
```

## Passo 6 — Tratar erros

Todo erro vem no mesmo formato, com uma mensagem em português que pode ir
direto para a tela:

```json
{ "erro": { "codigo": "PDF_PROTEGIDO", "mensagem": "O PDF está protegido por senha. Remova a senha e envie novamente.", "detalhes": {} } }
```

O que fazer em cada caso:

| Código | Repetir? | Ação recomendada |
|---|---|---|
| `NAO_AUTORIZADO` (401) | não | Chave errada ou ausente; corrija a configuração |
| `ARQUIVO_INVALIDO`, `PARAMETRO_INVALIDO` (400) | não | Mostre a mensagem ao usuário |
| `PDF_PROTEGIDO`, `PDF_CORROMPIDO` (422) | não | Mostre a mensagem; o usuário precisa de outro arquivo |
| `MUITO_GRANDE` (413) no `/convert` | sim, pelo lote | Reenvie em `POST /v1/batches` |
| `MUITO_GRANDE` (413) no lote | sim, dividido | Quebre em lotes menores |
| `LOTE_NAO_ENCONTRADO` (404) | não | O lote expirou (24 h) ou foi apagado; envie de novo |
| `RESULTADO_INDISPONIVEL`, `ITEM_INDISPONIVEL` (409) | sim | Ainda processando; continue o polling |
| `LIMITE_EXCEDIDO` (429) | sim | Espere o número de segundos em `Retry-After` |
| `ERRO_INTERNO` (500) | sim, uma vez | Se persistir, informe `detalhes.correlacao` a quem mantém a API |
| falha de rede / 502 / 503 | sim | Repita com espera crescente (2 s, 4 s, 8 s) |

No lote, o erro de um PDF **não** derruba os outros: ele aparece no item
correspondente (`itens[].erro`) e no `manifest.json`.

## Passo 7 — Usar o Markdown

Cada `.md` começa com um front-matter YAML que identifica o documento:

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
...
```

- Cada página vira um `## Página N`. Isso permite citar a página de origem
  ao mandar o texto para uma IA ou fazer busca.
- Página sem texto legível vira `_[sem texto reconhecível]_`, e o caso vem
  descrito em `avisos`.
- Mostre os `avisos` ao usuário quando houver OCR de baixa qualidade.
  Eles indicam quais páginas convém conferir no original.

Se o nome do arquivo seguir o padrão `NUMERO_CNJ.pdf` ou
`NUMERO_CNJ_tipo-documento.pdf`, `processo` e `documento` são preenchidos
sozinhos. Do contrário, envie esses campos no formulário.

## Boas práticas

- **Reenvio é barato:** o mesmo PDF (mesmo SHA-256 e mesmo modo de `ocr`)
  sai do cache em milissegundos, com `cache: true`. Não é preciso guardar
  controle próprio para evitar reprocessamento.
- **Polling a cada 2,5 s** consome 24 das 300 requisições por minuto da
  chave. Não faça polling de vários lotes em paralelo com intervalo menor.
- **Nunca registre o Markdown em log** no seu sistema. Os documentos têm
  dados pessoais e sigilosos, e a API também não registra.
- Use `DELETE` depois de baixar, para que nada fique no servidor além do
  necessário.
- Gere o cliente a partir do contrato, se preferir: `GET /v1/openapi.json`
  (OpenAPI 3.1, com exemplos).

## Checklist de integração

- [ ] Chave própria cadastrada em `API_CHAVES` e guardada no backend
- [ ] Origem do sistema em `API_CORS_ORIGENS` (se chamar do navegador)
- [ ] `GET /v1/health` respondendo pela rede do seu sistema
- [ ] `/v1/convert` com fallback para o lote em `413`
- [ ] Polling com tratamento de `429` / `Retry-After`
- [ ] Callback validando `X-Signature` sobre o corpo bruto, idempotente
- [ ] Erros exibidos a partir de `erro.mensagem`
- [ ] Itens com falha lidos do `manifest.json`
- [ ] `DELETE` do lote depois de baixar o resultado
- [ ] Timeout de pelo menos 5 min no `/v1/convert`

Cliente completo, pronto para copiar:
[`exemplos/cliente-forumhub.ts`](exemplos/cliente-forumhub.ts)
(`API_URL=... API_CHAVE=... node exemplos/cliente-forumhub.ts a.pdf b.pdf`).
