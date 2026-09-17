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


if __name__ == '__main__':
    unittest.main()
