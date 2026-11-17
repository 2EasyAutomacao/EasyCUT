# EasyCUT

Interface Qt (PySide6) para gerar cortes verticais com duas câmeras, logo e
legendas karaokê. Funciona em **Linux** e **macOS**. A transcrição com
**Whisper local** é opcional.

## Instalação

Pré-requisitos: Python 3.10+ e FFmpeg com libass.

```bash
# macOS
brew install ffmpeg
# Arch: sudo pacman -S ffmpeg · Debian/Ubuntu: sudo apt install ffmpeg
```

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m easycut
```

### Whisper local (opcional)

Instale **um** dos back-ends. O app detecta sozinho o que estiver disponível
(aba **Whisper** → *Disponibilidade*):

| Back-end        | Instalação                     | Indicado para                              |
|-----------------|--------------------------------|--------------------------------------------|
| faster-whisper  | `pip install faster-whisper`   | Linux e Mac (CPU) e GPUs NVIDIA (CUDA)     |
| mlx-whisper     | `pip install mlx-whisper`      | Mac com Apple Silicon (M1/M2/M3/M4…)       |
| whisper.cpp     | `brew install whisper-cpp` + modelo `ggml-*.bin` | Mac/Linux sem Python extra (Metal no Mac) |
| openai-whisper  | `pip install openai-whisper`   | Quem já usa PyTorch                        |

Os modelos (`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`) são
baixados na primeira transcrição e depois rodam offline. Para o whisper.cpp,
aponte o arquivo do modelo ggml na aba Whisper.

A transcrição gera linhas curtas (máx. de palavras/caracteres configurável) com o
**tempo de cada palavra**, usado no efeito karaokê.

## Uso

1. Escolha **Cam 1** (e opcionalmente **Cam 2**) e a pasta de **Saída**.
2. Crie os cortes na tabela (ou **Importar CSV** — formato antigo `id;start;end`,
   e `legendas.csv` com `id;start;end;text`).
3. Gere as legendas com **🎙 Transcrever** ou digite/importe.
4. Ajuste na coluna da direita:
   * **Formato** — proporção do vídeo (9:16, 4:5, 1:1, 3:4, 16:9 ou personalizada),
     layout (empilhado, lado a lado, câmera única), divisão entre câmeras e áudio.
   * **Legendas** — posição (X/Y em %, âncora, atalhos Topo/Centro/Divisão/Base),
     tamanho, largura máxima, fonte, cores, contorno, sombra e karaokê.
   * **Logo** — imagem, largura, opacidade e posição.
5. Na prévia dá para **arrastar** a legenda, o logo e a divisória entre as câmeras.
   **Prévia real (F5)** renderiza o quadro atual com o próprio FFmpeg.
6. **Renderizar corte** (Ctrl+R) ou **Renderizar todos** (Ctrl+Shift+R).

Projetos são salvos como `.easycut` (JSON). Caminhos do FFmpeg e configurações
do Whisper ficam nas preferências da máquina.

## Estrutura

```
easycut/
  core/       # sem Qt: modelos, geração do .ass, comandos ffmpeg, Whisper, CSV
  ui/         # janela, prévia interativa, painéis e tarefas em segundo plano
Assets/       # logo e fonte padrão (Bazinga)
easycut/EasyCutCLI.py    # script antigo de linha de comando (Windows), mantido como referência
```
