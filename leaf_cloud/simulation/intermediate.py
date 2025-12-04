"""Intermediate model export functionality for LEAF-Cloud.

This module provides functions to export the intermediate representation (IR)
of the infrastructure model from Terraform configurations. The IR is a 
simplified, normalized representation of the infrastructure that can be used
for analysis, visualization, and further processing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import (
    Any, Dict, List, Optional, Tuple, Union, Mapping, 
    TypeVar, cast, overload
)

from ..exceptions import LeafCloudError, ModelExportError
from ..config import LEAFCloudConfig

# Initialize module logger
logger = logging.getLogger(__name__)

# Type variables for generic types
T = TypeVar('T')
PathLike = Union[str, Path]


def _validate_terraform_dir(terraform_dir: Path) -> None:
    """Validate that the Terraform directory exists and contains valid files.
    
    Args:
        terraform_dir: Path to the Terraform directory to validate.
        
    Raises:
        FileNotFoundError: If the directory doesn't exist or contains no .tf files.
        NotADirectoryError: If the path exists but is not a directory.
    """
    terraform_dir = Path(terraform_dir).resolve()
    
    if not terraform_dir.exists():
        raise FileNotFoundError(f"Terraform directory not found: {terraform_dir}")
    if not terraform_dir.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {terraform_dir}")
    
    # Recursively check for .tf files
    has_tf_files = any(
        f.suffix == '.tf' 
        for f in terraform_dir.rglob('*') 
        if f.is_file()
    )
    
    if not has_tf_files:
        raise FileNotFoundError(
            f"No Terraform configuration files (.tf) found in {terraform_dir} or its subdirectories"
        )


def _validate_var_files(var_files: Optional[Tuple[Path, ...]]) -> List[str]:
    """Validate and process Terraform variable files.
    
    Args:
        var_files: Optional tuple of paths to variable files.
        
    Returns:
        List of validated variable file paths as strings.
        
    Raises:
        FileNotFoundError: If any variable file doesn't exist.
    """
    if not var_files:
        return []
    
    var_file_paths = []
    for var_file in var_files:
        var_file_path = Path(var_file).resolve()
        if not var_file_path.exists():
            raise FileNotFoundError(f"Variable file not found: {var_file_path}")
        var_file_paths.append(str(var_file_path))
    
    return var_file_paths


def _create_intermediate_model(
    terraform_dir: Path,
    config: LEAFCloudConfig,
    leaf: Any  # Avoid circular import
) -> Dict[str, Any]:
    """Create the intermediate model representation.
    
    Args:
        terraform_dir: Path to the Terraform directory.
        config: LEAF-Cloud configuration.
        leaf: Initialized LEAFCloud instance.
        
    Returns:
        Dictionary containing the intermediate model.
    """
    logger.debug("Creating intermediate model for directory: %s", terraform_dir)
    
    model: Dict[str, Any] = {
        'version': '1.0',
        'terraform_dir': str(terraform_dir),
        'config': {},
        'resources': [],
        'modules': [],
        'variables': [],
        'outputs': [],
        'metadata': {
            'generated_by': 'LEAF-Cloud',
            'format_version': '1.0',
        }
    }
    
    # Add configuration if available
    if hasattr(config, 'to_dict'):
        model['config'] = config.to_dict()
    
    # Try to get the intermediate model from the LEAF instance
    if hasattr(leaf, 'get_intermediate_model'):
        logger.debug("Getting intermediate model from LEAF instance")
        try:
            leaf_model = leaf.get_intermediate_model()
            logger.debug("Got model from get_intermediate_model()")
            
            if isinstance(leaf_model, dict):
                logger.debug("Updating model with data from get_intermediate_model()")
                if 'resources' in leaf_model:
                    logger.debug("Found %d resources in leaf_model", len(leaf_model.get('resources', [])))
                model.update(leaf_model)
                return model
            logger.warning(
                "Expected dictionary from get_intermediate_model(), got %s",
                type(leaf_model).__name__
            )
        except Exception as e:
            logger.warning(
                "Failed to get intermediate model from LEAF instance: %s",
                str(e), exc_info=True
            )
    
    # Fallback to basic model structure
    logger.debug("Using fallback intermediate model structure")
    return model


@overload
def export_intermediate_model(
    terraform_dir: PathLike,
    *,
    config_path: Optional[PathLike] = ...,
    var_files: Optional[Tuple[PathLike, ...]] = ...,
    output_path: None = ...
) -> Dict[str, Any]:
    ...

@overload
def export_intermediate_model(
    terraform_dir: PathLike,
    *,
    config_path: Optional[PathLike] = ...,
    var_files: Optional[Tuple[PathLike, ...]] = ...,
    output_path: PathLike
) -> None:
    ...

def export_intermediate_model(
    terraform_dir: PathLike,
    *,
    config_path: Optional[PathLike] = None,
    var_files: Optional[Tuple[PathLike, ...]] = None,
    output_path: Optional[PathLike] = None
) -> Optional[Dict[str, Any]]:
    """Export the intermediate representation of the infrastructure model.
    
    This function generates an intermediate representation (IR) of the
    infrastructure defined in the Terraform configuration. The IR is a
    simplified, normalized representation that can be used for analysis,
    visualization, and further processing.
    
    Args:
        terraform_dir: Path to the directory containing Terraform configuration
            files. Must be a valid directory containing at least one .tf file.
        config_path: Optional path to the LEAF-Cloud configuration file.
            If not provided, default configuration will be used.
        var_files: Optional tuple of paths to Terraform variable files (.tfvars).
            These will be passed to the Terraform configuration.
        output_path: Optional path where to save the intermediate model as JSON.
            If not provided, the model will be returned as a dictionary.
            
    Returns:
        If output_path is None, returns the intermediate model as a dictionary.
        If output_path is provided, returns None after writing to the file.
        
    Raises:
        FileNotFoundError: If terraform_dir, config_path, or any var_file
            doesn't exist, or if terraform_dir contains no .tf files.
        NotADirectoryError: If terraform_dir is not a directory.
        ModelExportError: If there's an error generating or exporting the model.
        
    Example:
        ```python
        from pathlib import Path
        from leaf_cloud.simulation.intermediate import export_intermediate_model
        
        # Basic usage
        model = export_intermediate_model("path/to/terraform")
        
        # With configuration and variable files
        export_intermediate_model(
            terraform_dir="path/to/terraform",
            config_path="config.yaml",
            var_files=("vars.tfvars", "secrets.auto.tfvars"),
            output_path="output/model.json"
        )
        ```
    """
    try:
        # Convert and validate paths
        terraform_path = Path(terraform_dir).resolve()
        _validate_terraform_dir(terraform_path)
        
        # Process variable files
        var_file_paths = _validate_var_files(
            tuple(Path(f) for f in var_files) if var_files else None
        )
        
        # Load configuration if provided
        config = LEAFCloudConfig()
        if config_path:
            config_path_obj = Path(config_path).resolve()
            if not config_path_obj.exists():
                raise FileNotFoundError(
                    f"Configuration file not found: {config_path_obj}"
            )
            logger.info("Loading configuration from %s", config_path_obj)
            config.load_from_file(config_path_obj)
        
        # Import LEAFCloud locally to avoid circular imports
        from ..leaf import LEAFCloud  # type: ignore[import]
        
        # Initialize LEAF-Cloud
        logger.debug("Initializing LEAF-Cloud")
        leaf = LEAFCloud(config_path=config_path)
        
        # Load Terraform configuration with variable files
        logger.info("Loading Terraform configuration from %s", terraform_path)
        logger.debug("Using variable files: %s", var_file_paths if var_file_paths else "None")
        
        try:
            # Load Terraform configuration
            leaf.load_terraform(str(terraform_path), var_files=var_file_paths)
            logger.debug("Successfully loaded Terraform configuration")
            
            # Debug: Check if parser was initialized
            if hasattr(leaf, '_parser'):
                logger.debug("TerraformParser instance found in LEAFCloud")
                # Debug: Check if resources were loaded
                if hasattr(leaf._parser, 'resources'):
                    logger.debug("Found %d resources in parser", len(leaf._parser.resources))
                    for i, (res_id, res_data) in enumerate(leaf._parser.resources.items()):
                        if i < 5:  # Only log first 5 resources to avoid log spam
                            logger.debug("Resource %d: %s - %s", i+1, res_id, res_data.get('type', 'unknown'))
                        elif i == 5:
                            logger.debug("... and %d more resources", len(leaf._parser.resources) - 5)
        except Exception as e:
            logger.error("Error loading Terraform configuration: %s", str(e), exc_info=True)
            raise
        
        try:
            # Build the model to generate the intermediate representation
            logger.info("Building infrastructure model")
            leaf.build_model()
            logger.debug("Successfully built infrastructure model")
            
            # Debug: Check if model was built
            if hasattr(leaf, 'model'):
                logger.debug("Model built successfully")
                if isinstance(leaf.model, dict) and 'resources' in leaf.model:
                    logger.debug("Model contains %d resources", len(leaf.model.get('resources', [])))
        except Exception as e:
            logger.error("Error building infrastructure model: %s", str(e), exc_info=True)
            raise
        
        try:
            # Create the intermediate model
            logger.debug("Generating intermediate model")
            model = _create_intermediate_model(terraform_path, config, leaf)
            logger.debug("Intermediate model created")
            
            # Debug: Check the created model
            if isinstance(model, dict):
                logger.debug("Intermediate model keys: %s", list(model.keys()))
                if 'resources' in model:
                    logger.debug("Intermediate model contains %d resources", len(model['resources']))
                else:
                    logger.warning("No 'resources' key in intermediate model")
        except Exception as e:
            logger.error("Error creating intermediate model: %s", str(e), exc_info=True)
            raise
        
        # Save to file if output path is provided
        if output_path is not None:
            output_path_obj = Path(output_path).resolve()
            try:
                output_path_obj.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path_obj, 'w', encoding='utf-8') as f:
                    json.dump(
                        model, 
                        f, 
                        indent=2, 
                        default=str,
                        ensure_ascii=False
                    )
                logger.info("Intermediate model exported to %s", output_path_obj)
            except (IOError, OSError) as e:
                raise ModelExportError(
                    f"Failed to write intermediate model to {output_path_obj}: {e}"
                ) from e
            return None
        
        return model
        
    except (FileNotFoundError, NotADirectoryError) as e:
        # Re-raise file/directory related errors as-is
        raise
    except Exception as e:
        error_msg = f"Failed to export intermediate model: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if not isinstance(e, ModelExportError):
            raise ModelExportError(error_msg) from e
        raise
