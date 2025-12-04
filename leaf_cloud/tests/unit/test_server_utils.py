"""
Unit tests for server_utils.py
"""
import json
from unittest.mock import patch, MagicMock
import pytest
from flask import Flask, jsonify, request

from leaf_cloud.server_utils import (
    ErrorCode,
    ApiResponse,
    api_response,
    handle_errors,
    get_required_param,
    get_optional_param,
    validate_json_schema,
    paginate,
    ValidationError
)

# Test application setup
@pytest.fixture
def app():
    """Create a test Flask application."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app

# Test client for making requests
@pytest.fixture
def client(app):
    """Create a test client for the Flask app."""
    with app.test_client() as client:
        with app.app_context():
            yield client

class TestErrorCode:
    """Test the ErrorCode enum."""
    
    def test_error_code_values(self):
        """Test that error codes have the expected string values."""
        assert ErrorCode.INVALID_INPUT.value == "invalid_input"
        assert ErrorCode.NOT_FOUND.value == "not_found"
        assert ErrorCode.SERVER_ERROR.value == "server_error"
        assert ErrorCode.VALIDATION_ERROR.value == "validation_error"
        assert ErrorCode.UNAUTHORIZED.value == "unauthorized"
        assert ErrorCode.FORBIDDEN.value == "forbidden"
        assert ErrorCode.CONFLICT.value == "conflict"
        assert ErrorCode.NOT_IMPLEMENTED.value == "not_implemented"
        assert ErrorCode.SERVICE_UNAVAILABLE.value == "service_unavailable"
        assert ErrorCode.TIMEOUT.value == "timeout"

class TestApiResponse:
    """Test the ApiResponse class."""
    
    def test_api_response_creation(self):
        """Test creating an ApiResponse with all fields."""
        response = ApiResponse(
            success=True,
            data={"key": "value"},
            error={"code": "error_code", "message": "Error message"},
            meta={"count": 1},
            warnings=[{"code": "warning_code", "message": "Warning message"}]
        )
        
        assert response.success is True
        assert response.data == {"key": "value"}
        assert response.error == {"code": "error_code", "message": "Error message"}
        assert response.meta == {"count": 1}
        assert response.warnings == [{"code": "warning_code", "message": "Warning message"}]
    
    def test_to_dict_removes_none_values(self):
        """Test that to_dict() removes None values."""
        response = ApiResponse(success=True, data=None, error=None)
        result = response.to_dict()
        assert "data" not in result
        assert "error" not in result
        assert result["success"] is True

class TestApiResponseFunction:
    """Test the api_response function."""
    
    def test_success_response(self, app):
        """Test creating a successful API response."""
        with app.app_context():
            response, status_code = api_response(
                success=True,
                data={"id": 123},
                meta={"count": 1}
            )
            
            # Convert response to dict for assertions
            response_data = response.get_json()
            
            assert status_code == 200
            assert response_data["success"] is True
            assert response_data["data"] == {"id": 123}
            assert response_data["meta"] == {"count": 1}
            assert "error" not in response_data
    
    def test_error_response(self, app):
        """Test creating an error API response."""
        with app.app_context():
            response, status_code = api_response(
                success=False,
                error={"code": "not_found", "message": "Resource not found"},
                status_code=404
            )
            
            # Convert response to dict for assertions
            response_data = response.get_json()
            
            assert status_code == 404
            assert response_data["success"] is False
            assert response_data["error"] == {"code": "not_found", "message": "Resource not found"}
            assert "data" not in response_data

class TestHandleErrorsDecorator:
    """Test the handle_errors decorator."""
    
    def test_handle_validation_error(self, app):
        """Test handling a ValidationError."""
        @app.route('/test')
        @handle_errors
        def test_route():
            raise ValidationError("Invalid input")
            
        with app.test_client() as client:
            response = client.get('/test')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert data["success"] is False
            assert data["error"]["code"] == "validation_error"
    
    def test_handle_leaf_cloud_error(self, app):
        """Test handling a LeafCloudError."""
        from leaf_cloud.exceptions import LeafCloudError
        
        # Create a custom error class that sets status_code
        class CustomError(LeafCloudError):
            status_code = 400
            
            def __init__(self, message, code=None):
                super().__init__(message)
                self.code = code
        
        @app.route('/test')
        @handle_errors
        def test_route():
            raise CustomError("Something went wrong", code="custom_error")
            
        with app.test_client() as client:
            response = client.get('/test')
            assert response.status_code == 400  # Status code from CustomError
            data = json.loads(response.data)
            assert data["success"] is False
            assert data["error"]["code"] == "custom_error"
    
    def test_handle_unexpected_error(self, app):
        """Test handling an unexpected error."""
        @app.route('/test')
        @handle_errors
        def test_route():
            raise ValueError("Unexpected error")
            
        with app.test_client() as client:
            response = client.get('/test')
            assert response.status_code == 500
            data = json.loads(response.data)
            assert data["success"] is False
            assert data["error"]["code"] == "server_error"

class TestGetRequiredParam:
    """Test the get_required_param function."""
    
    def test_get_required_param_success(self, app):
        """Test successfully getting a required parameter."""
        with app.test_request_context(json={"id": 123, "name": "Test"}):
            # Test getting a string parameter
            name = get_required_param("name")
            assert name == "Test"
            
            # Test getting an integer parameter with validation
            user_id = get_required_param("id", int, min_value=1)
            assert user_id == 123
    
    def test_get_required_param_missing(self, app):
        """Test getting a missing required parameter."""
        with app.test_request_context(json={"name": "Test"}):
            with pytest.raises(ValidationError) as excinfo:
                get_required_param("id")
            assert "Missing required parameter: id" in str(excinfo.value)
    
    def test_get_required_param_validation(self, app):
        """Test validation of required parameters."""
        with app.test_request_context(json={"age": -5}):
            with pytest.raises(ValidationError) as excinfo:
                get_required_param("age", int, min_value=0, max_value=120)
            assert "must be >=" in str(excinfo.value) or "must be at least" in str(excinfo.value)

class TestGetOptionalParam:
    """Test the get_optional_param function."""
    
    def test_get_optional_param_success(self, app):
        """Test successfully getting an optional parameter."""
        with app.test_request_context(json={"name": "Test"}):
            # Test getting a provided parameter
            name = get_optional_param("name", default="Default")
            assert name == "Test"
            
            # Test getting a missing parameter with default
            age = get_optional_param("age", default=30, param_type=int, min_value=0)
            assert age == 30
    
    def test_get_optional_param_validation(self, app):
        """Test validation of optional parameters."""
        with app.test_request_context(json={"age": -5}):
            with pytest.raises(ValidationError) as excinfo:
                # Don't provide a default value when we expect a validation error
                get_optional_param("age", param_type=int, min_value=0)
            assert "must be >= 0" in str(excinfo.value) or "must be at least 0" in str(excinfo.value)

class TestValidateJsonSchema:
    """Test the validate_json_schema function."""
    
    def test_validate_json_schema_success(self, app):
        """Test successful schema validation."""
        with app.app_context():
            schema = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "number", "minimum": 0}
                },
                "required": ["name"]
            }
            
            data = {"name": "John", "age": 30}
            # Should not raise an exception
            validate_json_schema(schema, data)
    
    def test_validate_json_schema_failure(self, app):
        """Test failed schema validation."""
        with app.app_context():
            schema = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "number", "minimum": 0}
                },
                "required": ["name"]
            }
            
            # Missing required field
            data = {"age": 30}
            with pytest.raises(ValueError) as excinfo:
                validate_json_schema(schema, data)
            assert "'name' is a required property" in str(excinfo.value)

class TestPaginate:
    """Test the paginate function."""
    
    def test_paginate_first_page(self):
        """Test pagination of the first page."""
        items = list(range(1, 26))  # 1-25
        result = paginate(items, page=1, per_page=10)
        
        assert result["items"] == list(range(1, 11))
        assert result["total"] == 25
        assert result["page"] == 1
        assert result["per_page"] == 10
        assert result["total_pages"] == 3
    
    def test_paginate_last_page(self):
        """Test pagination of the last page with partial results."""
        items = list(range(1, 26))  # 1-25
        result = paginate(items, page=3, per_page=10)
        
        assert result["items"] == list(range(21, 26))
        assert result["total"] == 25
        assert result["page"] == 3
        assert result["per_page"] == 10
        assert result["total_pages"] == 3
    
    def test_paginate_empty_list(self):
        """Test pagination of an empty list."""
        result = paginate([], page=1, per_page=10)
        
        assert result["items"] == []
        assert result["total"] == 0
        assert result["page"] == 1
        assert result["per_page"] == 10
        assert result["total_pages"] == 0
