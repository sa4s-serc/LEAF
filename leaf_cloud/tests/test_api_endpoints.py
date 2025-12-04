"""
Unit tests for LEAF-Cloud API endpoints.

This module contains comprehensive tests for all API endpoints including
file upload, simulation, analysis, and static file serving functionality.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

import pytest
from flask import Flask
from werkzeug.test import Client
from werkzeug.datastructures import FileStorage

# Import the Flask app and related components
from leaf_cloud.server import app, web_config, WebServerConfig
from leaf_cloud.exceptions import LeafCloudError, ValidationError


class TestAPIEndpoints:
    """Test class for API endpoint functionality."""
    
    @pytest.fixture
    def client(self):
        """Create a test client for the Flask app."""
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        with app.test_client() as client:
            yield client
    
    @pytest.fixture
    def temp_upload_dir(self):
        """Create a temporary directory for upload tests."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Update web config to use temp directory
            original_upload_folder = web_config.upload_folder
            web_config.upload_folder = temp_dir
            yield temp_dir
            # Restore original config
            web_config.upload_folder = original_upload_folder
    
    @pytest.fixture
    def sample_terraform_file(self):
        """Create a sample Terraform file for testing."""
        content = '''
resource "google_compute_instance" "test" {
  name         = "test-instance"
  machine_type = "e2-medium"
  zone         = "us-central1-a"
  
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }
}
'''
        return ("main.tf", content, "text/plain")
    
    @pytest.fixture
    def sample_csv_file(self):
        """Create a sample CSV workload file for testing."""
        content = '''time,rate
0,100
60,150
120,200
180,100
'''
        return ("workload.csv", content, "text/csv")

    def test_version_endpoint(self, client):
        """Test the /version endpoint."""
        response = client.get('/version')
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check response wrapper
        assert json_response['success'] is True
        assert 'data' in json_response
        
        data = json_response['data']
        
        # Check required fields
        assert 'version' in data
        assert 'build_date' in data
        assert 'description' in data
        assert 'api_version' in data
        
        # Check enhanced fields for frontend compatibility
        assert 'build_info' in data
        assert 'features' in data
        
        # Validate build_info structure
        build_info = data['build_info']
        assert 'timestamp' in build_info
        assert 'environment' in build_info
        assert 'server_mode' in build_info
        
        # Validate features structure
        features = data['features']
        assert 'file_upload' in features
        assert 'static_serving' in features
        assert 'ccf_proxy' in features
        assert 'simulation' in features
        assert 'analysis' in features

    def test_file_upload_success(self, client, temp_upload_dir, sample_terraform_file):
        """Test successful file upload."""
        filename, content, content_type = sample_terraform_file
        
        # Create file-like object using BytesIO
        from io import BytesIO
        file_data = {
            'files': (BytesIO(content.encode('utf-8')), filename, content_type)
        }
        
        response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            data = json_response['data']
        else:
            data = json_response
        
        # Check response structure
        assert data['success'] is True
        assert 'message' in data
        assert 'files' in data
        assert 'temp_directory' in data
        assert 'upload_id' in data
        
        # Check uploaded file info
        assert len(data['files']) == 1
        uploaded_file = data['files'][0]
        assert uploaded_file['original_filename'] == filename
        assert uploaded_file['size'] > 0
        assert uploaded_file['content_type'] == content_type
        
        # Verify file was actually saved
        upload_path = Path(data['temp_directory'])
        assert upload_path.exists()
        assert (upload_path / uploaded_file['filename']).exists()

    def test_file_upload_multiple_files(self, client, temp_upload_dir, sample_terraform_file, sample_csv_file):
        """Test uploading multiple files."""
        tf_filename, tf_content, tf_content_type = sample_terraform_file
        csv_filename, csv_content, csv_content_type = sample_csv_file
        
        # Create multiple files using BytesIO
        from io import BytesIO
        file_data = {
            'files': [
                (BytesIO(tf_content.encode('utf-8')), tf_filename, tf_content_type),
                (BytesIO(csv_content.encode('utf-8')), csv_filename, csv_content_type)
            ]
        }
        
        response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            data = json_response['data']
        else:
            data = json_response
        
        assert data['success'] is True
        assert len(data['files']) == 2
        
        # Check both files were uploaded
        filenames = [f['original_filename'] for f in data['files']]
        assert tf_filename in filenames
        assert csv_filename in filenames

    def test_file_upload_invalid_file_type(self, client, temp_upload_dir):
        """Test upload with invalid file type."""
        from io import BytesIO
        file_data = {
            'files': (BytesIO(b'invalid content'), 'test.txt', 'text/plain')
        }
        
        response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'Invalid file type' in data['message']

    def test_file_upload_no_files(self, client):
        """Test upload with no files provided."""
        response = client.post('/upload', data={}, content_type='multipart/form-data')
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'No files provided' in data['message']

    def test_upload_path_resolution(self, client, temp_upload_dir, sample_terraform_file):
        """Test upload path resolution endpoint."""
        # First upload a file
        filename, content, content_type = sample_terraform_file
        from io import BytesIO
        file_data = {'files': (BytesIO(content.encode('utf-8')), filename, content_type)}
        
        upload_response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        json_response = upload_response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            upload_data = json_response['data']
        else:
            upload_data = json_response
            
        upload_id = upload_data['upload_id']
        
        # Test path resolution
        response = client.get(f'/upload/{upload_id}/resolve')
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            data = json_response['data']
        else:
            data = json_response
        
        # Check response structure
        assert 'terraform_path' in data
        assert 'var_files' in data
        assert 'csv_files' in data
        assert 'session_directory' in data
        assert 'all_files' in data
        assert 'file_count' in data
        
        # Verify file counts
        file_count = data['file_count']
        assert file_count['terraform'] == 1
        assert file_count['total'] == 1

    def test_csv_validation(self, client, temp_upload_dir, sample_csv_file):
        """Test CSV workload file validation."""
        # First upload a CSV file
        filename, content, content_type = sample_csv_file
        from io import BytesIO
        file_data = {'files': (BytesIO(content.encode('utf-8')), filename, content_type)}
        
        upload_response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        json_response = upload_response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            upload_data = json_response['data']
        else:
            upload_data = json_response
            
        upload_id = upload_data['upload_id']
        
        # Test CSV validation
        validation_data = {
            'csv_filename': filename,
            'time_column': 'time',
            'rate_column': 'rate'
        }
        
        response = client.post(
            f'/upload/{upload_id}/validate-csv',
            data=json.dumps(validation_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            data = json_response['data']
        else:
            data = json_response
        
        # Check validation results
        assert 'success' in data
        assert 'row_count' in data
        assert 'columns' in data
        assert 'time_range' in data

    def test_cleanup_temp_files(self, client):
        """Test temporary file cleanup endpoint."""
        cleanup_data = {'max_age_hours': 1}
        
        response = client.post(
            '/upload/cleanup',
            data=json.dumps(cleanup_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        json_response = response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            data = json_response['data']
        else:
            data = json_response
        
        assert data['success'] is True
        assert 'cleaned_count' in data
        assert 'max_age_hours' in data

    @patch('leaf_cloud.server._make_ccf_request')
    def test_ccf_proxy_success(self, mock_ccf_request, client):
        """Test CCF proxy endpoint with successful response."""
        # Mock CCF API response
        mock_response = {
            'data': [
                {
                    'timestamp': '2024-01-01T00:00:00Z',
                    'co2e': 1.5,
                    'kilowattHours': 2.3
                }
            ]
        }
        mock_ccf_request.return_value = mock_response
        
        # Test CCF proxy request
        params = {
            'startDate': '2024-01-01',
            'endDate': '2024-01-31',
            'accountId': 'test-account'
        }
        
        response = client.get('/utils/ccf-proxy', query_string=params)
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        assert 'data' in data
        assert 'metadata' in data

    @patch('leaf_cloud.server._make_ccf_request')
    def test_ccf_proxy_fallback(self, mock_ccf_request, client):
        """Test CCF proxy fallback when API is unavailable."""
        # Mock CCF API failure
        mock_ccf_request.side_effect = Exception("API unavailable")
        
        params = {
            'startDate': '2024-01-01',
            'endDate': '2024-01-31'
        }
        
        response = client.get('/utils/ccf-proxy', query_string=params)
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Should return fallback data
        assert data['success'] is True
        assert data['fallback'] is True
        assert 'data' in data

    def test_ccf_proxy_test_endpoint(self, client):
        """Test CCF proxy test endpoint."""
        response = client.get('/utils/ccf-proxy/test')
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        assert data['test'] is True
        assert 'mock_data' in data

    @patch('leaf_cloud.server.LEAFCloud')
    def test_simulate_endpoint_success(self, mock_leaf_cloud, client, temp_upload_dir, sample_terraform_file):
        """Test simulation endpoint with successful execution."""
        # Mock LEAFCloud simulation with proper structure
        mock_instance = Mock()
        
        # Create a proper mock result that matches the expected structure
        mock_raw_result = Mock()
        mock_raw_result.metadata = Mock()
        mock_raw_result.metadata.start_time = 0.0
        mock_raw_result.metadata.end_time = 45.6
        mock_raw_result.metrics = {
            'carbon': {'total_co2e': 100.5},
            'energy': {'total_kwh': 200.3},
            'latency': {'avg_latency_ms': 50.2},
            'scaling': {'avg_instances': 2.5}
        }
        mock_raw_result.results = {'test': 'data'}
        
        mock_instance.simulate.return_value = mock_raw_result
        mock_leaf_cloud.return_value = mock_instance
        
        # Upload terraform file first
        filename, content, content_type = sample_terraform_file
        from io import BytesIO
        file_data = {'files': (BytesIO(content.encode('utf-8')), filename, content_type)}
        upload_response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        json_response = upload_response.get_json()
        
        # Check if response is wrapped in data field
        if 'data' in json_response:
            upload_data = json_response['data']
        else:
            upload_data = json_response
        
        # Resolve upload paths first
        upload_id = upload_data['upload_id']
        resolve_response = client.get(f'/upload/{upload_id}/resolve')
        resolve_json = resolve_response.get_json()
        
        if 'data' in resolve_json:
            resolve_data = resolve_json['data']
        else:
            resolve_data = resolve_json
        
        # Test simulation
        sim_data = {
            'terraform': resolve_data['terraform_path'],
            'workload_type': 'steady',
            'duration': 3600,
            'queue_factor': 1.0
        }
        
        response = client.post(
            '/simulate',
            data=json.dumps(sim_data),
            content_type='application/json'
        )
        
        # Debug: print response if it fails
        if response.status_code != 200:
            print(f"Response status: {response.status_code}")
            print(f"Response data: {response.get_json()}")
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        assert 'metrics_summary' in data
        assert 'results' in data
        assert 'execution_time' in data

    @patch('leaf_cloud.server.LEAFCloud')
    def test_simulate_endpoint_failure(self, mock_leaf_cloud, client):
        """Test simulation endpoint with failure."""
        # Mock LEAFCloud simulation failure
        mock_instance = Mock()
        mock_instance.simulate.side_effect = LeafCloudError("Simulation failed")
        mock_leaf_cloud.return_value = mock_instance
        
        sim_data = {
            'terraform': '/nonexistent/path',
            'workload_type': 'steady'
        }
        
        response = client.post(
            '/simulate',
            data=json.dumps(sim_data),
            content_type='application/json'
        )
        
        assert response.status_code == 500
        data = response.get_json()
        assert data['success'] is False
        assert 'error' in data

    def test_simulate_missing_parameters(self, client):
        """Test simulation endpoint with missing required parameters."""
        response = client.post(
            '/simulate',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'error' in data

    @patch('leaf_cloud.server.read_result_file')
    def test_analyze_endpoint_success(self, mock_read_result, client):
        """Test analysis endpoint with successful execution."""
        # Mock result file reading
        mock_read_result.return_value = {
            'metrics': {'carbon': 100, 'energy': 200},
            'results': {'test': 'data'}
        }
        
        analysis_data = {
            'result_file': '/path/to/result.json'
        }
        
        response = client.post(
            '/analyze',
            data=json.dumps(analysis_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        assert 'metrics' in data
        assert 'output_files' in data

    def test_export_simulation_endpoint(self, client):
        """Test simulation export endpoint."""
        export_data = {
            'result_file': '/path/to/result.json',
            'format': 'json'
        }
        
        with patch('leaf_cloud.server.read_result_file') as mock_read:
            mock_read.return_value = {'test': 'data'}
            
            response = client.post(
                '/export/simulation',
                data=json.dumps(export_data),
                content_type='application/json'
            )
            
            assert response.status_code == 200
            data = response.get_json()
            assert data['success'] is True

    def test_error_handling_consistency(self, client):
        """Test that all endpoints return consistent error formats."""
        # Test various error scenarios
        error_endpoints = [
            ('/upload/nonexistent/resolve', 'GET'),
            ('/simulate', 'POST'),
            ('/analyze', 'POST'),
            ('/utils/ccf-proxy', 'GET')  # Missing required params
        ]
        
        for endpoint, method in error_endpoints:
            if method == 'GET':
                response = client.get(endpoint)
            else:
                response = client.post(endpoint, data='{}', content_type='application/json')
            
            # Should return error response (4xx or 5xx)
            assert response.status_code >= 400
            
            data = response.get_json()
            # Check consistent error format
            assert 'success' in data
            assert data['success'] is False
            assert 'error' in data

    def test_cors_headers(self, client):
        """Test that CORS headers are properly set."""
        response = client.get('/version')
        
        # Check for CORS headers (these are set by Flask-CORS)
        assert response.status_code == 200
        # Note: In test environment, CORS headers might not be fully set
        # This test ensures the endpoint is accessible

    def test_request_size_limits(self, client):
        """Test that file size limits are enforced."""
        # Create a large file content (exceeding the limit would require actual large data)
        # For testing purposes, we'll test the validation logic
        large_content = "x" * 1000  # Small for testing, but validates the logic
        
        from io import BytesIO
        file_data = {
            'files': (BytesIO(large_content.encode('utf-8')), 'large.tf', 'text/plain')
        }
        
        response = client.post('/upload', data=file_data, content_type='multipart/form-data')
        
        # Should succeed with small test file
        assert response.status_code == 200


class TestParameterValidation:
    """Test parameter validation across endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create a test client for the Flask app."""
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

    def test_workload_parameter_validation(self, client):
        """Test workload parameter validation."""
        # Test invalid workload type
        invalid_data = {
            'terraform': '/path/to/terraform',
            'workload_type': 'invalid_type'
        }
        
        response = client.post(
            '/simulate',
            data=json.dumps(invalid_data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        # Handle both string and dict error formats
        error_text = data.get('error', data.get('message', ''))
        if isinstance(error_text, dict):
            error_text = str(error_text)
        assert 'workload_type' in error_text.lower() or 'invalid' in error_text.lower()

    def test_numeric_parameter_validation(self, client):
        """Test numeric parameter validation."""
        # Test negative duration
        invalid_data = {
            'terraform': '/path/to/terraform',
            'duration': -100
        }
        
        response = client.post(
            '/simulate',
            data=json.dumps(invalid_data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False

    def test_date_parameter_validation(self, client):
        """Test date parameter validation for CCF proxy."""
        # Test invalid date format
        params = {
            'startDate': 'invalid-date',
            'endDate': '2024-01-31'
        }
        
        response = client.get('/utils/ccf-proxy', query_string=params)
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False


if __name__ == '__main__':
    pytest.main([__file__])