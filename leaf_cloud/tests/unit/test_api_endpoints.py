"""
Unit tests for API endpoints in the LEAF-Cloud web server.

This module tests all API endpoints for proper functionality, error handling,
and response formats as required by the webview integration.
"""

import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

# Import the server module and its components
from leaf_cloud.server import app, web_config
from leaf_cloud.exceptions import ValidationError, LeafCloudError


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
        temp_dir = tempfile.mkdtemp()
        original_upload_folder = web_config.upload_folder
        web_config.upload_folder = temp_dir
        yield temp_dir
        web_config.upload_folder = original_upload_folder
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_version_endpoint_success(self, client):
        """Test the /version endpoint returns correct version information."""
        response = client.get('/version')
        
        assert response.status_code == 200
        data = response.get_json()
        
        # Check required fields
        assert 'success' in data
        assert data['success'] is True
        assert 'data' in data
        
        version_data = data['data']
        assert 'version' in version_data
        assert 'build_date' in version_data
        assert 'description' in version_data
        assert 'api_version' in version_data
        assert 'build_info' in version_data
        assert 'features' in version_data
        
        # Check build_info structure
        build_info = version_data['build_info']
        assert 'timestamp' in build_info
        assert 'environment' in build_info
        assert 'server_mode' in build_info
        
        # Check features structure
        features = version_data['features']
        assert 'file_upload' in features
        assert 'static_serving' in features
        assert 'ccf_proxy' in features
        assert 'simulation' in features
        assert 'analysis' in features
    
    def test_upload_endpoint_no_files(self, client, temp_upload_dir):
        """Test upload endpoint with no files returns error."""
        response = client.post('/upload')
        
        assert response.status_code == 400
        data = response.get_json()
        
        assert 'success' in data
        assert data['success'] is False
        assert 'error' in data
        assert 'no_files' in data.get('code', '')
    
    def test_upload_endpoint_valid_terraform_file(self, client, temp_upload_dir):
        """Test upload endpoint with valid Terraform file."""
        # Create a mock Terraform file
        terraform_content = '''
        resource "aws_instance" "example" {
          ami           = "ami-0c55b159cbfafe1d0"
          instance_type = "t2.micro"
        }
        '''
        
        file_data = BytesIO(terraform_content.encode('utf-8'))
        file_storage = FileStorage(
            stream=file_data,
            filename='main.tf',
            content_type='text/plain'
        )
        
        response = client.post('/upload', data={
            'files': file_storage
        })
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert 'success' in data
        assert data['success'] is True
        assert 'data' in data
        
        upload_data = data['data']
        assert 'files' in upload_data
        assert 'temp_directory' in upload_data
        assert 'upload_id' in upload_data
        assert 'message' in upload_data
        
        # Check uploaded file info
        files = upload_data['files']
        assert len(files) == 1
        
        file_info = files[0]
        assert 'filename' in file_info
        assert 'original_filename' in file_info
        assert 'size' in file_info
        assert 'content_type' in file_info
        assert 'path' in file_info
        
        assert file_info['original_filename'] == 'main.tf'
        assert file_info['size'] > 0
    
    def test_upload_endpoint_invalid_file_type(self, client, temp_upload_dir):
        """Test upload endpoint with invalid file type."""
        # Create a mock file with invalid extension
        file_content = "This is not a terraform file"
        file_data = BytesIO(file_content.encode('utf-8'))
        file_storage = FileStorage(
            stream=file_data,
            filename='invalid.txt',
            content_type='text/plain'
        )
        
        response = client.post('/upload', data={
            'files': file_storage
        })
        
        assert response.status_code == 400
        data = response.get_json()
        
        assert 'success' in data
        assert data['success'] is False
        assert 'error' in data
        assert 'upload_failed' in data.get('code', '')
    
    def test_upload_endpoint_multiple_files(self, client, temp_upload_dir):
        """Test upload endpoint with multiple valid files."""
        # Create mock Terraform and tfvars files
        tf_content = 'resource "aws_instance" "example" {}'
        tfvars_content = 'instance_type = "t2.micro"'
        
        tf_file = FileStorage(
            stream=BytesIO(tf_content.encode('utf-8')),
            filename='main.tf',
            content_type='text/plain'
        )
        
        tfvars_file = FileStorage(
            stream=BytesIO(tfvars_content.encode('utf-8')),
            filename='terraform.tfvars',
            content_type='text/plain'
        )
        
        response = client.post('/upload', data={
            'files': [tf_file, tfvars_file]
        })
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        upload_data = data['data']
        assert len(upload_data['files']) == 2
    
    def test_upload_resolve_endpoint(self, client, temp_upload_dir):
        """Test upload resolve endpoint."""
        # First upload a file
        tf_content = 'resource "aws_instance" "example" {}'
        tf_file = FileStorage(
            stream=BytesIO(tf_content.encode('utf-8')),
            filename='main.tf',
            content_type='text/plain'
        )
        
        upload_response = client.post('/upload', data={'files': tf_file})
        upload_data = upload_response.get_json()['data']
        upload_id = upload_data['upload_id']
        
        # Test resolve endpoint
        response = client.get(f'/upload/{upload_id}/resolve')
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        resolve_data = data['data']
        
        assert 'terraform_path' in resolve_data
        assert 'var_files' in resolve_data
        assert 'csv_files' in resolve_data
        assert 'session_directory' in resolve_data
        assert 'all_files' in resolve_data
        assert 'file_count' in resolve_data
    
    def test_upload_resolve_endpoint_not_found(self, client):
        """Test upload resolve endpoint with non-existent upload ID."""
        response = client.get('/upload/non-existent-id/resolve')
        
        assert response.status_code == 404
        data = response.get_json()
        assert data['success'] is False
    
    def test_upload_cleanup_endpoint(self, client, temp_upload_dir):
        """Test upload cleanup endpoint."""
        response = client.post('/upload/cleanup', json={
            'max_age_hours': 1
        })
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        cleanup_data = data['data']
        assert 'cleaned_count' in cleanup_data
        assert 'max_age_hours' in cleanup_data
        assert 'message' in cleanup_data
    
    def test_upload_cleanup_endpoint_invalid_age(self, client):
        """Test upload cleanup endpoint with invalid max_age_hours."""
        response = client.post('/upload/cleanup', json={
            'max_age_hours': 0
        })
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
    
    @patch('leaf_cloud.server.requests.get')
    def test_ccf_proxy_endpoint_success(self, mock_get, client):
        """Test CCF proxy endpoint with successful response."""
        # Mock successful CCF API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'emissions': [
                {'date': '2024-01-01', 'co2e': 10.5, 'kwh': 25.0}
            ]
        }
        mock_get.return_value = mock_response
        
        response = client.get('/utils/ccf-proxy', query_string={
            'startDate': '2024-01-01',
            'endDate': '2024-01-31',
            'accountId': 'test-account'
        })
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        assert 'data' in data
        ccf_data = data['data']
        assert 'success' in ccf_data
        assert 'data' in ccf_data
        assert 'metadata' in ccf_data
    
    @patch('leaf_cloud.server.requests.get')
    def test_ccf_proxy_endpoint_api_error(self, mock_get, client):
        """Test CCF proxy endpoint with API error."""
        # Mock CCF API error response
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Server Error")
        mock_get.return_value = mock_response
        
        response = client.get('/utils/ccf-proxy', query_string={
            'startDate': '2024-01-01',
            'endDate': '2024-01-31'
        })
        
        assert response.status_code == 500
        data = response.get_json()
        assert data['success'] is False
    
    def test_ccf_proxy_test_endpoint(self, client):
        """Test CCF proxy test endpoint."""
        response = client.get('/utils/ccf-proxy/test')
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        test_data = data['data']
        assert 'message' in test_data
        assert 'mock_data' in test_data
        assert 'timestamp' in test_data
    
    @patch('leaf_cloud.server.LEAFCloud')
    def test_simulate_endpoint_success(self, mock_leaf_cloud, client, temp_upload_dir):
        """Test simulate endpoint with successful simulation."""
        # Mock LEAFCloud instance and simulation result
        mock_instance = Mock()
        mock_leaf_cloud.return_value = mock_instance
        
        mock_result = Mock()
        mock_result.success = True
        mock_result.metrics_summary = {
            'carbon': {'total_co2e_kg': 10.5},
            'energy': {'total_kwh': 25.0},
            'latency': {'avg_ms': 150.0},
            'scaling': {'avg_pods': 3}
        }
        mock_result.results = {'test': 'data'}
        mock_result.execution_time = 5.2
        mock_result.output_file = '/path/to/output.json'
        
        mock_instance.simulate.return_value = mock_result
        
        # Test simulation request
        response = client.post('/simulate', json={
            'terraform': '/path/to/terraform',
            'workload_type': 'steady',
            'workload_params': {'rate': 100},
            'duration': 3600
        })
        
        assert response.status_code == 200
        data = response.get_json()
        
        assert data['success'] is True
        sim_data = data['data']
        assert 'success' in sim_data
        assert 'metrics_summary' in sim_data
        assert 'results' in sim_data
        assert 'execution_time' in sim_data
    
    @patch('leaf_cloud.server.LEAFCloud')
    def test_simulate_endpoint_validation_error(self, mock_leaf_cloud, client):
        """Test simulate endpoint with validation error."""
        response = client.post('/simulate', json={
            'workload_type': 'invalid_type'
        })
        
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
    
    @patch('leaf_cloud.server.LEAFCloud')
    def test_simulate_endpoint_simulation_error(self, mock_leaf_cloud, client):
        """Test simulate endpoint with simulation error."""
        # Mock LEAFCloud instance that raises an error
        mock_instance = Mock()
        mock_leaf_cloud.return_value = mock_instance
        mock_instance.simulate.side_effect = LeafCloudError("Simulation failed")
        
        response = client.post('/simulate', json={
            'terraform': '/path/to/terraform',
            'workload_type': 'steady',
            'workload_params': {'rate': 100}
        })
        
        assert response.status_code == 500
        data = response.get_json()
        assert data['success'] is False
    
    def test_static_file_serving_index(self, client):
        """Test serving React application index.html."""
        with patch('leaf_cloud.server.send_from_directory') as mock_send:
            mock_send.return_value = "index.html content"
            
            response = client.get('/')
            
            # Should attempt to serve index.html
            mock_send.assert_called_once()
    
    def test_static_file_serving_assets(self, client):
        """Test serving static assets."""
        with patch('leaf_cloud.server.send_from_directory') as mock_send:
            mock_send.return_value = "asset content"
            
            response = client.get('/static/js/main.js')
            
            # Should attempt to serve the requested file
            mock_send.assert_called_once()
    
    def test_error_response_format(self, client):
        """Test that error responses follow the standard format."""
        # Test with an endpoint that should return an error
        response = client.get('/upload/non-existent-id/resolve')
        
        assert response.status_code == 404
        data = response.get_json()
        
        # Check error response format
        assert 'success' in data
        assert data['success'] is False
        assert 'error' in data
        assert isinstance(data['error'], str)
        
        # Optional fields
        if 'details' in data:
            assert isinstance(data['details'], str)
        if 'code' in data:
            assert isinstance(data['code'], str)


class TestWorkloadValidation:
    """Test workload parameter validation."""
    
    def test_steady_workload_validation(self):
        """Test steady workload parameter validation."""
        from leaf_cloud.server import _validate_workload_parameters
        
        # Valid steady workload
        params = _validate_workload_parameters('steady', {'rate': 100})
        assert params['rate'] == 100.0
        
        # Invalid rate
        with pytest.raises(ValidationError):
            _validate_workload_parameters('steady', {'rate': -10})
    
    def test_burst_workload_validation(self):
        """Test burst workload parameter validation."""
        from leaf_cloud.server import _validate_workload_parameters
        
        # Valid burst workload
        params = _validate_workload_parameters('burst', {
            'base_rate': 50,
            'peak_rate': 200,
            'duration': 60,
            'interval': 300
        })
        assert params['base_rate'] == 50.0
        assert params['peak_rate'] == 200.0
        
        # Missing required parameter
        with pytest.raises(ValidationError):
            _validate_workload_parameters('burst', {'base_rate': 50})
        
        # Invalid peak rate (less than base rate)
        with pytest.raises(ValidationError):
            _validate_workload_parameters('burst', {
                'base_rate': 100,
                'peak_rate': 50,
                'duration': 60,
                'interval': 300
            })
    
    def test_cyclical_workload_validation(self):
        """Test cyclical workload parameter validation."""
        from leaf_cloud.server import _validate_workload_parameters
        
        # Valid cyclical workload
        params = _validate_workload_parameters('cyclical', {
            'base_rate': 100,
            'amplitude': 50,
            'period': 3600
        })
        assert params['base_rate'] == 100.0
        assert params['amplitude'] == 50.0
        assert params['period'] == 3600.0
        assert params['phase_shift'] == 0.0  # Default value
        
        # With phase shift
        params = _validate_workload_parameters('cyclical', {
            'base_rate': 100,
            'amplitude': 50,
            'period': 3600,
            'phase_shift': 90
        })
        assert params['phase_shift'] == 90.0
    
    def test_random_workload_validation(self):
        """Test random workload parameter validation."""
        from leaf_cloud.server import _validate_workload_parameters
        
        # Valid random workload
        params = _validate_workload_parameters('random', {
            'min_rate': 50,
            'max_rate': 200
        })
        assert params['min_rate'] == 50.0
        assert params['max_rate'] == 200.0
        
        # Invalid max rate (less than min rate)
        with pytest.raises(ValidationError):
            _validate_workload_parameters('random', {
                'min_rate': 200,
                'max_rate': 50
            })
    
    def test_csv_workload_validation(self):
        """Test CSV workload parameter validation."""
        from leaf_cloud.server import _validate_workload_parameters
        
        # Valid CSV workload
        params = _validate_workload_parameters('csv', {
            'csv_file': '/path/to/workload.csv'
        })
        assert params['csv_file'] == '/path/to/workload.csv'
        assert params['time_column'] == 'time'  # Default
        assert params['rate_column'] == 'rate'  # Default
        assert params['base_rate'] == 0.0  # Default
        
        # With custom columns
        params = _validate_workload_parameters('csv', {
            'csv_file': '/path/to/workload.csv',
            'time_column': 'timestamp',
            'rate_column': 'requests_per_second',
            'base_rate': 10
        })
        assert params['time_column'] == 'timestamp'
        assert params['rate_column'] == 'requests_per_second'
        assert params['base_rate'] == 10.0


class TestFileValidation:
    """Test file upload validation."""
    
    def test_validate_uploaded_file_valid(self):
        """Test validation of valid uploaded file."""
        from leaf_cloud.server import _validate_uploaded_file
        
        # Mock valid file
        mock_file = Mock()
        mock_file.filename = 'main.tf'
        mock_file.content_length = 1024
        
        is_valid, error_msg = _validate_uploaded_file(mock_file)
        assert is_valid is True
        assert error_msg == ""
    
    def test_validate_uploaded_file_no_filename(self):
        """Test validation of file with no filename."""
        from leaf_cloud.server import _validate_uploaded_file
        
        # Mock file with no filename
        mock_file = Mock()
        mock_file.filename = None
        
        is_valid, error_msg = _validate_uploaded_file(mock_file)
        assert is_valid is False
        assert "No file provided" in error_msg
    
    def test_validate_uploaded_file_invalid_extension(self):
        """Test validation of file with invalid extension."""
        from leaf_cloud.server import _validate_uploaded_file
        
        # Mock file with invalid extension
        mock_file = Mock()
        mock_file.filename = 'invalid.txt'
        mock_file.content_length = 1024
        
        is_valid, error_msg = _validate_uploaded_file(mock_file)
        assert is_valid is False
        assert "Invalid file type" in error_msg
    
    def test_validate_uploaded_file_too_large(self):
        """Test validation of file that's too large."""
        from leaf_cloud.server import _validate_uploaded_file
        
        # Mock file that's too large
        mock_file = Mock()
        mock_file.filename = 'large.tf'
        mock_file.content_length = 20 * 1024 * 1024  # 20MB (larger than 16MB limit)
        
        is_valid, error_msg = _validate_uploaded_file(mock_file)
        assert is_valid is False
        assert "File too large" in error_msg
    
    def test_validate_uploaded_file_long_filename(self):
        """Test validation of file with very long filename."""
        from leaf_cloud.server import _validate_uploaded_file
        
        # Mock file with very long filename
        mock_file = Mock()
        mock_file.filename = 'a' * 300 + '.tf'  # 304 characters (longer than 255 limit)
        mock_file.content_length = 1024
        
        is_valid, error_msg = _validate_uploaded_file(mock_file)
        assert is_valid is False
        assert "Filename too long" in error_msg


class TestCCFProxy:
    """Test Cloud Carbon Footprint proxy functionality."""
    
    @patch('leaf_cloud.server.requests.get')
    def test_make_ccf_request_success(self, mock_get):
        """Test successful CCF API request."""
        from leaf_cloud.server import _make_ccf_request
        
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'test': 'data'}
        mock_get.return_value = mock_response
        
        result = _make_ccf_request('/emissions', {'param': 'value'})
        assert result == {'test': 'data'}
    
    @patch('leaf_cloud.server.requests.get')
    def test_make_ccf_request_timeout(self, mock_get):
        """Test CCF API request timeout."""
        from leaf_cloud.server import _make_ccf_request
        import requests
        
        # Mock timeout
        mock_get.side_effect = requests.Timeout("Request timed out")
        
        with pytest.raises(requests.RequestException):
            _make_ccf_request('/emissions', {})
    
    @patch('leaf_cloud.server.requests.get')
    def test_make_ccf_request_connection_error(self, mock_get):
        """Test CCF API connection error."""
        from leaf_cloud.server import _make_ccf_request
        import requests
        
        # Mock connection error
        mock_get.side_effect = requests.ConnectionError("Connection failed")
        
        with pytest.raises(requests.RequestException):
            _make_ccf_request('/emissions', {})
    
    def test_transform_ccf_response(self):
        """Test CCF response transformation."""
        from leaf_cloud.server import _transform_ccf_response
        
        # Test with emissions data
        ccf_data = {
            'emissions': [{'date': '2024-01-01', 'value': 10.5}],
            'co2e': 15.2,
            'kwh': 35.8
        }
        
        result = _transform_ccf_response(ccf_data)
        
        assert result['success'] is True
        assert 'data' in result
        assert 'metadata' in result
        assert 'emissions' in result
        assert 'carbon_footprint' in result
        assert 'energy_consumption' in result
        
        assert result['carbon_footprint']['co2e_kg'] == 15.2
        assert result['energy_consumption']['kwh'] == 35.8