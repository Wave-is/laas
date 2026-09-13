"""
base.py
Abstract base interface for LLM Inference Engines.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseEngine(ABC):
    @abstractmethod
    def is_online(self) -> bool:
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        pass

    @abstractmethod
    def get_active_model(self) -> Optional[str]:
        pass

    @abstractmethod
    def switch_model(self, model_id: str) -> bool:
        pass

    @abstractmethod
    def unload_models(self) -> bool:
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        pass
