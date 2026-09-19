"""Unit tests for remote LLM node model discovery and agent synchronization."""
import json
import unittest
from unittest.mock import patch, MagicMock
from src.node_models import RemoteModel, query_node_models, discover_cluster_models
from src.agents.qwen_code.adapter import QwenCodeAdapter
from src.profiles_schema import ModelProfile


class TestNodeModels(unittest.TestCase):
    def test_query_node_models_success(self):
        node = {
            "id": "test-node",
            "name": "Test Node (A5000)",
            "url": "http://192.168.1.100:9292",
            "type": "llama_swap",
            "enabled": True,
        }

        mock_response_data = {
            "data": [
                {"id": "qwen3.8-27b-production", "owned_by": "llamacpp"},
                {"id": "qwen3.8-27b-long", "owned_by": "llamacpp"},
            ]
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response):
            models = query_node_models(node)

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].id, "qwen3.8-27b-production")
        self.assertEqual(models[0].endpoint, "http://192.168.1.100:9292/v1")
        self.assertEqual(models[0].node_id, "test-node")
        self.assertEqual(models[0].node_name, "Test Node (A5000)")
        self.assertEqual(models[1].id, "qwen3.8-27b-long")

    def test_query_node_models_list_format(self):
        node = {
            "id": "test-server",
            "url": "http://192.168.1.101:8080/v1",
            "type": "llama_server",
        }
        mock_data = [
            {"id": "custom-model-1"},
            {"id": "custom-model-2"},
        ]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response):
            models = query_node_models(node)

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].id, "custom-model-1")
        self.assertEqual(models[0].endpoint, "http://192.168.1.101:8080/v1")

    def test_query_node_models_error_handling(self):
        node = {
            "id": "offline-node",
            "url": "http://192.168.99.99:9999",
            "type": "llama_server",
        }
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            models = query_node_models(node)
        self.assertEqual(models, [])

    def test_discover_cluster_models_skips_comfyui(self):
        mock_cm = MagicMock()
        mock_cm.get_nodes.return_value = [
            {"id": "comfy-1", "url": "http://127.0.0.1:8188", "type": "comfyui", "enabled": True},
            {"id": "llm-1", "url": "http://192.168.1.100:9292", "type": "llama_swap", "enabled": True},
            {"id": "disabled-1", "url": "http://192.168.1.101:8080", "type": "llama_server", "enabled": False},
        ]

        def mock_query(node):
            if node["id"] == "llm-1":
                return [RemoteModel(id="model-a", name="model-a", endpoint="http://192.168.1.100:9292/v1",
                                    node_id="llm-1", node_name="LLM Node 1")]
            return []

        with patch("src.node_models.query_node_models", side_effect=mock_query) as m_query:
            models = discover_cluster_models(mock_cm)

        self.assertEqual(m_query.call_count, 1)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].id, "model-a")

    def test_qwen_adapter_includes_cluster_models(self):
        adapter = QwenCodeAdapter()
        adapter.schema_confirmed = True

        local_model = ModelProfile(
            id="local-1",
            name="Local Model 1",
            weights_path="C:/models/local.gguf",
            endpoint="http://127.0.0.1:9292/v1",
            status="production",
            qualified=True,
        )
        remote_model = RemoteModel(
            id="remote-1",
            name="Remote Qwen",
            endpoint="http://192.168.1.100:9292/v1",
            node_id="ai-station",
            node_name="AI Station",
            context=131072,
        )

        with patch.object(adapter, "get_config_locations", return_value=MagicMock(data={"user": "C:/fake/settings.json"})):
            with patch("src.agents.qwen_code.adapter.preview_merge") as mock_merge:
                mock_merge.return_value = MagicMock()
                res = adapter.configure_model_provider([local_model], cluster_models=[remote_model])
                self.assertTrue(res.ok)

                call_args = mock_merge.call_args[0]
                changes = call_args[1]
                providers_entry = next(val for key, val in changes if key == ["modelProviders", "local-agent-station"])
                self.assertEqual(len(providers_entry), 2)
                self.assertEqual(providers_entry[0]["id"], "local-1")
                self.assertEqual(providers_entry[0]["baseUrl"], "http://127.0.0.1:9292/v1")
                self.assertEqual(providers_entry[1]["id"], "remote-1")
                self.assertEqual(providers_entry[1]["baseUrl"], "http://192.168.1.100:9292/v1")
                self.assertIn("AI Station", providers_entry[1]["name"])


if __name__ == "__main__":
    unittest.main()
