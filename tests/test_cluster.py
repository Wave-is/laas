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
        self.assertIn('renderpc-local', ids)
        self.assertIn('ai-station', ids)
        self.assertIn('wavevm', ids)
        self.assertIn('remote-worker', ids)

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
        self.assertIn('renderpc-local', xml_text)
        self.assertIn('remote-worker', xml_text)

        # Import into fresh manager with merge=False
        fresh_cm = ClusterManager()
        count = fresh_cm.import_nodes_xml(xml_text, merge=False)
        self.assertGreaterEqual(count, 4)
        imported_ids = [n['id'] for n in fresh_cm.get_nodes()]
        self.assertIn('renderpc-local', imported_ids)
        self.assertIn('remote-worker', imported_ids)

    def test_import_invalid_xml(self):
        with self.assertRaises(ValueError):
            self.cm.import_nodes_xml("<malformed><unclosed>")
        with self.assertRaises(ValueError):
            self.cm.import_nodes_xml("<wrong-root><nodes></nodes></wrong-root>")

    def test_friend_node_uses_telegraf(self):
        nodes = self.cm.get_nodes()
        friend = next((n for n in nodes if n['id'] == 'remote-worker'), None)
        self.assertIsNotNone(friend)
        self.assertIn(':9273', friend.get('telemetry_url', ''))


if __name__ == '__main__':
    unittest.main()

