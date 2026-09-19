"""
Knowledge Distributor for LAAS.
Automatically generates and distributes the 'comfyui-image-gen' skill and
synchronizes model catalogs (local profiles + cluster nodes) to all
locally installed AI agent runtimes (Qwen Code Desktop, Antigravity/AGY, OpenClaw, Hermes).
"""
import json
import logging
import os
from pathlib import Path
import time
from typing import Dict, List, Optional

from .config import config
from .i18n import tr

log = logging.getLogger(__name__)

SKILL_NAME = "comfyui-image-gen"

GENERATE_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""
ComfyUI Image Generation CLI Helper for AI Agents.
Submits workflow to cluster ComfyUI node and downloads resulting image.
Zero dependencies (standard library only).
"""
import argparse
import json
import os
from pathlib import Path
import random
import sys
import time
import urllib.error
import urllib.request


def load_config():
    cfg_file = Path(__file__).resolve().parent.parent / "config.json"
    if cfg_file.is_file():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"server_url": "http://127.0.0.1:8188", "default_model": "z-image-turbo-Q8_0.gguf"}


def build_workflow(prompt: str, negative: str, width: int, height: int, seed: int, model: str, steps: int = 8):
    return {
        "16": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": model}
        },
        "18": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen_3_4b_fp8_mixed.safetensors",
                "type": "lumina2",
                "device": "default"
            }
        },
        "17": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "ae.safetensors"}
        },
        "13": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1}
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["18", 0]}
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["18", 0]}
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["16", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["13", 0]
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["17", 0]}
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "AgentGen", "images": ["8", 0]}
        }
    }


def main():
    cfg = load_config()
    default_server = cfg.get("server_url", "http://127.0.0.1:8188")
    default_model = cfg.get("default_model", "z-image-turbo-Q8_0.gguf")

    parser = argparse.ArgumentParser(description="Generate image via cluster ComfyUI")
    parser.add_argument("--prompt", "-p", required=True, help="Visual text prompt (English recommended)")
    parser.add_argument("--negative", "-n", default="blurry, low quality, distorted, bad anatomy, watermark", help="Negative prompt")
    parser.add_argument("--width", type=int, default=1024, help="Width in pixels (multiple of 16)")
    parser.add_argument("--height", type=int, default=1024, help="Height in pixels (multiple of 16)")
    parser.add_argument("--steps", type=int, default=8, help="Sampling steps")
    parser.add_argument("--seed", type=int, default=None, help="Generation seed")
    parser.add_argument("--model", default=default_model, help="GGUF UNET model name")
    parser.add_argument("--server-url", default=default_server, help="ComfyUI server URL")
    parser.add_argument("--output-dir", default=None, help="Directory to save the image")
    parser.add_argument("--output-name", default=None, help="Target image filename (e.g. logo.png)")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")

    args = parser.parse_args()

    server = args.server_url.rstrip("/")
    seed = args.seed if args.seed is not None else random.randint(1, 2**32 - 1)
    width = max(256, (args.width // 16) * 16)
    height = max(256, (args.height // 16) * 16)

    t0 = time.time()
    workflow = build_workflow(args.prompt, args.negative, width, height, seed, args.model, args.steps)

    # 1. Submit prompt to ComfyUI
    prompt_req = urllib.request.Request(
        f"{server}/prompt",
        data=json.dumps({"prompt": workflow}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "AgentSkill-ComfyUI"}
    )
    try:
        with urllib.request.urlopen(prompt_req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            prompt_id = resp_data.get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI rejected prompt: {resp_data}")
    except Exception as ex:
        print(json.dumps({"success": False, "error": f"Failed to connect to ComfyUI at {server}: {ex}"}))
        sys.exit(1)

    # 2. Poll /history/{prompt_id} until completed
    deadline = time.time() + args.timeout
    output_filename = None
    output_subfolder = ""

    while time.time() < deadline:
        time.sleep(0.8)
        try:
            h_req = urllib.request.Request(f"{server}/history/{prompt_id}", headers={"User-Agent": "AgentSkill-ComfyUI"})
            with urllib.request.urlopen(h_req, timeout=10) as h_resp:
                h_data = json.loads(h_resp.read().decode("utf-8"))
                if prompt_id in h_data:
                    info = h_data[prompt_id]
                    if "status" in info and info["status"].get("status_str") == "error":
                        err_msg = info["status"].get("messages", "ComfyUI generation error")
                        print(json.dumps({"success": False, "error": str(err_msg)}))
                        sys.exit(1)
                    outputs = info.get("outputs", {})
                    if "9" in outputs and outputs["9"].get("images"):
                        img_info = outputs["9"]["images"][0]
                        output_filename = img_info.get("filename")
                        output_subfolder = img_info.get("subfolder", "")
                        break
        except Exception:
            pass

    if not output_filename:
        print(json.dumps({"success": False, "error": f"Generation timed out after {args.timeout}s"}))
        sys.exit(1)

    # 3. Download the generated image
    view_url = f"{server}/view?filename={output_filename}&type=output"
    if output_subfolder:
        view_url += f"&subfolder={output_subfolder}"

    dest_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_name = args.output_name or f"generated_{int(time.time())}.png"
    if not dest_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        dest_name += ".png"

    dest_path = dest_dir / dest_name

    try:
        with urllib.request.urlopen(view_url, timeout=20) as img_resp:
            with open(dest_path, "wb") as f_out:
                f_out.write(img_resp.read())
    except Exception as ex:
        print(json.dumps({"success": False, "error": f"Failed to download image from {view_url}: {ex}"}))
        sys.exit(1)

    result = {
        "success": True,
        "file_path": str(dest_path.resolve()),
        "filename": dest_name,
        "prompt": args.prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "elapsed_seconds": round(time.time() - t0, 1),
        "server_url": server
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
'''


SKILL_MD_TEMPLATE = """---
name: {skill_name}
description: >-
  Generate images, illustrations, logos, concept art, UI assets, and graphics using the
  cluster's dedicated ComfyUI worker (RTX 3060 12GB on RenderPC at {server_url}).
  Use whenever the user asks to generate, draw, render, create an image, illustration,
  logo, banner, icon, or artwork. Run via: python "{script_path}" --prompt "<prompt>"
---

# ComfyUI Image Generation Worker

This skill connects directly to the cluster's high-speed local image generation worker
running on **RenderPC (NVIDIA GeForce RTX 3060 12GB)** via ComfyUI.

- **Endpoint:** `{server_url}`
- **Default Architecture:** Z-Image-Turbo (Lumina2 + Qwen 3.4B Text Encoder) & FLUX.2 Klein
- **Speed:** ~4–12 seconds per 1024×1024 image.

---

## When to Use

- User asks to draw, generate, or design an image, logo, background, banner, avatar, or icon.
- Creating visual mockups or assets for web or desktop applications.
- Prototyping visual designs.

---

## How to Call

Run the bundled CLI helper from your command execution tool:

```bash
python "{script_path}" --prompt "<detailed visual prompt in English>" --output-dir "<target_dir>"
```

### Options:
- `--prompt` (required): Detailed description of the visual scene.
- `--output-dir`: Folder to save the resulting image (e.g. `./assets` or project folder).
- `--output-name`: Optional custom filename (e.g. `hero_banner.png`).
- `--width` / `--height`: Resolution in pixels (default: `1024 1024`).
- `--negative`: Optional negative prompt (defaults to standard quality filters).
- `--seed`: Optional integer seed for reproducible generation.

---

## Best Practices for Prompting

1. **Language:** Always translate Russian or Ukrainian user requests into detailed, descriptive English for the diffusion model.
2. **Detail Level:** Specify subject, setting, art style, lighting, color palette, and composition.
   - *Example:* `A clean minimalist vector logo of an eagle head, blue and cyan neon gradients, dark background, modern tech aesthetic, 8k`
   - *Example:* `Cinematic photo of a modern cozy office with high-tech workstations, warm sunset window light, shallow depth of field, 8k, photorealistic`
3. **Common Aspect Ratios:**
   - Square (1:1): `--width 1024 --height 1024` (logos, icons, avatars)
   - Landscape (16:9): `--width 1280 --height 768` (banners, desktop wallpapers, web headers)
   - Portrait (9:16): `--width 768 --height 1280` (mobile screens, character posters)
"""


class KnowledgeDistributor:
    def __init__(self):
        pass

    def detect_agent_skills_dirs(self) -> Dict[str, Path]:
        """Finds all existing agent root configurations and returns their skills/ directories."""
        home = Path.home()
        candidates = {
            "Qwen Code Desktop / CLI": home / ".qwen" / "skills",
            "Google Antigravity": home / ".gemini" / "config" / "skills",
            "OpenClaw": home / ".openclaw" / "skills",
            "Hermes": home / ".hermes" / "skills",
        }
        detected = {}
        for agent_name, skills_path in candidates.items():
            parent = skills_path.parent
            if parent.is_dir() or skills_path.is_dir():
                skills_path.mkdir(parents=True, exist_ok=True)
                detected[agent_name] = skills_path
        return detected

    def get_comfy_endpoint(self) -> str:
        """Finds the active ComfyUI endpoint URL from ClusterManager or SharedServices."""
        try:
            from .cluster_manager import cluster_manager
            nodes = cluster_manager.get_nodes()
            for n in nodes:
                if n.get("type") == "comfyui" or ":8188" in n.get("url", ""):
                    url = n.get("url", "").rstrip("/")
                    if url:
                        return url
        except Exception:
            pass

        try:
            from .shared_services import SharedServices
            profs = SharedServices().profiles()
            for sp in profs.values():
                if sp.get("kind") == "comfyui" and sp.get("url"):
                    return sp["url"].rstrip("/")
        except Exception:
            pass

        return "http://127.0.0.1:8188"

    def deploy(self, server_url: Optional[str] = None) -> dict:
        """Generates the comfyui-image-gen skill and deploys it to all installed agents."""
        if not server_url:
            server_url = self.get_comfy_endpoint()

        agent_dirs = self.detect_agent_skills_dirs()
        deployed_agents = []
        deployed_paths = []

        config_data = {
            "server_url": server_url,
            "default_model": "z-image-turbo-Q8_0.gguf",
            "updated_at": time.time()
        }

        for agent_name, skills_root in agent_dirs.items():
            skill_dir = skills_root / SKILL_NAME
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)

            script_file = scripts_dir / "generate_image.py"
            with open(script_file, "w", encoding="utf-8") as f:
                f.write(GENERATE_SCRIPT_TEMPLATE)

            cfg_file = skill_dir / "config.json"
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)

            skill_md_content = SKILL_MD_TEMPLATE.format(
                skill_name=SKILL_NAME,
                server_url=server_url,
                script_path=str(script_file.resolve())
            )
            skill_md_file = skill_dir / "SKILL.md"
            with open(skill_md_file, "w", encoding="utf-8") as f:
                f.write(skill_md_content)

            deployed_agents.append(agent_name)
            deployed_paths.append(str(skill_dir))
            log.info("Deployed ComfyUI skill to %s at %s", agent_name, skill_dir)

        return {
            "success": True,
            "server_url": server_url,
            "agents_updated": deployed_agents,
            "paths": deployed_paths
        }

    def sync_models_to_agents(self) -> dict:
        """Discover cluster models and sync the full model catalog to all agents.

        This triggers the same sync pipeline as the Models page 'Sync with agents'
        button, but enriched with models discovered from cluster LLM nodes.
        Returns a summary dict with per-agent results.
        """
        results = {}
        try:
            from .controller import StationController
            from .node_models import discover_cluster_models
            from .cluster_manager import cluster_manager as cm

            cluster_models = discover_cluster_models(cm)
            results['cluster_models_found'] = len(cluster_models)
            log.info("sync_models_to_agents: discovered %d cluster models", len(cluster_models))

            ctrl = StationController()
            ctrl.discover_agents()
            for agent_id in ctrl.adapters:
                try:
                    preview = ctrl.preview_sync(agent_id, bind_model=None)
                    if preview.status != 'IN SYNC':
                        from .agent_sync import apply_preview
                        apply_preview(preview, accept_custom=True)
                        results[agent_id] = 'synced'
                    else:
                        results[agent_id] = 'in_sync'
                except Exception as exc:
                    log.debug("Auto-sync models for agent %s failed: %s", agent_id, exc)
                    results[agent_id] = str(exc)
        except Exception as exc:
            log.warning("sync_models_to_agents: cluster discovery failed: %s", exc)
            results['cluster_models_found'] = 0
        return results


# Backward compatibility alias
SkillDistributor = KnowledgeDistributor

skill_distributor = KnowledgeDistributor()
