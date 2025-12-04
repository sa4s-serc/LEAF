# LEAF-Cloud Validators

This module provides centralized validation functionality for the LEAF-Cloud project, reducing code duplication and ensuring consistent validation behavior across the codebase.

## Overview

The validators module includes:

1. **Base Validators**: Common validation functions and base classes
2. **Configuration Validation**: For validating application configuration
3. **Resource Type Mapping**: Centralized mapping between Terraform and LEAF-Cloud resource types
4. **Schema Validation**: For validating data against schemas with version support

## Usage

### Basic Validation

```python
from validators import validate_positive_number, validate_in_range

# Validate a positive number
try:
    validate_positive_number(42, "Answer")
except ValueError as e:
    print(e)

# Validate a number in range
try:
    validate_in_range(5, "Value", min_val=1, max_val=10)
except ValueError as e:
    print(e)
```

### Configuration Validation

```python
from validators.config_validator import validate_config

# Validate a configuration object
errors = validate_config(config)
if errors:
    for error in errors:
        print(f"Validation error: {error}")
```

### Resource Type Mapping

```python
from validators.resource_types import get_resource_type

# Get LEAF-Cloud resource type for a Terraform resource
resource_type = get_resource_type("google_compute_instance")
print(resource_type)  # Output: ComputeEngineVM
```

### Schema Validation

```python
from validators.schema_validator import schema_validator
from pydantic import BaseModel

class UserModel(BaseModel):
    id: int
    name: str
    email: str

# Validate data against a Pydantic model
try:
    user_data = {"id": 1, "name": "John Doe", "email": "john@example.com"}
    user = schema_validator.validate_with_model(user_data, UserModel)
    print(f"Valid user: {user}")
except SchemaValidationError as e:
    print(f"Validation failed: {e}")
```

## Adding New Validators

1. **For common validations**: Add new functions to `__init__.py`
2. **For configuration validation**: Create a new validator class in `config_validator.py`
3. **For new resource types**: Add mappings to `resource_types.py`
4. **For schema validation**: Add schema files to the `schemas` directory

## Best Practices

1. Always use the validators from this module instead of writing custom validation logic
2. Keep validation logic in this module, not in other parts of the codebase
3. Add new validators when you find yourself writing similar validation code in multiple places
4. Write tests for new validators
