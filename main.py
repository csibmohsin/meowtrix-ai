import os
import sys
import time
import uuid
import sqlite3
import threading
import webbrowser
import socket
import gc
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from llama_cpp import Llama
import uvicorn

# ---------------------------------------------------------------------------
# Directories & Constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "local_ai.db"
HTML_PATH = BASE_DIR / "index.html"

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

APP_NAME = "Meowtrix AI"
APP_SUBTITLE = "100% Offline • CPU Inference"
AUTHOR = "Mohsin Khawaja"

# ---------------------------------------------------------------------------
# Database Management (SQLite)
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        defaults = {
            "temperature": "0.7",
            "max_tokens": "1024",
            "memory_enabled": "true",
            "context_size": "4096",
            "cpu_threads": str(min(8, os.cpu_count() or 4)),
            "active_model": "",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()


init_db()

# ---------------------------------------------------------------------------
# Multi-GGUF Model Management & Hot-Swapping
# ---------------------------------------------------------------------------
model_lock = threading.Lock()
inference_lock = threading.Lock()
active_abort_signals: Dict[str, threading.Event] = {}

llm: Optional[Llama] = None
active_model_path: Optional[Path] = None
active_model_name: str = ""
CONTEXT_SIZE = 4096
cpu_cores = os.cpu_count() or 4
optimal_threads = min(8, cpu_cores)


def scan_available_models() -> List[Dict[str, Any]]:
    """Discovers all .gguf model files in models/ directory (and root fallback)."""
    found_paths = []
    
    # Primary: models/ folder
    if MODELS_DIR.is_dir():
        found_paths.extend(MODELS_DIR.glob("*.gguf"))
        found_paths.extend(MODELS_DIR.glob("**/*.gguf"))
    
    # Fallback: Root directory
    for root_gguf in BASE_DIR.glob("*.gguf"):
        if root_gguf not in found_paths:
            found_paths.append(root_gguf)

    models_info = []
    seen_names = set()

    for p in sorted(found_paths, key=lambda x: x.name.lower()):
        if p.name in seen_names:
            continue
        seen_names.add(p.name)

        stat = p.stat()
        size_bytes = stat.st_size
        size_gb = f"{size_bytes / (1024 ** 3):.2f} GB"

        # Detect quantization tag from filename
        quant_match = re.search(r'(Q\d_[A-Z0-9_]+|BF16|FP16|IQ\d_[A-Z0-9_]+)', p.name, re.I)
        quant = quant_match.group(1).upper() if quant_match else "GGUF"

        # Human-friendly display title
        display = p.stem.replace("-", " ").replace("_", " ").title()

        models_info.append({
            "filename": p.name,
            "path": str(p),
            "size_bytes": size_bytes,
            "size_gb": size_gb,
            "quantization": quant,
            "display_name": display,
            "is_active": (p.name == active_model_name),
            "modified": stat.st_mtime
        })

    return models_info


def get_current_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def determine_chat_format(filename: str) -> str:
    """Selects optimal chat format based on model family."""
    fn = filename.lower()
    if "llama-3" in fn or "llama3" in fn:
        return "chatml"
    elif "mistral" in fn or "mixtral" in fn:
        return "mistral-instruct"
    elif "qwen" in fn:
        return "chatml"
    elif "phi" in fn:
        return "chatml"
    elif "gemma" in fn:
        return "gemma"
    return "chatml"


def load_model(target_filename: str) -> bool:
    """Thread-safely hot-swaps active GGUF model."""
    global llm, active_model_path, active_model_name

    models = scan_available_models()
    matching = [m for m in models if m["filename"] == target_filename]
    if not matching:
        print(f"[ERROR] Target model not found: {target_filename}")
        return False

    target_info = matching[0]
    target_path = Path(target_info["path"])

    with model_lock:
        with inference_lock:
            # Abort any active generations
            for sig in active_abort_signals.values():
                sig.set()
            active_abort_signals.clear()

            # Release existing model memory
            if llm is not None:
                print(f"[MODEL] Unloading previous model: {active_model_name}...")
                del llm
                llm = None
                gc.collect()

            chat_fmt = determine_chat_format(target_info["filename"])
            print(f"[MODEL] Loading '{target_info['filename']}' ({target_info['size_gb']})...")
            print(f"[MODEL] Config: CPU Threads={optimal_threads}, Context={CONTEXT_SIZE}, Format='{chat_fmt}'")

            try:
                new_llm = Llama(
                    model_path=str(target_path),
                    chat_format=chat_fmt,
                    n_gpu_layers=0,
                    n_ctx=CONTEXT_SIZE,
                    n_threads=optimal_threads,
                    verbose=False,
                )
                llm = new_llm
                active_model_path = target_path
                active_model_name = target_info["filename"]
                set_setting("active_model", active_model_name)
                print(f"✨ [MODEL READY] '{active_model_name}' loaded successfully on CPU!\n")
                return True
            except Exception as e:
                print(f"[ERROR] Failed to load GGUF model: {e}")
                return False


# ---------------------------------------------------------------------------
# Initial Model Selection at Startup
# ---------------------------------------------------------------------------
initial_models = scan_available_models()
if initial_models:
    saved_model = get_current_setting("active_model", "")
    target_to_load = saved_model if any(m["filename"] == saved_model for m in initial_models) else initial_models[0]["filename"]
    load_model(target_to_load)
else:
    print("\n" + "=" * 60)
    print("🐱 [MEOWTRIX AI] Notice: No .gguf models found in 'models/' folder.")
    print("Please place any GGUF model in the 'models/' folder.")
    print("The web interface will automatically allow you to pick and load it!")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# FastAPI Application & Request Schemas
# ---------------------------------------------------------------------------
app = FastAPI(title="Meowtrix AI")

SYSTEM_PROMPT = (
    "You are Meowtrix AI 🐱, a helpful, intelligent local AI assistant engineered by Mohsin Khawaja. "
    "You run 100% locally on CPU without any cloud or third-party dependencies. "
    "Answer user questions directly, thoughtfully, and clearly without self-prefixing or adding redundant labels."
)

PREFIX_REGEX = re.compile(
    r"^(\s*<\|im_start\|>assistant\s*)*(\s*(Meowtrix\s*AI|Meowtrix|Dolphin\s*2\.9\s*Llama\s*3\s*8B|Dolphin|Assistant)\s*:\s*)+",
    re.IGNORECASE,
)

def strip_model_prefix(text: str) -> str:
    return PREFIX_REGEX.sub("", text).strip()


class CreateConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"

class RenameConversationRequest(BaseModel):
    title: str

class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class StopChatRequest(BaseModel):
    conversation_id: str

class ModelSelectRequest(BaseModel):
    model_filename: str

class MemoryCreateRequest(BaseModel):
    content: str
    category: Optional[str] = "general"

class MemoryUpdateRequest(BaseModel):
    content: str
    category: Optional[str] = "general"

class SettingsUpdateRequest(BaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    memory_enabled: Optional[bool] = None


def generate_title_from_prompt(prompt: str) -> str:
    cleaned = prompt.strip().replace("\n", " ")
    if len(cleaned) > 38:
        truncated = cleaned[:38]
        if " " in truncated:
            return truncated.rsplit(" ", 1)[0].strip()
        return truncated
    return cleaned or "New Chat"


# ---------------------------------------------------------------------------
# Frontend Static Route
# ---------------------------------------------------------------------------
@app.get("/")
async def serve_frontend():
    if not HTML_PATH.is_file():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(HTML_PATH)


# ---------------------------------------------------------------------------
# Model Management Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/models")
async def get_models_list():
    models = scan_available_models()
    return {
        "models": models,
        "active_model": active_model_name,
        "models_dir": str(MODELS_DIR),
        "count": len(models),
        "is_loaded": llm is not None
    }


@app.post("/api/models/select")
async def select_model_endpoint(req: ModelSelectRequest):
    success = load_model(req.model_filename)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to load model: {req.model_filename}")
    return {"status": "success", "active_model": active_model_name}


@app.post("/api/models/rescan")
async def rescan_models_endpoint():
    models = scan_available_models()
    # If currently no model loaded and models now exist, load the first one
    global llm
    if llm is None and models:
        load_model(models[0]["filename"])
    return {
        "models": models,
        "active_model": active_model_name,
        "count": len(models),
        "is_loaded": llm is not None
    }


# ---------------------------------------------------------------------------
# Conversation Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/conversations")
async def list_conversations(q: Optional[str] = None):
    with get_db() as conn:
        if q:
            rows = conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                WHERE c.title LIKE ? OR m.content LIKE ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """,
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """
            ).fetchall()

        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": r["message_count"],
            }
            for r in rows
        ]


@app.post("/api/conversations")
async def create_conversation(req: CreateConversationRequest):
    conv_id = str(uuid.uuid4())
    title = (req.title or "New Chat").strip()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (conv_id, title),
        )
        conn.commit()
    return {"id": conv_id, "title": title}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    with get_db() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()

        return {
            "id": conv["id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "messages": [
                {
                    "id": m["id"],
                    "role": m["role"],
                    "content": strip_model_prefix(m["content"]),
                    "created_at": m["created_at"],
                }
                for m in messages
            ],
        }


@app.patch("/api/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameConversationRequest):
    new_title = req.title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    with get_db() as conn:
        res = conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_title, conv_id),
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conn.commit()
    return {"id": conv_id, "title": new_title}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    with get_db() as conn:
        res = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conn.commit()
    return {"status": "deleted", "id": conv_id}


@app.delete("/api/conversations")
async def clear_all_conversations():
    with get_db() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
        conn.commit()
    return {"status": "all_conversations_cleared"}


# ---------------------------------------------------------------------------
# Long-Term Memory System
# ---------------------------------------------------------------------------
@app.get("/api/memories")
async def list_memories():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "category": r["category"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


@app.post("/api/memories")
async def create_memory(req: MemoryCreateRequest):
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty")
    mem_id = str(uuid.uuid4())
    category = req.category.strip() if req.category else "general"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO memories (id, content, category, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (mem_id, content, category),
        )
        conn.commit()
    return {"id": mem_id, "content": content, "category": category}


@app.put("/api/memories/{mem_id}")
async def update_memory(mem_id: str, req: MemoryUpdateRequest):
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty")
    category = req.category.strip() if req.category else "general"
    with get_db() as conn:
        res = conn.execute(
            "UPDATE memories SET content = ?, category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, category, mem_id),
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
    return {"id": mem_id, "content": content, "category": category}


@app.delete("/api/memories/{mem_id}")
async def delete_memory(mem_id: str):
    with get_db() as conn:
        res = conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
    return {"status": "deleted", "id": mem_id}


@app.delete("/api/memories")
async def clear_all_memories():
    with get_db() as conn:
        conn.execute("DELETE FROM memories")
        conn.commit()
    return {"status": "all_memories_cleared"}


# ---------------------------------------------------------------------------
# Settings Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        settings_dict = {r["key"]: r["value"] for r in rows}

        conv_count = conn.execute("SELECT COUNT(*) as count FROM conversations").fetchone()["count"]
        msg_count = conn.execute("SELECT COUNT(*) as count FROM messages").fetchone()["count"]
        mem_count = conn.execute("SELECT COUNT(*) as count FROM memories").fetchone()["count"]

        db_size_kb = 0
        if DB_PATH.is_file():
            db_size_kb = round(DB_PATH.stat().st_size / 1024, 1)

    models = scan_available_models()
    return {
        "model_name": active_model_name or "None",
        "models_count": len(models),
        "inference_engine": "llama-cpp-python (CPU)",
        "context_size": CONTEXT_SIZE,
        "cpu_threads": optimal_threads,
        "temperature": float(settings_dict.get("temperature", 0.7)),
        "max_tokens": int(settings_dict.get("max_tokens", 1024)),
        "memory_enabled": settings_dict.get("memory_enabled", "true").lower() == "true",
        "database_path": str(DB_PATH.relative_to(BASE_DIR) if DB_PATH.is_relative_to(BASE_DIR) else DB_PATH),
        "db_stats": {
            "conversations": conv_count,
            "messages": msg_count,
            "memories": mem_count,
            "db_size_kb": db_size_kb,
        },
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    with get_db() as conn:
        if req.temperature is not None:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('temperature', ?)", (str(req.temperature),))
        if req.max_tokens is not None:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_tokens', ?)", (str(req.max_tokens),))
        if req.memory_enabled is not None:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('memory_enabled', ?)",
                ("true" if req.memory_enabled else "false",),
            )
        conn.commit()
    return {"status": "updated"}


@app.post("/api/settings/reset-all")
async def reset_all_local_data():
    with get_db() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM memories")
        conn.commit()
    return {"status": "all_local_data_reset"}


# ---------------------------------------------------------------------------
# Chat Inference & Streaming with Real Stop Generation
# ---------------------------------------------------------------------------
@app.post("/api/chat/stop")
async def stop_generation(req: StopChatRequest):
    conv_id = req.conversation_id
    if conv_id in active_abort_signals:
        active_abort_signals[conv_id].set()
        print(f"[STOP REQUEST] Abort signal dispatched for conversation: {conv_id}")
        return {"status": "stopping"}
    return {"status": "no_active_generation"}


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    global llm

    if llm is None:
        raise HTTPException(
            status_code=400,
            detail="No GGUF model is currently loaded. Please add a .gguf model to the 'models/' folder and select it."
        )

    user_prompt = request.message.strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    conv_id = request.conversation_id

    # Validate or create conversation
    with get_db() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not conv:
            title = generate_title_from_prompt(user_prompt)
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (conv_id, title),
            )
        else:
            if conv["title"] in ("New Chat", "Untitled"):
                new_title = generate_title_from_prompt(user_prompt)
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_title, conv_id),
                )
            else:
                conn.execute(
                    "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (conv_id,),
                )

        # Persist user message in SQLite
        user_msg_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, 'user', ?, CURRENT_TIMESTAMP)",
            (user_msg_id, conv_id, user_prompt),
        )
        conn.commit()

        # Load recent conversation history (last 16 messages) for multi-turn context
        history_rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()

        # Load active user memories if enabled
        mem_enabled = get_current_setting("memory_enabled", "true").lower() == "true"
        memories_text = ""
        if mem_enabled:
            mem_rows = conn.execute("SELECT content FROM memories ORDER BY created_at ASC").fetchall()
            if mem_rows:
                mem_items = "\n".join(f"- {m['content']}" for m in mem_rows)
                memories_text = (
                    f"\n\n[Persistent User Memories & Preferences]\n{mem_items}\n"
                    f"Incorporate these user preferences and context naturally when answering."
                )

    # Compose system prompt with identity and optional memory context
    effective_system = SYSTEM_PROMPT + memories_text
    system_message = {"role": "system", "content": effective_system}

    # Prepare messages sequence for ChatML
    recent_history = [
        {"role": r["role"], "content": strip_model_prefix(r["content"])}
        for r in history_rows[-16:]
    ]
    messages_for_model = [system_message] + recent_history

    temp = request.temperature if request.temperature is not None else float(get_current_setting("temperature", "0.7"))
    max_toks = request.max_tokens if request.max_tokens is not None else int(get_current_setting("max_tokens", "1024"))

    # Register abort event for Stop Generation
    abort_event = threading.Event()
    active_abort_signals[conv_id] = abort_event

    def stream_generator():
        accumulated_text = ""
        assistant_msg_id = str(uuid.uuid4())

        with inference_lock:
            start_time = time.time()
            print(f"\n[STREAM START] Generation for conversation {conv_id} ({len(messages_for_model)} messages)...")
            token_count = 0

            try:
                stream = llm.create_chat_completion(
                    messages=messages_for_model,
                    max_tokens=max_toks,
                    temperature=temp,
                    stop=["<|im_end|>", "<|im_start|>", "<|end_of_text|>", "<|eot_id|>"],
                    stream=True,
                )

                for chunk in stream:
                    if abort_event.is_set():
                        print(f"[STREAM STOPPED] Generation aborted by user after {token_count} tokens.")
                        break

                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        token_count += 1
                        accumulated_text += delta
                        yield delta

                elapsed = time.time() - start_time
                status_desc = "Stopped" if abort_event.is_set() else "Completed"
                print(f"[STREAM {status_desc.upper()}] {status_desc} in {elapsed:.2f}s ({token_count} tokens streamed).")

            except Exception as e:
                elapsed = time.time() - start_time
                print(f"[STREAM ERROR] Exception after {elapsed:.2f}s: {e}")
                error_msg = f"\n\n⚠️ [Inference Error: {str(e)}]"
                accumulated_text += error_msg
                yield error_msg

            finally:
                cleaned_final = strip_model_prefix(accumulated_text)
                if cleaned_final:
                    try:
                        with get_db() as conn:
                            conn.execute(
                                "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, CURRENT_TIMESTAMP)",
                                (assistant_msg_id, conv_id, cleaned_final),
                            )
                            conn.execute(
                                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (conv_id,),
                            )
                            conn.commit()
                    except Exception as db_err:
                        print(f"[DB ERROR] Failed saving assistant response: {db_err}")

                active_abort_signals.pop(conv_id, None)

    return StreamingResponse(
        stream_generator(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Free Port Finder & Browser Launcher
# ---------------------------------------------------------------------------
def find_free_port(preferred: int = 8000) -> int:
    for port in range(preferred, preferred + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return preferred


def open_browser(port: int):
    time.sleep(1.2)
    url = f"http://127.0.0.1:{port}"
    print(f"Opening {APP_NAME} in browser at {url} ...")
    webbrowser.open(url)


if __name__ == "__main__":
    host = "127.0.0.1"
    port = find_free_port(8000)
    print("\n" + "=" * 65)
    print(f"  ⚡ {APP_NAME} — {APP_SUBTITLE}")
    print(f"  Engineered by {AUTHOR} (@csib.mohsin)")
    print(f"  Web Dashboard: http://{host}:{port}")
    print(f"  Models Folder: {MODELS_DIR}")
    print(f"  Active Model:  {active_model_name or 'None (Drop GGUF in models/)'}")
    print("=" * 65 + "\n")

    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
