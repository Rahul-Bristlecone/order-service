"""
Service URLs configuration for inter-service communication.
Loads from environment variables with sensible defaults for local development.
"""
import os
from urllib.parse import urlparse


def _is_container_runtime():
    return os.path.exists("/.dockerenv") or bool(os.getenv("KUBERNETES_SERVICE_HOST"))


def _normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _resolve_runtime_url(url: str, internal_default: str) -> str:
    """
    Inside containers, localhost/127.0.0.1 points to the order-service container itself.
    If a localhost URL is configured there, fallback to the service DNS name.
    """
    normalized = _normalize_base_url(url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    if _is_container_runtime() and host in {"127.0.0.1", "localhost"}:
        return _normalize_base_url(internal_default)
    return normalized


class ServiceConfig:
    """Centralized configuration for external service URLs."""

    CUSTOMER_INTERNAL_DEFAULT = "http://customer-service:5006"
    COMPANY_INTERNAL_DEFAULT = "http://company-service:5005"
    LOCATION_INTERNAL_DEFAULT = "http://store-service:5002"
    PRODUCT_INTERNAL_DEFAULT = "http://product-service:5003"
    
    @classmethod
    def get_service_urls(cls):
        """Return dictionary of all service URLs for validation."""
        customer_url = os.getenv("CUSTOMER_SERVICE_URL", "http://127.0.0.1:5006")
        company_url = os.getenv("COMPANY_SERVICE_URL", "http://127.0.0.1:5005")
        location_url = os.getenv("LOCATION_SERVICE_URL", "http://127.0.0.1:5002")
        product_url = os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:5003")

        return {
            "customer_service": _resolve_runtime_url(customer_url, cls.CUSTOMER_INTERNAL_DEFAULT),
            "company_service": _resolve_runtime_url(company_url, cls.COMPANY_INTERNAL_DEFAULT),
            "location_service": _resolve_runtime_url(location_url, cls.LOCATION_INTERNAL_DEFAULT),
            "product_service": _resolve_runtime_url(product_url, cls.PRODUCT_INTERNAL_DEFAULT),
        }
    
    @classmethod
    def get_timeout(cls):
        """Return request timeout for service calls."""
        return int(os.getenv("SERVICE_REQUEST_TIMEOUT", "5"))


# Convenience function for importing
def get_service_urls():
    """Get all service URLs as a dictionary."""
    return ServiceConfig.get_service_urls()


def get_service_timeout():
    """Get request timeout value."""
    return ServiceConfig.get_timeout()
