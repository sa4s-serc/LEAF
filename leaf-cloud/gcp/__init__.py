"""
Google Cloud Platform (GCP) resources module for LEAF-Cloud framework.

This package provides classes for modeling GCP resources including compute,
networking, security, and generic components. These models include energy
consumption profiles, performance characteristics, and Petri net mappings
for simulation.
"""

__version__ = '0.1.0'

# Compute resources
from .compute import (
    ComputeResource,
    ComputeEngineVM,
    GKENode,
    GKECluster,
    AppEngine,
    CloudFunction,
    CloudRun
)

# Generic GCP components
from .generic_component import (
    GCPServiceCategory,
    GenericGCPComponent
)

# Network resources
from .network import (
    NetworkResourceType,
    LoadBalancerType,
    NetworkResource,
    VPC,
    LoadBalancer,
    CDN,
    DNS,
    Interconnect,
    VPN
)

# Security resources
from .security import (
    SecurityResourceType,
    SecurityOperationType,
    SecurityResource,
    IAM,
    SecurityCommandCenter,
    CloudKMS,
    CloudArmor,
    SecretManager
)

# Define public API
__all__ = [
    # Compute resources
    'ComputeResource', 'ComputeEngineVM', 'GKENode', 'GKECluster',
    'AppEngine', 'CloudFunction', 'CloudRun',
    
    # Generic components
    'GCPServiceCategory', 'GenericGCPComponent',
    
    # Network resources
    'NetworkResourceType', 'LoadBalancerType', 'NetworkResource',
    'VPC', 'LoadBalancer', 'CDN', 'DNS', 'Interconnect', 'VPN',
    
    # Security resources
    'SecurityResourceType', 'SecurityOperationType', 'SecurityResource',
    'IAM', 'SecurityCommandCenter', 'CloudKMS', 'CloudArmor', 'SecretManager'
]