import unittest

from news_harness.connectors.base import (
    SourceConnector,
    ProcessingConnector,
    ConnectorError,
)
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.models import ContentItem, ProcessedContent


class _DummySource(SourceConnector):
    platform = "dummy"
    source_label = "Dummy"

    def fetch(self):
        return []


class _DummyProc(ProcessingConnector):
    platform = "dummy"

    def process(self, item):
        return ProcessedContent(item_id=item.id, processing_status="raw")


class TestBaseConnector(unittest.TestCase):
    def test_source_is_abstract(self):
        with self.assertRaises(TypeError):
            SourceConnector()

    def test_processor_is_abstract(self):
        with self.assertRaises(TypeError):
            ProcessingConnector()

    def test_connector_error(self):
        with self.assertRaises(ConnectorError):
            raise ConnectorError("boom")


class TestConnectorRegistry(unittest.TestCase):
    def test_register_and_get_source(self):
        reg = ConnectorRegistry()
        reg.register(_DummySource)
        self.assertIs(reg.get_source("dummy"), _DummySource)
        self.assertIsNone(reg.get_source("missing"))

    def test_register_and_get_processor(self):
        reg = ConnectorRegistry()
        reg.register(_DummyProc)
        self.assertIs(reg.get_processor("dummy"), _DummyProc)
        self.assertIsNone(reg.get_processor("missing"))

    def test_list_sources_and_processors(self):
        reg = ConnectorRegistry()
        reg.register(_DummySource)
        reg.register(_DummyProc)
        # 注册表为全局单例（connector 在 import 时已自动登记），用成员判定而非精确列表
        self.assertIn("dummy", reg.list_sources())
        self.assertIn("dummy", reg.list_processors())

    def test_register_idempotent(self):
        reg = ConnectorRegistry()
        reg.register(_DummySource)
        reg.register(_DummySource)
        self.assertEqual(reg.list_sources().count("dummy"), 1)


if __name__ == "__main__":
    unittest.main()
