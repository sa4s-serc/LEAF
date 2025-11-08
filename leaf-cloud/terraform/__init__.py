import logging
from .parser import TerraformParser
from .model_builder import ModelBuilder
from .generator import TerraformGenerator

"""
LEAF-Cloud Terraform Module

This package provides functionality for parsing Terraform configurations,
building cloud infrastructure models, and generating optimized Terraform code.

The module consists of three main components:
- Parser: Extracts resources, variables, and outputs from Terraform files
- ModelBuilder: Converts Terraform resources into a LEAF-Cloud model
- Generator: Creates optimized Terraform configurations from simulation results
"""


# Configure package logger
logger = logging.getLogger(__name__)

# Import main classes from submodules

# Package metadata
__version__ = "0.1.0"
__author__ = "LEAF-Cloud Team"

# Export public classes
__all__ = [
    "TerraformParser",
    "ModelBuilder",
    "TerraformGenerator",
]