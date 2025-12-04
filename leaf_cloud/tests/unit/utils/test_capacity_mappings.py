"""Unit tests for capacity_mappings.py."""
import pytest
from unittest.mock import patch, MagicMock
from enum import Enum

# Import the module to test
from leaf_cloud.utils.capacity_mappings import (
    _parse_cpu_to_float,
    get_capacity_from_config,
    TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP,
    RESOURCE_TYPE_DEFAULT_CAPACITIES,
    DIRECT_QUANTITY_ATTRIBUTES,
)

# Import the actual ResourceType to use in tests
from leaf_cloud.core.resource import ResourceType

# Apply patches for the module under test
with patch('leaf_cloud.utils.capacity_mappings.RESOURCE_TYPE_DEFAULT_CAPACITIES', {
    ResourceType.COMPUTE: 1.0,
    ResourceType.DATABASE: 1.0,
    ResourceType.STORAGE: 1.0,
    ResourceType.NETWORK: 1.0,
    ResourceType.SECURITY: 1.0,
    ResourceType.GENERIC: 1.0,
}):
    from leaf_cloud.utils import capacity_mappings
    from leaf_cloud.utils.capacity_mappings import (
        _parse_cpu_to_float,
        get_capacity_from_config,
        TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP,
        RESOURCE_TYPE_DEFAULT_CAPACITIES,
        DIRECT_QUANTITY_ATTRIBUTES,
    )

class TestParseCpuToFloat:
    """Test the _parse_cpu_to_float function."""
    
    def test_parse_integer_string(self):
        """Test parsing integer CPU strings."""
        assert _parse_cpu_to_float("1") == 1.0
        assert _parse_cpu_to_float("2") == 2.0
        
    def test_parse_float_string(self):
        """Test parsing float CPU strings."""
        assert _parse_cpu_to_float("0.5") == 0.5
        assert _parse_cpu_to_float("1.5") == 1.5
        
    def test_parse_millicpu(self):
        """Test parsing millicpu values."""
        assert _parse_cpu_to_float("500m") == 0.5
        assert _parse_cpu_to_float("1000m") == 1.0
        assert _parse_cpu_to_float("1500m") == 1.5
        
    def test_parse_number(self):
        """Test parsing numeric inputs."""
        assert _parse_cpu_to_float(1) == 1.0
        assert _parse_cpu_to_float(0.5) == 0.5
        assert _parse_cpu_to_float(1.5) == 1.5
        
    def test_parse_invalid(self):
        """Test invalid CPU strings."""
        assert _parse_cpu_to_float("invalid") is None
        assert _parse_cpu_to_float("1.2.3") is None
        assert _parse_cpu_to_float("123x") is None
        assert _parse_cpu_to_float("") is None
        assert _parse_cpu_to_float(None) is None

class TestGetCapacityFromConfig:
    """Test the get_capacity_from_config function."""
    
    def test_google_compute_instance_machine_type(self):
        """Test GCE instance machine type mapping."""
        config = {
            "machine_type": "e2-micro"
        }
        capacity = get_capacity_from_config(
            "google_compute_instance", 
            config, 
            ResourceType.COMPUTE
        )
        assert capacity == 0.25  # e2-micro has 0.25 vCPU
        
    def test_google_sql_database_instance_tier(self):
        """Test Cloud SQL instance tier mapping."""
        config = {
            "settings": {
                "tier": "db-n1-standard-4"
            }
        }
        capacity = get_capacity_from_config(
            "google_sql_database_instance",
            config,
            ResourceType.DATABASE
        )
        assert capacity == 4.0  # db-n1-standard-4 has 4 vCPUs
        
    def test_google_container_node_pool(self):
        """Test GKE node pool capacity calculation."""
        config = {
            "node_config": {
                "machine_type": "e2-standard-4"
            },
            "node_count": 2
        }
        capacity = get_capacity_from_config(
            "google_container_node_pool",
            config,
            ResourceType.COMPUTE
        )
        assert capacity == 8.0  # e2-standard-4 has 4 vCPUs * 2 nodes
        
    def test_google_cloudfunctions_function_memory(self):
        """Test Cloud Functions memory mapping."""
        # Test v1 function
        config_v1 = {
            "available_memory_mb": 1024
        }
        capacity_v1 = get_capacity_from_config(
            "google_cloudfunctions_function",
            config_v1,
            ResourceType.COMPUTE
        )
        assert capacity_v1 == 4.0  # 1024MB maps to 4.0 units
        
        # Test v2 function
        config_v2 = {
            "service_config": {
                "available_memory": "1Gi"
            }
        }
        capacity_v2 = get_capacity_from_config(
            "google_cloudfunctions_function",
            config_v2,
            ResourceType.COMPUTE
        )
        assert capacity_v2 == 4.0  # 1Gi maps to 4.0 units
        
    def test_direct_quantity_attribute(self):
        """Test direct quantity attributes."""
        config = {
            "target_size": 3
        }
        capacity = get_capacity_from_config(
            "google_compute_instance_group_manager",
            config,
            ResourceType.COMPUTE
        )
        assert capacity == 3.0  # Direct mapping of target_size
        
    def test_unknown_resource_type(self):
        """Test with an unknown resource type."""
        config = {}
        capacity = get_capacity_from_config(
            "unknown_resource_type",
            config,
            ResourceType.GENERIC
        )
        assert capacity == 1.0  # Default capacity for unknown types
        
    def test_missing_required_attributes(self):
        """Test with missing required attributes."""
        config = {}
        capacity = get_capacity_from_config(
            "google_compute_instance",
            config,
            ResourceType.COMPUTE
        )
        assert capacity == 1.0  # Default capacity when required attributes are missing

class TestCapacityMappings:
    """Test the capacity mapping constants and helper functions."""
    
    def test_terraform_attribute_map_structure(self):
        """Verify the structure of TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP."""
        assert isinstance(TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP, dict)
        assert "google_compute_instance" in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP
        assert "google_sql_database_instance" in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP
        
    def test_default_capacities(self):
        """Verify default capacities for resource types."""
        assert isinstance(RESOURCE_TYPE_DEFAULT_CAPACITIES, dict)
        assert all(isinstance(t, ResourceType) for t in RESOURCE_TYPE_DEFAULT_CAPACITIES.keys())
        assert all(isinstance(c, float) for c in RESOURCE_TYPE_DEFAULT_CAPACITIES.values())
        
    def test_direct_quantity_attributes(self):
        """Verify direct quantity attributes mapping."""
        assert isinstance(DIRECT_QUANTITY_ATTRIBUTES, dict)
        assert "google_compute_instance_group_manager" in DIRECT_QUANTITY_ATTRIBUTES
        assert "target_size" in DIRECT_QUANTITY_ATTRIBUTES["google_compute_instance_group_manager"]
