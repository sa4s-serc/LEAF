"""Visualization module for LEAF-Cloud.

This module provides functionality for generating various types of visualizations
based on the simulation models and results. It supports multiple diagram types
and integrates with the LEAF-Cloud core functionality.

Example:
    ```python
    from pathlib import Path
    from leaf_cloud.visualization import generate_diagrams

    # Generate all default diagrams
    diagrams = generate_diagrams(Path("path/to/terraform"))
    print(f"Generated diagrams: {diagrams}")
    
    # Generate specific diagram types
    diagrams = generate_diagrams(
        Path("path/to/terraform"),
        diagram_types=["deployment", "data_flow"]
    )
    ```
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Union

from ..exceptions import LeafCloudError, ValidationError

# Initialize module logger
logger = logging.getLogger(__name__)

# Define diagram types as a constant for reuse
DIAGRAM_TYPES = ["deployment", "class", "data_flow"]
DIAGRAM_EXTENSIONS = {"png", "svg", "pdf", "jpg", "jpeg"}

def generate_diagrams(
    terraform_dir: Union[str, Path],
    config_path: Optional[Union[str, Path]] = None,
    diagram_types: Optional[Sequence[str]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    output_format: str = "png",
    **kwargs: Any
) -> List[str]:
    """Generate visualizations based on the provided Terraform files.

    This function orchestrates the generation of various types of diagrams
    based on the Terraform configuration and LEAF-Cloud models.

    Args:
        terraform_dir: Path to the directory containing Terraform files.
        config_path: Optional path to the LEAF-Cloud configuration file.
        diagram_types: List of diagram types to generate. If None, all types are generated.
                     Supported types: 'deployment', 'class', 'data_flow'.
        output_dir: Directory to save the generated diagrams. If None, a 'diagrams'
                  subdirectory will be created in the current working directory.
        output_format: Output image format (e.g., 'png', 'svg', 'pdf'). Defaults to 'png'.
        **kwargs: Additional keyword arguments to pass to the diagram generator.

    Returns:
        List of paths to the generated diagram files.

    Raises:
        LeafCloudError: If diagram generation fails.
        ValidationError: If input parameters are invalid.
        FileNotFoundError: If terraform_dir or config_path doesn't exist.
        PermissionError: If output directory cannot be created or written to.

    Example:
        ```python
        # Basic usage
        diagrams = generate_diagrams("path/to/terraform")
        
        # Custom output directory and format
        diagrams = generate_diagrams(
            "path/to/terraform",
            output_dir="output/diagrams",
            output_format="svg"
        )
        ```
    """
    from ..config import LEAFCloudConfig
    from ..leaf import LEAFCloud
    
    try:
        # Validate input parameters
        terraform_dir = Path(terraform_dir).resolve()
        if not terraform_dir.is_dir():
            raise FileNotFoundError(f"Terraform directory not found: {terraform_dir}")
            
        if config_path is not None:
            config_path = Path(config_path).resolve()
            if not config_path.is_file():
                raise FileNotFoundError(f"Config file not found: {config_path}")
        
        output_format = output_format.lower()
        if output_format not in DIAGRAM_EXTENSIONS:
            raise ValidationError(
                f"Unsupported output format: {output_format}. "
                f"Supported formats: {', '.join(sorted(DIAGRAM_EXTENSIONS))}"
            )
        
        # Process diagram types
        if not diagram_types:
            diagram_types = DIAGRAM_TYPES
        else:
            invalid_types = set(diagram_types) - set(DIAGRAM_TYPES)
            if invalid_types:
                raise ValidationError(
                    f"Invalid diagram types: {', '.join(invalid_types)}. "
                    f"Supported types: {', '.join(DIAGRAM_TYPES)}"
                )
        
        # Set up output directory
        output_dir = Path(output_dir) if output_dir else Path.cwd() / "diagrams"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            # Test write access
            (output_dir / ".write_test").touch(exist_ok=True)
        except (OSError, PermissionError) as e:
            raise PermissionError(
                f"Cannot write to output directory {output_dir}: {str(e)}"
            ) from e
        
        logger.info("Generating %s diagrams in %s", 
                   ", ".join(diagram_types), output_dir)
        
        # Initialize LEAF-Cloud with the provided configuration
        config = LEAFCloudConfig()
        if config_path:
            logger.debug("Loading configuration from %s", config_path)
            config.load_from_file(str(config_path))
            
        leaf = LEAFCloud()
        logger.debug("Loading Terraform configuration from %s", terraform_dir)
        leaf.load_terraform(str(terraform_dir))
        
        # Generate requested diagram types
        generated_files = []
        for diagram_type in diagram_types:
            output_file = output_dir / f"{diagram_type}_diagram.{output_format}"
            try:
                logger.info("Generating %s diagram: %s", 
                          diagram_type, output_file)
                leaf.generate_visualization(
                    diagram_type=diagram_type,
                    output_file=str(output_file),
                    **kwargs
                )
                generated_files.append(str(output_file))
                logger.debug("Successfully generated %s", output_file)
            except Exception as e:
                logger.error("Failed to generate %s diagram: %s", 
                           diagram_type, str(e))
                raise LeafCloudError(
                    f"Failed to generate {diagram_type} diagram: {str(e)}"
                ) from e
            
        logger.info("Successfully generated %d diagrams", len(generated_files))
        return generated_files
        
    except (FileNotFoundError, PermissionError, ValidationError):
        # Re-raise expected exceptions with original traceback
        raise
    except Exception as e:
        # Wrap unexpected exceptions in LeafCloudError
        logger.exception("Unexpected error generating diagrams")
        raise LeafCloudError(f"Failed to generate diagrams: {str(e)}") from e
