# PROMPT — Criar Aplicação: Extrator de Processos Judiciais (PDF → Markdown)

> Entregue este prompt a um LLM com capacidade de gerar código (Claude Code, etc.).
> Ele especifica a aplicação completa. Quanto mais literal a implementação, melhor.

---

## PROMPT (copie daqui para baixo)

Você é um engenheiro de software sênior especializado em processamento de documentos e em sistemas do Judiciário brasileiro (PJe, PPe/Sinesp). Construa uma aplicação web completa, em **um único arquivo `index.html`** (HTML + CSS + JS, sem build step), que converte PDFs de processos judiciais brasileiros em Markdown limpo, estruturado e fiel.

### 1. CONTEXTO E OBJETIVO

O usuário é da área jurídica e processa lotes de PDFs baixados do PJe (TJBA e outros tribunais). Cada PDF é a íntegra de um processo: capa do PJe com índice de documentos, seguida das peças (portarias, boletins de ocorrência, despachos, sentenças, atas de audiência, certidões, cartas precatórias etc.), com muito ruído de digitalização e assinatura eletrônica.

O objetivo é gerar um `.md` por PDF que sirva como versão de trabalho dos autos: navegável, sem boilerplate, com cada peça identificada e delimitada, em ordem cronológica, **sem jamais resumir ou alterar o conteúdo jurídico das peças principais**.

Requisitos não funcionais inegociáveis:
- **100% local**: nenhum dado sai do navegador (documentos são sigilosos). Bibliotecas via CDN são aceitáveis; upload de conteúdo, não.
- Processamento em lote (múltiplos PDFs em paralelo) com progresso por arquivo.
- Robustez: um PDF corrompido não pode derrubar a fila.

### 2. STACK

- `pdf.js` (extração de texto nativo + renderização para OCR);
- `tesseract.js` com idioma `por` (OCR de fallback);
- `jszip` (download em lote);
- IndexedDB (cache de resultados por SHA-256 do arquivo);
- Sem frameworks. JS moderno (ES2020+), módulos inline.

### 3. PIPELINE DE PROCESSAMENTO (por PDF)

**Fase 1 — Extração nativa paralela.** Extraia o texto de todas as páginas com `getTextContent()`, reordenando itens por coordenadas (y desc, x asc) e inserindo espaço/quebra de linha por gap geométrico (evita palavras grudadas e ordem de leitura errada). Concorrência adaptativa a `navigator.hardwareConcurrency` e `navigator.deviceMemory`.

**Fase 2 — Decisão de OCR por página.** Uma página vai para OCR quando: (a) texto < limiar configurável (default 50 chars); (b) densidade de caracteres corrompidos de encoding (∞ È „ ˙ ı etc.) > 2%; (c) razão de caracteres latinos válidos < 55%; (d) menos de 15 palavras reais (3+ letras). Antes do OCR, renderize a ~2200px de largura e aplique grayscale + binarização Otsu. Pool de workers Tesseract via Scheduler, dimensionado pela memória (~1 worker por 1,2 GB, teto 8), com `preserve_interword_spaces=1` e `user_defined_dpi=300`.

**Fase 3 — Parse da capa do PJe (fonte de verdade).** A primeira(s) página(s) do PDF é a capa: metadados do processo + tabela "Documentos" com colunas Id / Data da Assinatura / Documento / Tipo. Extraia:
- Metadados: número do processo, classe, órgão julgador, assuntos, data de distribuição, segredo de justiça, partes com seus papéis (AUTOR, INVESTIGADO, RÉU, EXEQUENTE...), outros participantes;
- **O índice de documentos**: lista ordenada de `{id, data, titulo, tipo}`. Atenção: na extração, o Id frequentemente aparece quebrado em duas linhas (ex.: `46439` numa linha e `7941` na seguinte formam o Id `464397941`) — implemente a reconstituição juntando o fragmento numérico da linha seguinte quando o Id tiver menos dígitos que o padrão (9–10 dígitos).

A capa NÃO entra no corpo do Markdown — vira o frontmatter e a linha do tempo.

**Fase 4 — Segmentação por âncora de Id (primária) + cabeçalhos (fallback).** Cada página do corpo termina com rodapé `Num. XXXXXXXXX - Pág. Y`. Esse número É o Id da tabela da capa. Segmente as páginas agrupando por Id: todas as páginas com o mesmo `Num.` pertencem à mesma peça, e o tipo/data oficiais vêm da capa. Só quando não houver rodapé `Num.` utilizável, caia para segmentação por cabeçalhos formais de peça (AUTUAÇÃO, PORTARIA, DESPACHO, SENTENÇA, ATA DE AUDIÊNCIA, CARTA PRECATÓRIA, CERTIDÃO...).

**Fase 5 — Limpeza.** Remova, por regex, todo o boilerplate (padrões reais do PJe/TJBA e Sinesp/PPe):
- `Este documento foi gerado pelo usuário...`, `Número do documento: ...`, URLs `https://pje...listView.seam?x=...`, `Assinado eletronicamente por: ...`;
- `Fls: N` / `Visto:` (inclusive duplicados pela extração: `FFllss::`, `VViissttoo::`);
- `Gerado por Sinesp Segurança`, bloco `O sigilo deste documento é protegido...`, bloco `A autenticidade do documento pode ser conferida...`, `Informe o código verificador (MAC)...`, `Este documento ainda poderá receber assinaturas`, `Código Verificador (MAC): ... CRC: ...`, `Impresso por: ... IP de Registro: ...`, `Data de Impressão: ...`, `Pg. N/M`, `Página N de N`;
- Cabeçalhos institucionais repetidos por página (`PODER JUDICIÁRIO / TRIBUNAL DE JUSTIÇA...`, `GOVERNO DO ESTADO DA BAHIA / POLÍCIA CIVIL / DELEGACIA TERRITORIAL...`) — preserve apenas a primeira ocorrência dentro de cada peça;
- Rodapés `Num. XXXXX - Pág. Y` (depois de usados na segmentação);
- Hifenização de quebra de linha (`investi-\ngado` → `investigado`) e colapso de 3+ quebras em 2.

Página que após a limpeza fica com < 30 caracteres substantivos: descartada, mas registrada no inventário.

**Fase 6 — Classificação e tratamento.** Cada peça cai em um de três grupos:
- **Grupo A (transcrição integral)**: AUTUAÇÃO, PORTARIA, BO, TERMO DE DECLARAÇÕES, INTERROGATÓRIO, RELATÓRIO, DENÚNCIA, RESPOSTA À ACUSAÇÃO, ALEGAÇÕES FINAIS, PETIÇÃO, PARECER, MANIFESTAÇÃO, DECISÃO, DESPACHO, SENTENÇA, PRONÚNCIA, ATA DE AUDIÊNCIA, CARTA PRECATÓRIA, LAUDO, MEDIDA PROTETIVA;
- **Grupo B (resumo em lista)**: CERTIDÃO, INTIMAÇÃO, OFÍCIO, MANDADO, ATO ORDINATÓRIO, CONCLUSÃO, REMESSA, RECIBO, JUNTADA;
- **Grupo C (descarte registrado)**: páginas só de assinatura, folhas de rosto Sinesp, páginas vazias.

Quando o tipo vier da capa (Fase 4), mapeie o tipo oficial do PJe para o grupo. Quando vier do fallback, use classificador por **scoring ponderado**: cada tipo tem sinais (regex) com pesos 1/3/6/10, bônus se o sinal aparece nos primeiros 400 caracteres, e pontuação mínima por tipo — nunca substring simples com early-return (gera falsos positivos: petição que cita "apelação" não é recurso). Em dúvida entre A e B, prefira A.

**Fase 7 — Geração do Markdown.** Estrutura exata:

```markdown
---
processo: "NNNNNNN-DD.AAAA.J.TR.OOOO"
classe: "..."
orgao_julgador: "..."
assuntos: "..."
partes:
  autor: "..."
  reu_investigado: "..."
  outros: ["..."]
distribuicao: "DD/MM/AAAA"
segredo_justica: false
total_paginas_pdf: NN
pecas_transcritas: NN
gerado_em: "AAAA-MM-DD"
fonte: "arquivo.pdf"
---

# Processo NNNNNNN-DD.AAAA.J.TR.OOOO

## Linha do Tempo

| Data | Peça | Id (PJe) | Págs. PDF |
|------|------|----------|-----------|

---

## 1. TIPO DA PEÇA — DD/MM/AAAA `[Id NNNNNNNNN · págs. N–M]`

(texto integral limpo)

---

## Peças de Expediente (resumo)

- **Tipo** — DD/MM/AAAA `[Id ... · pág. N]`
  > primeira linha significativa da peça (até 140 chars)

---

## Inventário de Descarte

- Págs. N–M: descrição do que foi descartado e porquê.
```

Regras de fidelidade: nunca parafrasear nem "melhorar" redação de peça do Grupo A; corrigir apenas artefatos de extração; texto ilegível vira `[ilegível]`; campos estruturados (BO, qualificação de partes) formatados como `**Campo:** valor`; datas `DD/MM/AAAA` no corpo, ISO no frontmatter; omitir metadado ausente (nunca inferir).

### 4. INTERFACE

- Dropzone (clique ou arraste, múltiplos PDFs), tema escuro profissional;
- Opções: OCR automático (on), forçar OCR (off), limiar de chars/página, cache (on);
- Card por arquivo: nome, barra de progresso por fase (extração azul / OCR âmbar), estatísticas (páginas, peças, % OCR, chars bruto→limpo, tempo), estado por cor (ok / erro / cache);
- Ações por arquivo: baixar `.md`, copiar, pré-visualizar em modal;
- Ações globais: baixar todos em ZIP, limpar lista, limpar cache;
- Resumo agregado do lote e indicador do hardware detectado (núcleos/memória/workers).

### 5. QUALIDADE E ROBUSTEZ

- try/catch por página e por arquivo: falha isolada não interrompe o lote; erro aparece no card;
- Cache IndexedDB keyed por SHA-256; hit exibe o resultado instantaneamente com selo "cache";
- Liberar memória: `page.cleanup()`, zerar canvas após OCR;
- Nome do arquivo de saída: `<numero-do-processo>.md` (extraia o número CNJ do nome do arquivo ou da capa);
- Código comentado por seção, constantes de regex agrupadas e nomeadas no topo.

### 6. CRITÉRIOS DE ACEITAÇÃO (teste mental antes de entregar)

1. Um PDF de 76 páginas do PJe/TJBA com capa de 2 páginas, ~30 páginas de conteúdo e ~40 páginas de rodapé de assinatura produz: frontmatter completo, linha do tempo com todos os Ids da capa, peças do Grupo A íntegras, certidões resumidas, e inventário cobrindo as páginas descartadas — **a soma dos intervalos cobre 100% do PDF**.
2. Nenhum rodapé de autenticação, `Fls/Visto` ou URL do PJe sobra no corpo.
3. PDF escaneado sem camada de texto é processado inteiramente via OCR sem intervenção.
4. Dois PDFs processados simultaneamente terminam sem corromper progresso um do outro.
5. Reprocessar o mesmo arquivo com cache ligado retorna em < 1s.

Entregue o `index.html` completo e funcional, sem placeholders nem "TODO".
