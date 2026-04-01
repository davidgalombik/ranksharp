"""Adapter registry — maps retailer slugs to adapter classes."""
import importlib
import structlog
from scraper.base_adapter import BaseAdapter

log = structlog.get_logger()


class AdapterRegistry:
    """
    Loads and caches adapter classes by retailer slug.
    Adapter class paths are stored in the `retailers.adapter_class` DB column,
    so new adapters can be added without touching this file.
    """

    _cache: dict[str, type[BaseAdapter]] = {}

    @classmethod
    def get(cls, adapter_class_path: str) -> type[BaseAdapter]:
        if adapter_class_path in cls._cache:
            return cls._cache[adapter_class_path]

        module_path, class_name = adapter_class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            klass = getattr(module, class_name)
            cls._cache[adapter_class_path] = klass
            log.info("adapter_loaded", adapter=adapter_class_path)
            return klass
        except (ImportError, AttributeError) as exc:
            log.error("adapter_load_failed", adapter=adapter_class_path, error=str(exc))
            raise

    @classmethod
    def build(cls, retailer_config: dict) -> BaseAdapter:
        """Instantiate the correct adapter for a retailer config dict."""
        klass = cls.get(retailer_config["adapter_class"])
        return klass(retailer_config)
