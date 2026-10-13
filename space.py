"""Ponto de entrada do Hugging Face Space (SDK Gradio, ZeroGPU gratuito).

O SDK Docker exige cartão; o Gradio não. O Extrator não usa GPU nem
Gradio, mas o ZeroGPU encerra o container se nenhuma função @spaces.GPU
for registrada — e o registro só é enviado dentro de `Blocks.launch()`.
Por isso um Gradio mínimo é lançado numa porta lateral, e o FastAPI do
Extrator fica na 7860 (a porta que o Space expõe).
Metadados do Space (sdk, app_file...) ficam no topo do README.md.
"""
import os

import gradio as gr
import uvicorn

from app.main import app

try:
    import spaces  # só existe no ambiente ZeroGPU
except ImportError:  # local / outros hosts
    spaces = None

PORTA = int(os.environ.get("GRADIO_SERVER_PORT", 7860))  # ZeroGPU usa PORT=7861 internamente

if spaces is not None:
    @spaces.GPU
    def _placeholder_gpu():
        return None

    with gr.Blocks() as demo:
        gr.Markdown("Extrator PDF Jurídico — a interface está na raiz do Space.")
    demo.launch(prevent_thread_lock=True, quiet=True, ssr_mode=False,
                server_name="127.0.0.1", server_port=PORTA + 10)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORTA)
