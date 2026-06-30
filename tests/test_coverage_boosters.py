from src.order_service.config import services as svc
from src.order_service.main import create_app
from src.order_service.schema.order_schema import UpdateOrderSchema


def test_service_url_resolution_inside_container(monkeypatch):
    monkeypatch.setattr(svc, "_is_container_runtime", lambda: True)
    resolved = svc._resolve_runtime_url("http://localhost:5001/", "http://customer-service:5001")
    assert resolved == "http://customer-service:5001"

    monkeypatch.setenv("SERVICE_REQUEST_TIMEOUT", "9")
    assert svc.get_service_timeout() == 9


def test_update_schema_preload_non_dict_passthrough():
    schema = UpdateOrderSchema()
    # Exercises the non-dict passthrough path in normalize_status_keys.
    assert schema.normalize_status_keys("not-a-dict") == "not-a-dict"


def test_health_endpoint_response():
    app = create_app("sqlite:///:memory:")
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "healthy"}
