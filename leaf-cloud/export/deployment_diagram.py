import os
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
import subprocess
import base64
from io import BytesIO
from ..core.petri_net import PetriNet
from ..core.resource import Resource, ResourceType


"""
export/deployment_diagram.py

This module provides functionality to generate deployment diagrams from LEAF-Cloud models,
showing the physical layout of resources, their regional distribution, network connections,
and data flows. It uses Mermaid syntax as an intermediate representation and can export
diagrams in various standard formats.
"""

# Configure logger
logger = logging.getLogger(__name__)


class DeploymentDiagramGenerator:
    """
    Generates deployment diagrams from LEAF-Cloud models using Mermaid syntax,
    showing resources by region, their connections, and data flows.
    """

    def __init__(self, petri_net: PetriNet, resource_mapping: Dict[str, Resource]):
        """
        Initialize the diagram generator with a model.

        Args:
            petri_net: The PetriNet model
            resource_mapping: Mapping from resource IDs to Resource objects
        """
        self.petri_net = petri_net
        self.resource_mapping = resource_mapping
        self.regions = {}  # Group resources by region
        self.connections = []  # Store connections between resources
        self.mermaid_code = ""

        # Process resources and their relationships
        self._process_resources()
        self._extract_connections()

        logger.info("Deployment diagram generator initialized")

    def _process_resources(self) -> None:
        """
        Process all resources and group them by region.
        """
        for res_id, resource in self.resource_mapping.items():
            region = resource.region
            if region is None:
                logger.warning(
                    f"Resource {res_id} has no region specified, defaulting to 'default'")
                region = "default"

            if region not in self.regions:
                self.regions[region] = {
                    ResourceType.COMPUTE: [],
                    ResourceType.STORAGE: [],
                    ResourceType.NETWORK: [],
                    ResourceType.SECURITY: []
                }

            self.regions[region][resource.resource_type].append(resource)

        logger.debug(
            f"Processed {len(self.resource_mapping)} resources across {len(self.regions)} regions")

    def _extract_connections(self) -> None:
        """
        Extract connections between resources from the Petri net model.
        """
        # Map place IDs to Resource objects
        place_to_resource = {}
        for place in self.petri_net.places.values():
            # Place.id matches resource.name when building
            for res in self.resource_mapping.values():
                if res.name == place.id:
                    place_to_resource[place.id] = res
                    break
        # Iterate transitions via input/output arcs
        for trans_id in self.petri_net.input_arcs:
            input_arcs = self.petri_net.input_arcs.get(trans_id, [])
            output_arcs = self.petri_net.output_arcs.get(trans_id, [])
            for in_arc in input_arcs:
                src_res = place_to_resource.get(in_arc.place_id)
                if not src_res:
                    continue
                for out_arc in output_arcs:
                    tgt_res = place_to_resource.get(out_arc.place_id)
                    if not tgt_res:
                        continue
                    # Pass actual Transition object
                    transition = self.petri_net.transitions.get(trans_id)
                    if transition is None:
                        logger.warning(f"Transition not found for ID: {trans_id}")
                        continue
                    
                    self.connections.append((src_res, tgt_res, transition))

    def generate_mermaid_diagram(self) -> str:
        """
        Generate a Mermaid deployment diagram from the model.

        Returns:
            Mermaid diagram syntax as a string
        """
        diagram = ["graph TB"]

        # Add region subgraphs
        region_ids = {}
        for region_name, resources_by_type in self.regions.items():
            # Sanitize region name for Mermaid ID
            region_id = f"region_{region_name.replace('-', '_')}"
            region_ids[region_name] = region_id

            # Start subgraph for region
            diagram.append(f"    subgraph {region_id}[{region_name}]")

            # Process resources by type
            for resource_type, resources in resources_by_type.items():
                if resources:
                    # Add resource type subgraph
                    type_name = resource_type.name
                    diagram.append(
                        f"        subgraph {region_id}_{type_name}[{type_name}]")

                    # Add resources
                    for resource in resources:
                        res_id = resource.id.replace('-', '_')
                        res_name = resource.name
                        icon = self._get_resource_icon(resource_type)
                        diagram.append(
                            f"            {res_id}[{icon} {res_name}]")

                    # Close resource type subgraph
                    diagram.append("        end")

            # Close region subgraph
            diagram.append("    end")
            diagram.append("")

        # Add connections
        for source, target, transition in self.connections:
            source_id = source.id.replace('-', '_')
            target_id = target.id.replace('-', '_')
            transition_label = transition.name.split(
                '_')[-1] if '_' in transition.name else ''

            # Add connection with optional label
            if transition_label:
                diagram.append(
                    f"    {source_id} -->|{transition_label}| {target_id}")
            else:
                diagram.append(f"    {source_id} --> {target_id}")

        # Join all lines and store the Mermaid code
        self.mermaid_code = "\n".join(diagram)
        return self.mermaid_code

    def _get_resource_icon(self, resource_type: ResourceType) -> str:
        """
        Get an appropriate icon for a resource type.

        Args:
            resource_type: The resource type

        Returns:
            String representing an icon (FontAwesome or Unicode)
        """
        icons = {
            ResourceType.COMPUTE: "🖥️",
            ResourceType.STORAGE: "💾",
            ResourceType.NETWORK: "🌐",
            ResourceType.SECURITY: "🔒",
            ResourceType.GENERIC: "📦"
        }
        return icons.get(resource_type, "📦")

    def export_diagram(self, output_path: str, format: str = "svg") -> str:
        """
        Export the diagram to a file in the specified format.

        Args:
            output_path: Path to save the diagram
            format: Format to export (svg, png, pdf)

        Returns:
            Path to the exported file
        """
        if not self.mermaid_code:
            self.generate_mermaid_diagram()

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # For SVG output, use Mermaid CLI if available
        if format.lower() == "svg":
            try:
                # Save Mermaid code to a temporary file
                temp_file = f"{output_path}.mmd"
                with open(temp_file, 'w') as f:
                    f.write(self.mermaid_code)

                # Use mmdc (Mermaid CLI) to convert to SVG
                result = subprocess.run(
                    ["mmdc", "-i", temp_file, "-o", output_path],
                    capture_output=True,
                    text=True
                )

                # Clean up temporary file
                os.remove(temp_file)

                if result.returncode != 0:
                    logger.warning(
                        f"Failed to convert Mermaid to SVG: {result.stderr}")
                    # Fallback: save just the Mermaid code
                    with open(output_path, 'w') as f:
                        f.write(self.mermaid_code)
                else:
                    logger.info(
                        f"Exported deployment diagram to {output_path}")

            except FileNotFoundError:
                logger.warning(
                    "Mermaid CLI not found. Saving Mermaid code only.")
                # Save just the Mermaid code if CLI not available
                with open(output_path, 'w') as f:
                    f.write(self.mermaid_code)

        # For PNG output
        elif format.lower() == "png":
            try:
                # Use Mermaid CLI if available
                temp_file = f"{output_path}.mmd"
                with open(temp_file, 'w') as f:
                    f.write(self.mermaid_code)

                # Convert to PNG
                result = subprocess.run(
                    ["mmdc", "-i", temp_file, "-o",
                        output_path, "-b", "transparent"],
                    capture_output=True,
                    text=True
                )

                # Clean up temporary file
                os.remove(temp_file)

                if result.returncode != 0:
                    logger.warning(
                        f"Failed to convert Mermaid to PNG: {result.stderr}")
                    # Fallback: save Mermaid code with .mmd extension
                    output_path = f"{output_path}.mmd"
                    with open(output_path, 'w') as f:
                        f.write(self.mermaid_code)

            except FileNotFoundError:
                logger.warning(
                    "Mermaid CLI not found. Saving Mermaid code only.")
                # Save just the Mermaid code with .mmd extension
                output_path = f"{output_path}.mmd"
                with open(output_path, 'w') as f:
                    f.write(self.mermaid_code)

        # For other formats, save as Mermaid code
        else:
            logger.warning(
                f"Unsupported format '{format}'. Saving Mermaid code only.")
            output_path = f"{output_path}.mmd"
            with open(output_path, 'w') as f:
                f.write(self.mermaid_code)

        return output_path

    def export_data_flow(self, output_path: str, format: str = "svg") -> str:
        """
        Generate and export a data flow diagram specifically highlighting data movement.

        Args:
            output_path: Path to save the diagram
            format: Format to export (svg, png, pdf)

        Returns:
            Path to the exported file
        """
        # Generate a specialized data flow diagram
        diagram = ["flowchart LR"]

        # Add data sources and sinks
        sources = set()
        sinks = set()

        for source, target, _ in self.connections:
            if source.resource_type == ResourceType.STORAGE:
                sources.add(source)
            if target.resource_type == ResourceType.STORAGE:
                sinks.add(target)

        # Add sources
        diagram.append("    subgraph sources[Data Sources]")
        for source in sources:
            source_id = source.id.replace('-', '_')
            diagram.append(f"        {source_id}[💾 {source.name}]")
        diagram.append("    end")

        # Add processing nodes (compute resources)
        diagram.append("    subgraph processing[Processing]")
        for source, target, _ in self.connections:
            if source.resource_type == ResourceType.COMPUTE or target.resource_type == ResourceType.COMPUTE:
                if source.resource_type == ResourceType.COMPUTE:
                    res = source
                else:
                    res = target
                res_id = res.id.replace('-', '_')
                diagram.append(f"        {res_id}[🖥️ {res.name}]")
        diagram.append("    end")

        # Add sinks
        diagram.append("    subgraph sinks[Data Sinks]")
        for sink in sinks:
            sink_id = sink.id.replace('-', '_')
            diagram.append(f"        {sink_id}[💾 {sink.name}]")
        diagram.append("    end")

        # Add data flow connections
        for source, target, _ in self.connections:
            source_id = source.id.replace('-', '_')
            target_id = target.id.replace('-', '_')

            # Data flows are directed
            diagram.append(f"    {source_id} -->|data| {target_id}")

        # Store the data flow diagram code
        data_flow_code = "\n".join(diagram)

        # Export using the same mechanism as the main export_diagram method
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Save Mermaid code to a temporary file
        temp_file = f"{output_path}.mmd"
        with open(temp_file, 'w') as f:
            f.write(data_flow_code)

        try:
            # Use mmdc (Mermaid CLI) to convert to desired format
            result = subprocess.run(
                ["mmdc", "-i", temp_file, "-o", output_path],
                capture_output=True,
                text=True
            )

            # Clean up temporary file
            os.remove(temp_file)

            if result.returncode != 0:
                logger.warning(
                    f"Failed to convert data flow diagram: {result.stderr}")
                # Fallback: save just the Mermaid code
                with open(output_path, 'w') as f:
                    f.write(data_flow_code)
        except Exception as e:
            logger.warning(f"Failed to convert data flow diagram: {e}")
            # Fallback: save just the Mermaid code
            with open(output_path, 'w') as f:
                f.write(data_flow_code)

        return output_path

    def generate_html_diagram(self, output_path: str) -> str:
        """
        Generate an interactive HTML version of the diagram using Mermaid JS.

        Args:
            output_path: Path to save the HTML file

        Returns:
            Path to the exported HTML file
        """
        if not self.mermaid_code:
            self.generate_mermaid_diagram()

        # Create HTML template with Mermaid JS
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LEAF-Cloud Deployment Diagram</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }}
        .mermaid {{
            display: flex;
            justify-content: center;
        }}
        h1 {{
            text-align: center;
            color: #333;
        }}
    </style>
</head>
<body>
    <h1>LEAF-Cloud Deployment Diagram</h1>
    <div class="mermaid">
{self.mermaid_code}
    </div>
    <script>
        mermaid.initialize({{ startOnLoad: true }});
    </script>
</body>
</html>
"""

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Write HTML file
        with open(output_path, 'w') as f:
            f.write(html_content)

        logger.info(
            f"Exported interactive HTML deployment diagram to {output_path}")
        return output_path


def generate_deployment_diagram(petri_net: PetriNet, resource_mapping: Dict[str, Resource],
                                output_path: str, format: str = "svg") -> str:
    """
    Generate a deployment diagram from a PetriNet model and export it.

    Args:
        petri_net: The PetriNet model
        resource_mapping: Mapping from resource IDs to Resource objects
        output_path: Path to save the diagram
        format: Format to export (svg, png, pdf)

    Returns:
        Path to the exported diagram
    """
    generator = DeploymentDiagramGenerator(petri_net, resource_mapping)
    generator.generate_mermaid_diagram()
    return generator.export_diagram(output_path, format)


def generate_data_flow_diagram(petri_net: PetriNet, resource_mapping: Dict[str, Resource],
                               output_path: str, format: str = "svg") -> str:
    """
    Generate a data flow diagram from a PetriNet model and export it.

    Args:
        petri_net: The PetriNet model
        resource_mapping: Mapping from resource IDs to Resource objects
        output_path: Path to save the diagram
        format: Format to export (svg, png, pdf)

    Returns:
        Path to the exported diagram
    """
    generator = DeploymentDiagramGenerator(petri_net, resource_mapping)
    return generator.export_data_flow(output_path, format)
