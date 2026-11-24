# 🐱 Meowtrix AI — Models Directory

Place any **`.gguf`** (GPT-Generated Unified Format) model file in this folder.  
Meowtrix AI will automatically discover all `.gguf` files and let you hot-swap between them seamlessly from the top navigation bar!

---

## 🚀 Recommended GGUF Models

All these models run directly on your CPU without any GPU, Ollama, or Docker required:

| Model | Parameters | Quantization | Size | Best For | HuggingFace Download |
| :--- | :---: | :---: | :---: | :--- | :--- |
| **Llama 3.2 1B Instruct** | 1B | `Q4_K_M` | ~800 MB | Ultra-fast on low-spec laptops | [Download GGUF](https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF) |
| **Llama 3.2 3B Instruct** | 3B | `Q4_K_M` | ~2.0 GB | Fast & intelligent balance | [Download GGUF](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF) |
| **Phi-3.5 Mini Instruct** | 3.8B | `Q4_K_M` | ~2.3 GB | Exceptional reasoning & coding | [Download GGUF](https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF) |
| **Qwen 2.5 3B / 7B Instruct** | 3B / 7B | `Q4_K_M` | ~2.2 GB / ~4.6 GB | Top-tier multilingual & logic | [Download GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF) |
| **Mistral 7B Instruct v0.3** | 7B | `Q4_K_M` | ~4.4 GB | General conversation & writing | [Download GGUF](https://huggingface.co/bartowski/Mistral-7B-Instruct-v0.3-GGUF) |
| **Dolphin 2.9 Llama 3 8B** | 8B | `Q4_K_M` | ~4.9 GB | Deep reasoning & uncensored tasks | [Download GGUF](https://huggingface.co/cognitivecomputations/dolphin-2.9-llama3-8b-gguf) |
| **DeepSeek R1 Distill Qwen 1.5B** | 1.5B | `Q4_K_M` | ~1.1 GB | Lightweight chain-of-thought reasoning | [Download GGUF](https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF) |

---

## 💡 How to Add Models:
1. Download any `.gguf` file from HuggingFace.
2. Move or copy the `.gguf` file into this `models/` directory.
3. In the Meowtrix AI web interface, click the **Model Selector** dropdown at the top, or click **🔄 Refresh**, and select your model!

