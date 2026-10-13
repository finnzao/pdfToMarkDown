# Deploy do Extrator PDF Jurídico

## Antes de expor na web (obrigatório)

Os documentos processados são **sigilosos**. Nunca publique a aplicação
sem estes três itens:

1. **Autenticação** — defina as variáveis de ambiente:
   ```
   EXTRATOR_USUARIO=seu-usuario
   EXTRATOR_SENHA=uma-senha-longa-e-aleatoria
   ```
   Com elas definidas, todas as rotas exigem HTTP Basic (o navegador
   pede usuário/senha). Sem elas, a aplicação roda aberta — use assim
   apenas localmente.
2. **HTTPS** — HTTP Basic sem TLS trafega a senha em claro. Todas as
   opções abaixo já entregam HTTPS.
3. **Nenhum armazenamento** — a aplicação é stateless por design: o PDF
   é processado em memória e nada é gravado em disco. Mantenha assim.

---

## Opção 1 — VPS com Docker + Caddy (recomendada: controle e custo)

Um VPS de 2 vCPU / 4 GB RAM (Hetzner ~€6, DigitalOcean ~US$12) processa
com folga PDFs de 500+ páginas.

```bash
# no servidor (Ubuntu/Debian com Docker instalado)
git clone <seu-repositorio> && cd PdfToMarkDown

docker build -t extrator .
docker run -d --restart unless-stopped \
  -e EXTRATOR_USUARIO=usuario \
  -e EXTRATOR_SENHA='senha-forte' \
  -p 127.0.0.1:8077:8077 \
  --memory=3g \
  extrator
```

HTTPS automático com Caddy (aponte um domínio para o IP do servidor):

```bash
sudo apt install caddy
# /etc/caddy/Caddyfile:
#   extrator.seudominio.com.br {
#       reverse_proxy 127.0.0.1:8077
#       request_body { max_size 200MB }
#   }
sudo systemctl reload caddy
```

## Opção 2 — Render / Railway (mais rápida, zero servidor)

1. Suba o repositório para o GitHub;
2. No [Render](https://render.com) (ou Railway): *New Web Service* →
   conecte o repositório → ele detecta o `Dockerfile` sozinho;
3. Configure as variáveis `EXTRATOR_USUARIO` e `EXTRATOR_SENHA`;
4. Escolha um plano com **pelo menos 2 GB de RAM** (PDFs de 78 MB picos
   de ~1–2 GB durante a extração). O free tier não aguenta processos
   grandes e hiberna.

HTTPS e domínio `*.onrender.com` saem automáticos.

## Opção 2b — Hugging Face Spaces (gratuito)

Único serviço gratuito com RAM suficiente para processos grandes. Os
documentos passam por servidores da HF (EUA) — use só se isso for
aceitável. O SDK Docker exige cartão; o gratuito é o SDK **Gradio** em
hardware ZeroGPU (o app não usa a GPU, só a CPU).

O repositório já está pronto para ser publicado como está: `space.py`
(ponto de entrada, `app_file` nos metadados do `README.md`) serve o
FastAPI na porta 7860 com um Gradio mínimo montado em `/gradio`
(exigência do SDK).

1. *New Space* → SDK Gradio, template Blank, ZeroGPU, **Private**;
2. Envie o repositório inteiro para o Space (o histórico do Space é
   outro, por isso o `--force` na primeira vez):
   ```bash
   git remote add hf https://huggingface.co/spaces/Comarca/extrator-pdf-juridico
   git push --force hf main
   ```
   (usuário: seu login na HF; senha: um *access token* com permissão
   de escrita, criado em *Settings → Access Tokens*);
3. Atualizações: basta `git push hf main` de novo.

Com o Space privado, só a sua conta da HF acessa a página — não é
preciso definir `EXTRATOR_USUARIO`/`EXTRATOR_SENHA`. Defina-as apenas
se tornar o Space público (aí são a única barreira contra uso por
terceiros da sua cota de CPU; documentos não ficam armazenados em
nenhum caso).

Hiberna após 48 h sem uso (demora ~1 min para acordar). Space existente:
<https://huggingface.co/spaces/Comarca/extrator-pdf-juridico>.

## Opção 3 — Rede interna (tribunal/escritório)

Se os usuários estão na mesma rede (fórum, escritório), o mais adequado
para dados sigilosos é **não sair para a internet**: rode o Docker num
servidor interno e acesse por `http://ip-interno:8077` (com as
variáveis de autenticação definidas). Zero exposição externa.

---

## Ajustes de produção

| Parâmetro | Onde | Recomendação |
|-----------|------|--------------|
| Workers | `Dockerfile` (CMD) | ≈ nº de vCPUs (CPU-bound) |
| Tamanho máximo | `app/main.py` (`MAX_BYTES`) | 200 MB (padrão) |
| Memória do contêiner | `docker run --memory` | ≥ 2 GB por worker |

## Teste rápido pós-deploy

```bash
curl -u usuario:senha https://extrator.seudominio.com.br/api/perfis
# sem credenciais deve retornar 401:
curl -o /dev/null -w "%{http_code}" https://extrator.seudominio.com.br/
```
