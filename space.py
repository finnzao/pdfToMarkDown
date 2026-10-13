"""Ponto de entrada do Hugging Face Space (SDK Gradio, ZeroGPU gratuito).

O SDK Docker exige cartão; o Gradio não. Este arquivo monta um Gradio
mínimo (exigência do SDK) e serve o FastAPI do Extrator na porta 7860.
Metadados do Space (sdk, app_file...) ficam no topo do README.md.
"""
import os

import gradio as gr
import uvicorn

from app.main import app

with gr.Blocks() as demo:
    gr.Markdown("Extrator PDF Jurídico — a interface está em [/](/).")

app = gr.mount_gradio_app(app, demo, path="/gradio")

if __name__ == "__main__":
    # ZeroGPU define PORT=7861 para uso interno; o app deve ouvir em 7860
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("GRADIO_SERVER_PORT", 7860)))
