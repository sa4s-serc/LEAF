import os
import logging
import json
from typing import Dict, List, Optional, Any, Union, Tuple
import subprocess
import base64
from enum import Enum
from ..core.petri_net import PetriNet, Place, Token, Transition, Arc
from ..core.resource import Resource, ResourceType, ResourceState
from ..terraform.model_builder import ModelBuilder

"""
export/class_diagram.py

This module generates class diagrams of the modeled infrastructure using Mermaid.js syntax.
It visualizes relationships between GCP components, creates UML representations of the 
resource hierarchy, and exports diagrams in standard formats (SVG, PNG).
"""

# Configure logger
logger = logging.getLogger(__name__)

class RelationType(Enum):
    """Types of relationships between resources in the diagram"""
    DEPENDS_ON = 1     # Resource dependency
    CONNECTS_TO = 2    # Network connection
    SCALES_WITH = 3    # Scaling relationship
    BOTTLENECK = 4     # Performance bottleneck

def default_resource_mapping_function(model_builder: ModelBuilder) -> Dict[str, str]:
    """
    Default function to create a mapping between places and resources.
    This is used when the place class doesn't have a resource attribute.
    
    Args:
        model_builder: The ModelBuilder object containing both petri_net and resource_mapping
        
    Returns:
        Dictionary mapping place IDs to resource IDs
    """
    place_to_resource = {}
    
    # Since Place doesn't have a resource attribute directly,
    # we need to infer the mapping from the ModelBuilder
    # This assumes place IDs in the PetriNet correspond to resource IDs in some way
    
    # Try to match places to resources by name or ID patterns
    for place_id, place in model_builder.petri_net.places.items():
        # Skip special places like Source and Sink
        if place_id in ["Source", "Sink"]:
            continue
            
        # Try to find a matching resource
        for res_id, resource in model_builder.resource_mapping.items():
            # Check if place ID contains resource name
            res_name = resource.name if hasattr(resource, 'name') else str(res_id)
            if res_name in place_id:
                place_to_resource[place_id] = res_id
                break
    
    return place_to_resource

class ClassDiagramGenerator:
    """
    Generates class diagrams of the modeled infrastructure using Mermaid syntax.
    
    This class analyzes the Petri net model and resource relationships to create
    visual representations of the infrastructure, highlighting resource types,
    relationships, and potential bottlenecks.
    """
    
    def __init__(self, model: Union[PetriNet, ModelBuilder, Dict[str, Any]], 
                 include_bottlenecks: bool = True,
                 show_resource_categories: bool = True):
        """
        Initialize the class diagram generator.
        
        Args:
            model: The model to visualize (PetriNet, ModelBuilder, or model dict)
            include_bottlenecks: Whether to include bottleneck indicators
            show_resource_categories: Whether to show resource categories
        """
        self.include_bottlenecks = include_bottlenecks
        self.show_resource_categories = show_resource_categories
        
        # Extract model data
        if isinstance(model, PetriNet):
            self.petri_net = model
            self.resources = self._extract_resources_from_petri_net(model)
        elif isinstance(model, ModelBuilder):
            self.petri_net = model.petri_net
            self.resources = model.resource_mapping
        elif isinstance(model, dict):
            # self.petri_net = None  # Can't reconstruct full Petri net from dict
            self.resources = model.get('resources', {})
        else:
            raise ValueError("Model must be a PetriNet, ModelBuilder, or dictionary")
        
        # Store relationships between resources
        self.relationships = self._analyze_relationships()
        
        # Identify bottlenecks if requested
        self.bottlenecks = self._identify_bottlenecks() if include_bottlenecks else []
        
        logger.info(f"ClassDiagramGenerator initialized with {len(self.resources)} resources")

    def _extract_resources_from_petri_net(self, petri_net: PetriNet) -> Dict[str, Resource]:
        """
        Extract resources from places in the Petri net.
        Assumes that if a Place is meant to be represented as a resource, it will have
        a 'resource' attribute that is an instance of the Resource class.
        
        Args:
            petri_net: The Petri net model
            
        Returns:
            Dictionary mapping resource IDs to Resource objects
        """
        resources: Dict[str, Resource] = {}

        for place in petri_net.places.values():
            place_resource_attr = getattr(place, 'resource', None)
            if isinstance(place_resource_attr, Resource):
                # We have a valid Resource object associated with the place
                res_name = place_resource_attr.name
                res_type_val = "unknown_type"

                if isinstance(place_resource_attr.resource_type, ResourceType):
                    res_type_val = place_resource_attr.resource_type.value
                elif isinstance(place_resource_attr.resource_type, str):
                    # Fallback if resource_type is stored as a string
                    res_type_val = place_resource_attr.resource_type
                else:
                    logger.warning(f"Place {place.id}'s resource {res_name} has unexpected resource_type: {type(place_resource_attr.resource_type)}")
                    res_type_val = ResourceType.GENERIC.value # Default

                resource_id = f"{res_type_val}_{res_name}"
                resources[resource_id] = place_resource_attr
            elif place_resource_attr is not None:
                # 'resource' attribute exists but is not a Resource object
                logger.warning(f"Place {place.id} has a 'resource' attribute of unexpected type: {type(place_resource_attr)}. Skipping.")
        
        return resources

    def _analyze_relationships(self) -> List[Tuple[str, str, RelationType]]:
        """
        Analyze relationships between resources using Petri net input/output arcs.
        Returns connections between resource IDs.
        """
        relationships: List[Tuple[str, str, RelationType]] = []
        if not self.petri_net:
            return relationships
        # Map place IDs to resource IDs based on self.resources mapping
        place_to_resource: Dict[str, str] = {res.name: res_id for res_id, res in self.resources.items()}
        # Iterate transitions and their arcs
        for trans_id, transition in self.petri_net.transitions.items():
            # Input arcs for this transition
            input_arcs = self.petri_net.input_arcs.get(trans_id, [])
            output_arcs = self.petri_net.output_arcs.get(trans_id, [])
            for in_arc in input_arcs:
                src_place = in_arc.place_id
                if src_place not in place_to_resource:
                    continue
                src_res = place_to_resource[src_place]
                for out_arc in output_arcs:
                    tgt_place = out_arc.place_id
                    if tgt_place not in place_to_resource:
                        continue
                    tgt_res = place_to_resource[tgt_place]
                    relationships.append((src_res, tgt_res, RelationType.CONNECTS_TO))
        return relationships

    def _identify_bottlenecks(self) -> List[str]:
        """
        Identify potential bottlenecks in the model without modifying the relationships while iterating.

        Returns:
            List of resource IDs that could be bottlenecks
        """
        bottlenecks: List[str] = []
        if not self.petri_net:
            return bottlenecks
        # Count incoming connections per target
        connection_counts: Dict[str, int] = {}
        for source, target, _ in self.relationships:
            connection_counts[target] = connection_counts.get(target, 0) + 1
        # Threshold for bottleneck identification
        threshold = 3
        # Collect new relations to append afterwards
        new_relations: List[Tuple[str, str, RelationType]] = []
        for resource_id, count in connection_counts.items():
            if count >= threshold:
                bottlenecks.append(resource_id)
                # Schedule bottleneck edges for existing connections
                for source, target, _ in list(self.relationships):
                    if target == resource_id:
                        new_relations.append((source, target, RelationType.BOTTLENECK))
        # Append new bottleneck relations once
        self.relationships.extend(new_relations)
        return bottlenecks

    def generate_mermaid_code(self) -> str:
        """
        Generate Mermaid.js code for the class diagram.
        
        Returns:
            Mermaid.js code as a string
        """
        mermaid_lines = ["classDiagram"]
        
        # Initialize resource_by_type to group resources for diagram generation
        resource_by_type: Dict[str, List[Tuple[str, str]]] = {}
        for rt_member in ResourceType.__members__.values(): # Iterate over enum member instances
            resource_by_type[rt_member.value] = []
        
        # Group resources by type
        for res_id, resource_item in self.resources.items():
            res_type_str: str
            res_name_str: str

            if isinstance(resource_item, Resource):
                # It's a Resource object
                res_name_str = resource_item.name
                if isinstance(resource_item.resource_type, ResourceType):
                    res_type_str = resource_item.resource_type.value
                elif isinstance(resource_item.resource_type, str):
                    res_type_str = resource_item.resource_type # Fallback for string type
                else:
                    logger.warning(f"Resource object {res_id} has unexpected resource_type type: {type(resource_item.resource_type)}. Defaulting to GENERIC.")
                    res_type_str = ResourceType.GENERIC.value
            elif isinstance(resource_item, dict):
                # It's a dictionary representation of a resource
                res_name_str = resource_item.get('name', 'Unknown')
                res_type_val = resource_item.get('type', ResourceType.GENERIC.value)
                # Ensure res_type_val is a string (enum's value)
                if isinstance(res_type_val, ResourceType):
                    res_type_str = res_type_val.value
                else:
                    res_type_str = str(res_type_val)
            else:
                logger.warning(f"Item {res_id} in self.resources is of unexpected type: {type(resource_item)}. Using default values.")
                res_type_str = ResourceType.GENERIC.value
                res_name_str = "UnknownResource"
                
            # Add to the appropriate list in resource_by_type
            if res_type_str in resource_by_type:
                resource_by_type[res_type_str].append((res_id, res_name_str))
            else:
                # If type is not a predefined one, add to GENERIC category
                logger.warning(f"Resource type '{res_type_str}' for {res_id} not in predefined categories. Adding to GENERIC.")
                resource_by_type[ResourceType.GENERIC.value].append((res_id, res_name_str))
        
        # Add class definitions with appropriate styling
        for res_type, resources in resource_by_type.items():
            if resources:
                # Add class for the resource type itself
                if self.show_resource_categories:
                    mermaid_lines.append(f"    class {res_type} {{")
                    mermaid_lines.append(f"        +Type Category")
                    mermaid_lines.append("    }")
                
                # Add classes for each resource
                for res_id, res_name in resources:
                    # Clean the name for Mermaid
                    clean_id = res_id.replace("-", "_").replace(".", "_")
                    
                    mermaid_lines.append(f"    class {clean_id} {{")
                    mermaid_lines.append(f"        +{res_name}")
                    
                    # Add resource type
                    mermaid_lines.append(f"        +{res_type}")
                    
                    # Flag bottlenecks
                    if self.include_bottlenecks and res_id in self.bottlenecks:
                        mermaid_lines.append("        +BOTTLENECK!")
                        
                    mermaid_lines.append("    }")
                    
                    # Add relationship to type category
                    if self.show_resource_categories:
                        mermaid_lines.append(f"    {clean_id} --|> {res_type}")
        
        # Add relationships between resources
        for source, target, rel_type in self.relationships:
            # Clean IDs for Mermaid
            clean_source = source.replace("-", "_").replace(".", "_")
            clean_target = target.replace("-", "_").replace(".", "_")
            
            if rel_type == RelationType.DEPENDS_ON:
                mermaid_lines.append(f"    {clean_source} --> {clean_target} : depends on")
            elif rel_type == RelationType.CONNECTS_TO:
                mermaid_lines.append(f"    {clean_source} --> {clean_target} : connects to")
            elif rel_type == RelationType.SCALES_WITH:
                mermaid_lines.append(f"    {clean_source} ..> {clean_target} : scales with")
            elif rel_type == RelationType.BOTTLENECK:
                # Use dependency arrow '..>' for bottleneck annotation
                mermaid_lines.append(f"    {clean_source} ..> {clean_target} : bottleneck")
        
        # Add styling for different resource types
        mermaid_lines.append("    classDef compute fill:#f9f,stroke:#333,stroke-width:2px")
        mermaid_lines.append("    classDef storage fill:#bbf,stroke:#333,stroke-width:2px")
        mermaid_lines.append("    classDef network fill:#bfb,stroke:#333,stroke-width:2px")
        mermaid_lines.append("    classDef security fill:#fbb,stroke:#333,stroke-width:2px")
        mermaid_lines.append("    classDef bottleneck fill:#ff6666,stroke:#333,stroke-width:4px")
        
        # Apply styling
        mermaid_lines.append(f"    class COMPUTE compute")
        mermaid_lines.append(f"    class STORAGE storage")
        mermaid_lines.append(f"    class NETWORK network")
        mermaid_lines.append(f"    class SECURITY security")
        
        # Apply bottleneck styling
        for res_id in self.bottlenecks:
            clean_id = res_id.replace("-", "_").replace(".", "_")
            mermaid_lines.append(f"    class {clean_id} bottleneck")

        return "\n".join(mermaid_lines)

    def export_to_file(self, output_path: str, format: str = 'png') -> str:
        """
        Export the class diagram to a file.
        
        Args:
            output_path: Path to save the diagram
            format: Output format ('png' or 'svg')
        
        Returns:
            Path to the saved file
        """
        if format.lower() not in ['png', 'svg']:
            raise ValueError("Format must be 'png' or 'svg'")
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        # Generate Mermaid code
        mermaid_code = self.generate_mermaid_code()
        
        # Save Mermaid code to a temporary file
        mermaid_file = f"{output_path}.mmd"
        with open(mermaid_file, 'w') as f:
            f.write(mermaid_code)
        
        # Use Mermaid CLI to render the diagram if installed
        try:
            logger.info(f"Generating {format} diagram at {output_path}")
            
            # Try using mmdc CLI if available
            subprocess.run(
                ["mmdc", "-i", mermaid_file, "-o", output_path, "-t", "default"],
                check=True
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("Mermaid CLI not found. Consider installing with 'npm install -g @mermaid-js/mermaid-cli'")
            
            # Save just the Mermaid code as fallback
            with open(output_path, 'w') as f:
                f.write(f"```mermaid\n{mermaid_code}\n```")
            logger.info(f"Saved Mermaid code to {output_path} - manually render with a Mermaid-compatible tool")
        
        return output_path
        
    def export_to_url(self) -> str:
        """
        Generate a Mermaid Live Editor URL with the diagram.
        
        Returns:
            URL to view the diagram in Mermaid Live Editor
        """
        mermaid_code = self.generate_mermaid_code()
        encoded_code = base64.urlsafe_b64encode(mermaid_code.encode()).decode()
        return f"https://mermaid.live/edit#pako:{encoded_code}"


def generate_class_diagram(model, output_path: str, format: str = 'png', 
                          include_bottlenecks: bool = True,
                          show_resource_categories: bool = True) -> str:
    """
    Convenience function to generate and save a class diagram.
    
    Args:
        model: The model to visualize (PetriNet, ModelBuilder, or dict)
        output_path: Path to save the diagram
        format: Output format ('png' or 'svg')
        include_bottlenecks: Whether to include bottleneck indicators
        show_resource_categories: Whether to show resource categories
    
    Returns:
        Path to the saved diagram file
    """
    generator = ClassDiagramGenerator(
        model, 
        include_bottlenecks=include_bottlenecks,
        show_resource_categories=show_resource_categories
    )
    return generator.export_to_file(output_path, format)

def model_builder_to_class_diagram(model_builder: ModelBuilder,
                                    output_path: str,
                                    format: str = 'png',
                                    include_bottlenecks: bool = True,
                                    show_resource_categories: bool = True) -> str:
    """
    Generate and save a class diagram from a ModelBuilder instance.
    """
    if not isinstance(model_builder, ModelBuilder):
        raise ValueError("This function requires a ModelBuilder instance")
    # Use ClassDiagramGenerator directly for ModelBuilder
    generator = ClassDiagramGenerator(
        model_builder,
        include_bottlenecks=include_bottlenecks,
        show_resource_categories=show_resource_categories
    )
    return generator.export_to_file(output_path, format)