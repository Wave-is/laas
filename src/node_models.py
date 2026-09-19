"""
Remote LLM node model discovery for LAAS.
Queries /v1/models endpoints on cluster LLM nodes to discover available models.
"""
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)

QUERY_TIMEOUT = 3.0


@dataclass
class RemoteModel:
    """A model discovered on a remote cluster node."""
    id: str
    name: str
    endpoint: str           # e.g. http://192.168.1.100:9292/v1
    node_id: str
    node_name: str
    context: int = 65536
    owned_by: str = ""
    vision: bool = False
    tool_calling: bool = False


def _normalize_endpoint(url: str) -> str:
    """Ensure endpoint ends with /v1."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def query_node_models(node: dict) -> List[RemoteModel]:
    """Query /v1/models on a single LLM node and return discovered models."""
    url = (node.get("url") or "").rstrip("/")
    if not url:
        return []

    endpoint = _normalize_endpoint(url)
    models_url = endpoint + "/models"

    try:
        req = urllib.request.Request(models_url, headers={
            "User-Agent": "laas-cluster",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.debug("Cannot query models from %s (%s): %s", node.get("id"), models_url, exc)
        return []

    # OpenAI-compatible format: {"data": [{"id": "model-name", "owned_by": ...}, ...]}
    raw_models = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(raw_models, list):
        log.debug("Unexpected /v1/models response from %s: %s", node.get("id"), type(raw_models))
        return []

    result = []
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id") or entry.get("name", "")
        if not model_id:
            continue
        result.append(RemoteModel(
            id=model_id,
            name=model_id,
            endpoint=endpoint,
            node_id=node.get("id", ""),
            node_name=node.get("name", ""),
            owned_by=entry.get("owned_by", ""),
        ))
    return result


def discover_cluster_models(cm) -> List[RemoteModel]:
    """Discover models from all enabled LLM nodes in the cluster.

    Args:
        cm: ClusterManager instance (or any object with get_nodes() -> list[dict]).

    Returns:
        Deduplicated list of RemoteModel objects from all reachable nodes.
    """
    nodes = cm.get_nodes()
    all_models = []
    seen = set()

    for node in nodes:
        if not node.get("enabled", True):
            continue
        ntype = node.get("type", "llama_server")
        # Skip non-LLM nodes (ComfyUI is an image generation service)
        if ntype == "comfyui" or ":8188" in node.get("url", ""):
            continue

        models = query_node_models(node)
        for m in models:
            key = (m.id, m.endpoint)
            if key not in seen:
                seen.add(key)
                all_models.append(m)

    log.info("Discovered %d models from %d cluster LLM nodes",
             len(all_models), sum(1 for n in nodes if n.get("enabled", True)
                                  and n.get("type", "llama_server") != "comfyui"
                                  and ":8188" not in n.get("url", "")))
    return all_models
