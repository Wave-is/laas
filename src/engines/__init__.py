"""
engines factory
"""
from .llama_swap import LlamaSwapEngine
from .ollama import OllamaEngine
from ..config import config

def get_active_engine():
    engine_type = config.get("active_engine", "llama_swap")
    if engine_type == "ollama":
        return OllamaEngine()
    return LlamaSwapEngine()
