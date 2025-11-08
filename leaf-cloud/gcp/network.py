import logging
import math
from enum import Enum
from typing import Dict, List, Optional, Union, Any
from ..core.resource import Resource, ResourceType, ResourceState

"""
gcp/network.py

This module defines network-related GCP resources for the LEAF-Cloud framework.
It models VPC, Load Balancers, CDN, DNS, Interconnect, and VPN resources,
including their specific attributes, energy models, and Petri net mappings.
"""

# Configure logger
logger = logging.getLogger(__name__)


class NetworkResourceType(Enum):
    """Specific network resource types in GCP"""
    VPC = 'vpc'
    LOAD_BALANCER = 'load_balancer'
    CDN = 'cdn'
    DNS = 'dns'
    INTERCONNECT = 'interconnect'
    VPN = 'vpn'
    FIREWALL = 'firewall'
    ROUTER = 'router'
    NAT = 'nat'


class LoadBalancerType(Enum):
    """Types of GCP load balancers"""
    HTTP = 'http'
    TCP = 'tcp'
    UDP = 'udp'
    INTERNAL = 'internal'
    EXTERNAL = 'external'


class NetworkResource(Resource):
    """Base class for all network resources in GCP"""
    
    def __init__(self, 
                 name: str,
                 capacity: float = 1.0,
                 network_type: NetworkResourceType = NetworkResourceType.VPC,
                 region: Optional[str] = None,
                 bandwidth_mbps: float = 1000.0,
                 latency_ms: float = 1.0):
        """
        Initialize a network resource.
        
        Args:
            name: Unique name for the resource
            capacity: Maximum capacity of the resource
            network_type: Specific type of network resource
            region: The GCP region where the resource is deployed
            bandwidth_mbps: Maximum bandwidth in Mbps
            latency_ms: Base latency in milliseconds
        """
        super().__init__(name, capacity, ResourceType.NETWORK, region)
        self.network_type = network_type
        self.bandwidth_mbps = bandwidth_mbps
        self.base_latency_ms = latency_ms
        # Track active connections and traffic
        self.active_connections = 0
        self.current_throughput_mbps = 0.0
        self.total_data_transferred_gb = 0.0
        
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate energy consumption at the given timestamp.
        
        Network equipment energy consumption is modeled based on:
        1. Base power (idle state)
        2. Utilization-dependent power (traffic load)
        3. Number of active connections
        
        Args:
            timestamp: Simulation time to calculate energy consumption
            
        Returns:
            Energy consumption value in kWh
        """
        # Base power consumption is 20% of total possible power
        base_power = 0.2
        
        # Get current utilization (based on traffic)
        utilization = self.utilization_ratio
        
        # Calculate energy as: base power + utilization-dependent portion
        # This is a simplified model - in reality, the relationship might be non-linear
        power_factor = base_power + (1 - base_power) * utilization
        
        # Scale by bandwidth capacity (larger bandwidth = more energy)
        bandwidth_factor = self.bandwidth_mbps / 1000  # normalize to Gbps
        
        # Different network resources have different energy profiles
        if self.network_type == NetworkResourceType.VPC:
            resource_factor = 0.7
        elif self.network_type == NetworkResourceType.LOAD_BALANCER:
            resource_factor = 1.0
        elif self.network_type == NetworkResourceType.CDN:
            resource_factor = 1.2  # CDNs use more energy due to distributed nature
        elif self.network_type == NetworkResourceType.DNS:
            resource_factor = 0.5  # DNS uses less energy
        else:
            resource_factor = 0.8
        
        # Energy (kWh) = power (kW) * time (h)
        # This is a simplified model - assumes the timestamp is in hours
        energy_consumption = power_factor * bandwidth_factor * resource_factor * 0.01  # scale to reasonable kWh
        
        return energy_consumption
    
    def get_current_latency_ms(self) -> float:
        """
        Calculate the current latency including base latency and congestion effects.
        
        Returns:
            Current latency in milliseconds
        """
        # Simple congestion model: latency increases with utilization
        # At 100% utilization, latency is doubled
        congestion_factor = 1.0 + self.utilization_ratio
        return self.base_latency_ms * congestion_factor
    
    def map_to_petri_net_elements(self) -> Dict[str, Any]:
        """
        Map this network resource to Petri net elements.
        
        Returns:
            Dictionary of Petri net elements representing this resource
        """
        # Basic mapping - to be extended with actual Petri net implementation
        return {
            "places": [
                {"id": f"{self.id}_input", "name": f"{self.name}_input", "tokens": 0},
                {"id": f"{self.id}_processing", "name": f"{self.name}_processing", "tokens": 0},
                {"id": f"{self.id}_output", "name": f"{self.name}_output", "tokens": 0}
            ],
            "transitions": [
                {
                    "id": f"{self.id}_process", 
                    "name": f"{self.name}_process",
                    "delay": self.get_current_latency_ms() / 1000.0  # Convert ms to seconds
                }
            ],
            "arcs": [
                {"from": f"{self.id}_input", "to": f"{self.id}_process"},
                {"from": f"{self.id}_process", "to": f"{self.id}_output"}
            ],
            "resource_id": self.id
        }


class VPC(NetworkResource):
    """
    Google Cloud Virtual Private Cloud (VPC) network.
    """
    
    def __init__(self, 
                 name: str,
                 region: Optional[str] = None,
                 subnet_ranges: Optional[List[str]] = None,
                 auto_create_subnets: bool = False,
                 mtu: int = 1500,
                 bandwidth_mbps: float = 10000.0):
        """
        Initialize a VPC network.
        
        Args:
            name: Name of the VPC network
            region: GCP region for the VPC
            subnet_ranges: CIDR ranges for subnets
            auto_create_subnets: Whether to auto-create subnets
            mtu: Maximum transmission unit
            bandwidth_mbps: Maximum bandwidth in Mbps
        """
        super().__init__(name, 
                         capacity=1.0, 
                         network_type=NetworkResourceType.VPC, 
                         region=region,
                         bandwidth_mbps=bandwidth_mbps,
                         latency_ms=0.5)  # VPC has very low base latency
                         
        self.subnet_ranges = subnet_ranges or []
        self.auto_create_subnets = auto_create_subnets
        self.mtu = mtu
        self.connected_resources: Dict[str, str] = {}  # resource_id -> resource_type
        
    def add_connected_resource(self, resource_id: str, resource_type: str) -> None:
        """Add a resource connected to this VPC network"""
        self.connected_resources[resource_id] = resource_type
        logger.debug(f"Added resource {resource_id} of type {resource_type} to VPC {self.name}")
        
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate energy consumption for VPC.
        VPCs consume energy based on connected resources and traffic.
        """
        base_energy = super().get_energy_consumption(timestamp)
        # Additional factor based on number of connected resources
        connected_factor = min(1.0, len(self.connected_resources) / 100.0)
        
        return base_energy * (1.0 + 0.5 * connected_factor)


class LoadBalancer(NetworkResource):
    """
    Google Cloud Load Balancer resource.
    """
    
    def __init__(self, 
                 name: str,
                 region: Optional[str] = None,
                 lb_type: LoadBalancerType = LoadBalancerType.HTTP,
                 bandwidth_mbps: float = 5000.0,
                 max_connections: int = 10000):
        """
        Initialize a Load Balancer resource.
        
        Args:
            name: Name of the load balancer
            region: GCP region for the load balancer
            lb_type: Type of load balancer
            bandwidth_mbps: Maximum bandwidth in Mbps
            max_connections: Maximum number of simultaneous connections
        """
        super().__init__(name, 
                         capacity=1.0, 
                         network_type=NetworkResourceType.LOAD_BALANCER, 
                         region=region,
                         bandwidth_mbps=bandwidth_mbps,
                         latency_ms=2.0)  # LB adds ~2ms of latency
        
        self.lb_type = lb_type
        self.max_connections = max_connections
        self.backend_services: List[str] = []
        self.health_checks_enabled = True
        self.ssl_certificates: List[str] = []
        
    def add_backend_service(self, service_name: str) -> None:
        """Add a backend service to this load balancer"""
        if service_name not in self.backend_services:
            self.backend_services.append(service_name)
            
    def get_current_latency_ms(self) -> float:
        """
        Calculate load balancer latency including SSL processing if applicable.
        """
        base_latency = super().get_current_latency_ms()
        
        # SSL processing adds latency
        if self.lb_type == LoadBalancerType.HTTP and self.ssl_certificates:
            base_latency += 1.0  # SSL adds ~1ms
            
        # If heavily loaded, add more latency
        if self.utilization_ratio > 0.8:
            base_latency *= 1.5
            
        return base_latency


class CDN(NetworkResource):
    """
    Google Cloud CDN (Content Delivery Network) resource.
    """
    
    def __init__(self, 
                 name: str,
                 region: Optional[str] = None,
                 bandwidth_mbps: float = 10000.0,
                 cache_size_gb: float = 1000.0):
        """
        Initialize a CDN resource.
        
        Args:
            name: Name of the CDN
            region: GCP region (edge locations)
            bandwidth_mbps: Maximum bandwidth in Mbps
            cache_size_gb: Size of the cache in GB
        """
        super().__init__(name, 
                         capacity=1.0, 
                         network_type=NetworkResourceType.CDN, 
                         region=region,
                         bandwidth_mbps=bandwidth_mbps,
                         latency_ms=15.0)  # CDNs have higher latency due to geographic distribution
        
        self.cache_size_gb = cache_size_gb
        self.cache_hit_ratio = 0.8  # Default cache hit ratio
        self.edge_locations = []  # List of edge locations
        self.origins = []  # List of origin servers
        self.ssl_enabled = True
        
    def add_edge_location(self, location: str) -> None:
        """Add a CDN edge location"""
        if location not in self.edge_locations:
            self.edge_locations.append(location)
            logger.debug(f"Added edge location {location} to CDN {self.name}")
            
    def add_origin(self, origin: str) -> None:
        """Add an origin server to this CDN"""
        if origin not in self.origins:
            self.origins.append(origin)
            
    def get_current_latency_ms(self) -> float:
        """
        Calculate CDN latency based on cache hit ratio.
        
        Returns:
            Latency in milliseconds
        """
        base_latency = super().get_current_latency_ms()
        
        # If cache hit, latency is lower; if cache miss, latency is higher
        expected_latency = (self.cache_hit_ratio * base_latency) + \
                          ((1 - self.cache_hit_ratio) * base_latency * 5)
        
        # Edge locations reduce latency for users close to them
        edge_factor = max(0.5, 1.0 - (len(self.edge_locations) * 0.02))
        
        return expected_latency * edge_factor


class DNS(NetworkResource):
    """
    Google Cloud DNS (Domain Name System) resource.
    """
    
    def __init__(self,
                name: str,
                region: Optional[str] = None,
                zones_count: int = 1,
                records_per_zone: int = 100):
        """
        Initialize a DNS resource.
        
        Args:
            name: Name of the DNS service
            region: GCP region
            zones_count: Number of DNS zones
            records_per_zone: Average number of records per zone
        """
        super().__init__(name,
                        capacity=1.0,
                        network_type=NetworkResourceType.DNS,
                        region=region,
                        bandwidth_mbps=100.0,  # DNS requires less bandwidth
                        latency_ms=20.0)  # DNS lookups add latency
                        
        self.zones_count = zones_count
        self.records_per_zone = records_per_zone
        self.total_records = zones_count * records_per_zone
        self.query_rate_per_second = 0.0
        self.cache_ttl_seconds = 300  # Default TTL of 5 minutes
        
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate DNS energy consumption.
        DNS servers use less energy than other network resources.
        """
        base_energy = super().get_energy_consumption(timestamp)
        
        # Scale by number of zones and records
        zones_factor = math.log10(max(1, self.zones_count)) / 10
        
        # Query rate affects energy consumption
        query_factor = min(1.0, self.query_rate_per_second / 10000)
        
        return base_energy * (0.5 + zones_factor + query_factor)
        
    def update_query_rate(self, queries_per_second: float) -> None:
        """Update the current query rate"""
        self.query_rate_per_second = queries_per_second


class Interconnect(NetworkResource):
    """
    Google Cloud Interconnect for dedicated connections to GCP.
    """
    
    def __init__(self,
                name: str,
                region: str,
                bandwidth_mbps: float = 10000.0,
                interconnect_type: str = "dedicated",
                redundant: bool = True):
        """
        Initialize an Interconnect resource.
        
        Args:
            name: Name of the interconnect
            region: GCP region
            bandwidth_mbps: Bandwidth in Mbps (10Gbps default)
            interconnect_type: 'dedicated' or 'partner'
            redundant: Whether redundant connections are configured
        """
        super().__init__(name,
                        capacity=1.0,
                        network_type=NetworkResourceType.INTERCONNECT,
                        region=region,
                        bandwidth_mbps=bandwidth_mbps,
                        latency_ms=5.0)  # Direct interconnects have low latency
                        
        self.interconnect_type = interconnect_type
        self.redundant = redundant
        self.vlan_attachments = []
        self.bgp_sessions = []
        self.mtu = 1500
        
    def add_vlan_attachment(self, vlan_id: int, vlan_name: str) -> None:
        """Add a VLAN attachment to this interconnect"""
        self.vlan_attachments.append({"id": vlan_id, "name": vlan_name})
        
    def add_bgp_session(self, peer_ip: str, peer_asn: int) -> None:
        """Add a BGP session to this interconnect"""
        self.bgp_sessions.append({"peer_ip": peer_ip, "peer_asn": peer_asn})
        
    def get_current_latency_ms(self) -> float:
        """Calculate interconnect latency based on configuration"""
        base_latency = super().get_current_latency_ms()
        
        # Dedicated interconnects have lower latency than partner interconnects
        if self.interconnect_type == "dedicated":
            type_factor = 0.8
        else:
            type_factor = 1.2
            
        # Redundant connections can reduce effective latency through load balancing
        if self.redundant:
            redundancy_factor = 0.9
        else:
            redundancy_factor = 1.0
            
        return base_latency * type_factor * redundancy_factor


class VPN(NetworkResource):
    """
    Google Cloud VPN for secure connections to GCP.
    """
    
    def __init__(self,
                name: str,
                region: str,
                vpn_type: str = "ha",  # 'classic' or 'ha' (high availability)
                bandwidth_mbps: float = 3000.0,
                encryption_algorithm: str = "aes-256-gcm"):
        """
        Initialize a VPN resource.
        
        Args:
            name: Name of the VPN
            region: GCP region
            vpn_type: 'classic' or 'ha' (high availability)
            bandwidth_mbps: Bandwidth in Mbps (3Gbps default for HA VPN)
            encryption_algorithm: Encryption algorithm used
        """
        super().__init__(name,
                        capacity=1.0,
                        network_type=NetworkResourceType.VPN,
                        region=region,
                        bandwidth_mbps=bandwidth_mbps,
                        latency_ms=20.0)  # VPNs add encryption overhead to latency
                        
        self.vpn_type = vpn_type
        self.encryption_algorithm = encryption_algorithm
        self.tunnels = []
        self.remote_peers = []
        self.routes = []
        
    def add_tunnel(self, tunnel_name: str, shared_secret: Optional[str] = None) -> None:
        """Add a VPN tunnel"""
        self.tunnels.append({"name": tunnel_name, "shared_secret": shared_secret})
        
    def add_remote_peer(self, peer_ip: str, peer_name: Optional[str] = None) -> None:
        """Add a remote peer for the VPN connection"""
        self.remote_peers.append({"ip": peer_ip, "name": peer_name or peer_ip})
        
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate VPN energy consumption.
        VPNs use more energy due to encryption/decryption operations.
        """
        base_energy = super().get_energy_consumption(timestamp)
        
        # Encryption affects energy consumption
        if self.encryption_algorithm == "aes-256-gcm":
            crypto_factor = 1.3
        elif self.encryption_algorithm == "aes-128-gcm":
            crypto_factor = 1.2
        else:
            crypto_factor = 1.1
            
        # HA VPNs use more energy due to redundant tunnels
        if self.vpn_type == "ha":
            ha_factor = 1.5
        else:
            ha_factor = 1.0
            
        # Number of tunnels also affects energy
        tunnel_factor = max(1.0, len(self.tunnels) * 0.2)
        
        return base_energy * crypto_factor * ha_factor * tunnel_factor
        
    def get_current_latency_ms(self) -> float:
        """Calculate VPN latency including encryption overhead"""
        base_latency = super().get_current_latency_ms()
        
        # Encryption adds overhead
        if self.encryption_algorithm == "aes-256-gcm":
            crypto_latency = 1.4
        elif self.encryption_algorithm == "aes-128-gcm":
            crypto_latency = 1.2
        else:
            crypto_latency = 1.1
            
        # HA VPNs can have lower effective latency due to redundancy
        if self.vpn_type == "ha" and len(self.tunnels) > 1:
            ha_factor = 0.9
        else:
            ha_factor = 1.0
            
        return base_latency * crypto_latency * ha_factor
    
    
class APIGateway(NetworkResource):
    """
    Google Cloud API Gateway for secure API management.
    """
    
    def __init__(self,
                name: str,
                region: str,
                bandwidth_mbps: float = 1000.0,
                max_requests_per_second: float = 1000.0):
        """
        Initialize an API Gateway resource.
        
        Args:
            name: Name of the API Gateway
            region: GCP region
            bandwidth_mbps: Bandwidth in Mbps (1Gbps default)
            max_requests_per_second: Maximum requests per second
        """
        super().__init__(name,
                        capacity=1.0,
                        network_type=NetworkResourceType.LOAD_BALANCER,
                        region=region,
                        bandwidth_mbps=bandwidth_mbps,
                        latency_ms=2.0)  # API Gateway adds ~2ms of latency
                        
        self.max_requests_per_second = max_requests_per_second
        self.endpoints = []
        self.auth_methods = []
        
    def add_endpoint(self, endpoint: str) -> None:
        """Add an API endpoint to this gateway"""
        if endpoint not in self.endpoints:
            self.endpoints.append(endpoint)
            
    def add_auth_method(self, method: str) -> None:
        """Add an authentication method to this gateway"""
        if method not in self.auth_methods:
            self.auth_methods.append(method)
            
    def get_current_latency_ms(self) -> float:
        """Calculate API Gateway latency based on configuration"""
        base_latency = super().get_current_latency_ms()
        
        # Auth methods can add latency
        auth_latency = len(self.auth_methods) * 0.5
        
        # If heavily loaded, add more latency
        if self.utilization_ratio > 0.8:
            base_latency *= 1.5
            
        return base_latency + auth_latency