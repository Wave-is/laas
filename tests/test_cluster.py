"""Tests for LLM Cluster Manager and UI integration."""
import unittest
from src.cluster_manager import ClusterManager, DEFAULT_NODES
from src.ui.control_center import PAGE_IDS, page_title, ControlCenter
from src.i18n import catalog


class TestCluster(unittest.TestCase):
    def setUp(self):
        self.cm = ClusterManager()

    def test_page_ids_and_titles(self):
        self.assertIn('cluster', PAGE_IDS)
        self.assertEqual(page_title('cluster'), 'LLM-кластер')
        self.assertEqual(catalog('uk').get('LLM-кластер'), 'LLM-кластер')
        self.assertEqual(catalog('en').get('LLM-кластер'), 'LLM Cluster')

    def test_default_nodes(self):
        nodes = self.cm.get_nodes()
        self.assertGreaterEqual(len(nodes), 4)
        ids = [n['id'] for n in nodes]
        self.assertIn('comfyui-local', ids)
        self.assertIn('primary-node', ids)

    def test_add_remove_node(self):
        initial_len = len(self.cm.get_nodes())
        new_node = {
            'name': 'Test Extra Node',
            'url': 'http://127.0.0.1:9999',
            'type': 'llama_server'
        }
        nid = self.cm.add_node(new_node)
        self.assertEqual(len(self.cm.get_nodes()), initial_len + 1)

        # Update node
        new_node['name'] = 'Updated Extra Node'
        ok = self.cm.update_node(nid, new_node)
        self.assertTrue(ok)
        updated = next(n for n in self.cm.get_nodes() if n['id'] == nid)
        self.assertEqual(updated['name'], 'Updated Extra Node')

        # Remove node
        self.cm.remove_node(nid)
        self.assertEqual(len(self.cm.get_nodes()), initial_len)

    def test_snapshot_summary_calculation(self):
        snap = self.cm.get_snapshot()
        self.assertIn('summary', snap)
        self.assertIn('total_nodes', snap['summary'])
        self.assertIn('total_gpus', snap['summary'])
        self.assertIn('total_vram_gb', snap['summary'])
        self.assertIn('active_inferences', snap['summary'])

    def test_control_center_has_cluster_methods(self):
        self.assertTrue(hasattr(ControlCenter, '_build_cluster'))
        self.assertTrue(hasattr(ControlCenter, '_refresh_cluster_ui'))
        self.assertTrue(hasattr(ControlCenter, '_open_cluster_node_dialog'))
        self.assertTrue(hasattr(ControlCenter, '_export_cluster_xml'))
        self.assertTrue(hasattr(ControlCenter, '_import_cluster_xml'))

    def test_export_and_import_xml(self):
        xml_text = self.cm.export_nodes_xml()
        self.assertIn('<laas-cluster version="1.0">', xml_text)
        self.assertIn('<nodes>', xml_text)
        self.assertIn('comfyui-local', xml_text)
        self.assertIn('primary-node', xml_text)

        # Import into fresh manager with merge=False
        fresh_cm = ClusterManager()
        count = fresh_cm.import_nodes_xml(xml_text, merge=False)
        self.assertGreaterEqual(count, 4)
        imported_ids = [n['id'] for n in fresh_cm.get_nodes()]
        self.assertIn('comfyui-local', imported_ids)
        self.assertIn('primary-node', imported_ids)

    def test_import_invalid_xml(self):
        with self.assertRaises(ValueError):
            self.cm.import_nodes_xml("<malformed><unclosed>")
        with self.assertRaises(ValueError):
            self.cm.import_nodes_xml("<wrong-root><nodes></nodes></wrong-root>")

    def test_telegraf_prometheus_parser(self):
        sample_metrics = (
            '# HELP nvidia_smi_utilization_gpu Telegraf collected metric\n'
            '# TYPE nvidia_smi_utilization_gpu untyped\n'
            'nvidia_smi_utilization_gpu{host="RenderPC",index="0",name="NVIDIA GeForce RTX 3060"} 45\n'
            'nvidia_smi_memory_used{host="RenderPC",index="0",name="NVIDIA GeForce RTX 3060"} 2048\n'
            'nvidia_smi_memory_total{host="RenderPC",index="0",name="NVIDIA GeForce RTX 3060"} 12288\n'
            'nvidia_smi_temperature_gpu{host="RenderPC",index="0",name="NVIDIA GeForce RTX 3060"} 52\n'
            'nvidia_smi_power_draw{host="RenderPC",index="0",name="NVIDIA GeForce RTX 3060"} 38.5\n'
            'cpu_usage_active{cpu="cpu0",host="RenderPC"} 12.0\n'
            'cpu_usage_active{cpu="cpu1",host="RenderPC"} 8.0\n'
            'mem_used{host="RenderPC"} 17179869184\n'
            'mem_total{host="RenderPC"} 34359738368\n'
        )
        parsed = self.cm._parse_telegraf_prometheus(sample_metrics)
        self.assertEqual(len(parsed.get('gpus', [])), 1)
        gpu = parsed['gpus'][0]
        self.assertEqual(gpu['name'], 'NVIDIA GeForce RTX 3060')
        self.assertEqual(gpu['util_percent'], 45.0)
        self.assertEqual(gpu['vram_used_gb'], 2.0)
        self.assertEqual(gpu['vram_total_gb'], 12.0)
        self.assertEqual(gpu['temp_c'], 52.0)
        self.assertEqual(gpu['power_w'], 38.5)
        self.assertEqual(parsed['cpu_util'], 10.0)
        self.assertEqual(parsed['ram_used_gb'], 16.0)
        self.assertEqual(parsed['ram_total_gb'], 32.0)

    def test_telemetry_server_generation(self):
        from src.telemetry_server import telemetry_server
        prom_text = telemetry_server.get_prometheus_text()
        self.assertIn('mem_total', prom_text)
        self.assertIn('cpu_usage_active', prom_text)

        t_dict = telemetry_server.get_telemetry_dict()
        self.assertIn('cpu', t_dict)
        self.assertIn('ram', t_dict)
        self.assertIn('gpus', t_dict)

    def test_cluster_i18n_comfyui_keys(self):
        self.assertIn('ComfyUI (Генератор изображений)', catalog('uk'))
        self.assertIn('ComfyUI (Генератор изображений)', catalog('en'))
        self.assertEqual(catalog('uk').get('ComfyUI (Генератор изображений)'), 'ComfyUI (Генератор зображень)')
        self.assertEqual(catalog('en').get('ComfyUI (Генератор изображений)'), 'ComfyUI (Image Generator)')

    def test_xml_export_and_import_services(self):
        from src.shared_services import SharedServices
        ss = SharedServices()
        _, exp = ss.edit_snapshot("comfyui-test")
        ss.save({
            "id": "comfyui-test",
            "name": "ComfyUI Test",
            "kind": "comfyui",
            "type": "local",
            "url": "http://127.0.0.1:8188",
            "health_url": "http://127.0.0.1:8188/system_stats",
            "executable": "python.exe",
            "arguments": ["main.py", "--port", "8188"],
            "monitor_enabled": True
        }, exp)
        xml_text = self.cm.export_nodes_xml()
        self.assertIn('<services>', xml_text)
        self.assertIn('comfyui-test', xml_text)

        # Verify import parses services element without crashing
        fresh_cm = ClusterManager()
        fresh_cm.import_nodes_xml(xml_text, merge=True)

    def test_skill_distributor_deployment(self):
        import tempfile
        from pathlib import Path
        from src.skill_distributor import SkillDistributor

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home = tmp_path / "user_home"
            (mock_home / ".qwen").mkdir(parents=True)
            (mock_home / ".gemini" / "config").mkdir(parents=True)

            dist = SkillDistributor()
            # Monkeypatch Path.home for test
            orig_home = Path.home
            try:
                Path.home = lambda: mock_home
                detected = dist.detect_agent_skills_dirs()
                self.assertIn("Qwen Code Desktop / CLI", detected)
                self.assertIn("Google Antigravity", detected)

                res = dist.deploy("http://127.0.0.1:8188")
                self.assertTrue(res["success"])
                self.assertEqual(len(res["agents_updated"]), 2)

                qwen_skill = mock_home / ".qwen" / "skills" / "comfyui-image-gen"
                self.assertTrue((qwen_skill / "SKILL.md").is_file())
                self.assertTrue((qwen_skill / "scripts" / "generate_image.py").is_file())
                self.assertTrue((qwen_skill / "config.json").is_file())

                skill_content = (qwen_skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("comfyui-image-gen", skill_content)
                self.assertIn("http://127.0.0.1:8188", skill_content)
            finally:
                Path.home = orig_home

    def test_skill_distribution_i18n(self):
        self.assertIn("📢 Рассказать агентам", catalog("uk"))
        self.assertIn("📢 Рассказать агентам", catalog("en"))
        self.assertEqual(catalog("uk").get("📢 Рассказать агентам"), "📢 Оповістити агентів")
        self.assertEqual(catalog("en").get("📢 Рассказать агентам"), "📢 Share with Agents")

    def test_cluster_refresh_mode_config(self):
        from src.config import DEFAULT_SETTINGS
        self.assertIn('cluster_refresh_mode', DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS['cluster_refresh_mode'], 'manual')
        self.assertEqual(DEFAULT_SETTINGS['cluster_poll_interval_sec'], 15.0)

    def test_cluster_sample_async(self):
        import time
        received = []
        def on_done(snap):
            received.append(snap)

        t = self.cm.sample_async(callback=on_done)
        self.assertIsNotNone(t)
        t.join(timeout=5.0)
        self.assertEqual(len(received), 1)
        self.assertIn('summary', received[0])
        self.assertIn('nodes', received[0])

    def test_cluster_refresh_modes_i18n(self):
        keys = [
            "Вручную", "15 сек", "30 сек", "60 сек",
            "Обновление:", "Последнее обновление: {time}", "Опрос..."
        ]
        for k in keys:
            self.assertIn(k, catalog("en"), f"Missing EN translation for {k}")
            self.assertIn(k, catalog("uk"), f"Missing UK translation for {k}")

        self.assertEqual(catalog("en").get("Вручную"), "Manual")
        self.assertEqual(catalog("uk").get("Вручную"), "Вручну")
        self.assertEqual(catalog("en").get("15 сек"), "15 sec")
        self.assertEqual(catalog("uk").get("15 сек"), "15 сек")

    def test_control_center_has_redesigned_cluster_methods(self):
        self.assertTrue(hasattr(ControlCenter, '_trigger_manual_cluster_refresh'))
        self.assertTrue(hasattr(ControlCenter, '_on_cluster_mode_changed'))
        self.assertTrue(hasattr(ControlCenter, '_schedule_cluster_timer'))
        self.assertTrue(hasattr(ControlCenter, '_cluster_auto_tick'))
        self.assertTrue(hasattr(ControlCenter, '_build_node_card'))
        self.assertTrue(hasattr(ControlCenter, '_update_node_card_inplace'))


if __name__ == '__main__':
    unittest.main()


