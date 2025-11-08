import os
import logging
from typing import Dict, List, Any, Optional, Set, Tuple, cast
import hcl2
import yaml
import json
import re
import collections.abc
from ..utils.helpers import normalize_block

"""
terraform/parser.py

This module provides functionality to parse Terraform configuration files,
extract resource definitions and attributes, and handle variable resolution.
It serves as the first step in the LEAF-Cloud model building process by
providing structured Terraform data to the model builder.
"""

class TerraformParser:
    """
    Parses Terraform configuration files and extracts resources, variables,
    outputs, and other relevant information.
    """

    def __init__(self, terraform_dir: str, var_files: Optional[List[str]] = None):
        """
        Initialize the TerraformParser with a directory containing Terraform configurations.

        Args:
            terraform_dir: Path to the directory containing Terraform configurations
            var_files: Optional list of Terraform variable files (.tfvars) to use for resolution
        """
        self.terraform_dir = terraform_dir
        self.var_files = var_files or []
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.variables: Dict[str, Dict[str, Any]] = {}
        self.outputs: Dict[str, Dict[str, Any]] = {}
        self.modules: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO) # Default level, can be changed

    def parse_all(self) -> Dict[str, Any]:
        """
        Parse all Terraform files in the directory and extract resources, variables, and outputs.

        Returns:
            Dictionary containing parsed Terraform resources, variables, and outputs
        """
        self.logger.info(f"Starting Terraform parsing for directory: {self.terraform_dir}")
        self._load_variable_definitions()
        self._load_variable_values() # Loads defaults and then tfvars
        self._load_resources() # Also loads modules now
        self._load_outputs()
        self._resolve_variables() # Resolves variables in resources, modules, and outputs
        
        self.logger.info("Terraform parsing completed.")
        return {
            "resources": self.resources,
            "variables": self.variables,
            "outputs": self.outputs,
            "modules": self.modules # Added modules to the output
        }
    
    def _convert_to_pure_dict(self, data: Any) -> Any:
        """
        Convert HCL2 parser output to pure Python dictionaries and lists.
        Ensures all mapping types become dict and sequence types become list,
        and dictionary keys are strings.

        Args:
            data: Data structure returned by HCL2 parser or its parts

        Returns:
            Converted data structure
        """
        if isinstance(data, str):
            return data
        if isinstance(data, collections.abc.Mapping):
            return {str(k): self._convert_to_pure_dict(v) for k, v in data.items()}
        if isinstance(data, collections.abc.Sequence): # Excludes str due to the check above
            return [self._convert_to_pure_dict(item) for item in data]
        return data # Primitives, booleans, numbers, None, etc.
    
    def _load_variable_definitions(self) -> None:
        """
        Load variable definitions from Terraform files.
        """
        self.logger.debug(f"Starting _load_variable_definitions for dir: {self.terraform_dir}")
        found_files = self._find_terraform_files()
        self.logger.debug(f"Files found for variable definitions: {found_files}")
        for file_path in found_files:
            self.logger.debug(f"Processing file for variables: {file_path}")
            try:
                # It's better to open the file once for hcl2.load
                with open(file_path, 'r') as tf_file:
                    parsed_hcl = hcl2.load(tf_file)
                    self.logger.debug(f"hcl2.load output for {file_path}: {parsed_hcl}")
                    
                    parsed_dict = self._convert_to_pure_dict(parsed_hcl)
                    self.logger.debug(f"_convert_to_pure_dict output for {file_path}: {parsed_dict}")
                    
                    if 'variable' in parsed_dict:
                        self.logger.debug(f"'variable' key found in parsed_dict for {file_path}")
                        # The 'variable' key contains a list of dictionaries, 
                        # each dictionary representing one variable block with its name as key.
                        for var_block_dict in parsed_dict['variable']:
                            for var_name, var_config in var_block_dict.items():
                                if isinstance(var_config, list) and len(var_config) > 0:
                                    # HCL2 can wrap single variable configs in a list
                                    var_config_actual = var_config[0]
                                else:
                                    var_config_actual = var_config

                                if not isinstance(var_config_actual, dict):
                                    self.logger.warning(f"Skipping malformed variable '{var_name}' in {file_path}: config is not a dict.")
                                    continue

                                self.variables[var_name] = {
                                    'description': var_config_actual.get('description'),
                                    'type': var_config_actual.get('type'),
                                    'default': var_config_actual.get('default'),
                                    'sensitive': var_config_actual.get('sensitive', False)
                                }
                                self.logger.debug(f"Loaded variable '{var_name}' from {file_path}")
                    else:
                        self.logger.debug(f"'variable' key NOT found in parsed_dict for {file_path}")
                self.logger.debug(f"Successfully processed variable definitions from {file_path}")
            except Exception as e:
                self.logger.error(f"Error parsing variable definitions from {file_path}: {e}", exc_info=True)
    
    def _load_variable_values(self) -> None:
        """
        Load variable values from tfvars files.
        """
        # First, set defaults from variable definitions
        for var_name, var_config in self.variables.items():
            if 'default' in var_config and var_config['default'] is not None:
                self.variables[var_name]['value'] = var_config['default']
        
        # Then override with values from tfvars files
        tfvars_files_to_load = [os.path.join(self.terraform_dir, f) for f in os.listdir(self.terraform_dir) if f.endswith('.tfvars') or f.endswith('.tfvars.json')]
        if self.var_files:
            for var_file_path in self.var_files:
                if os.path.exists(var_file_path):
                    tfvars_files_to_load.append(var_file_path)
                else:
                    self.logger.warning(f"Specified var-file '{var_file_path}' not found.")

        for tfvars_file in tfvars_files_to_load:
            try:
                with open(tfvars_file, 'r') as f:
                    content = f.read()
                    # Determine if JSON or HCL format for .tfvars
                    if tfvars_file.endswith('.json'):
                        data = yaml.safe_load(content) # Using yaml.safe_load for JSON as well, as it's a superset
                    else: # Assuming HCL-like format for .tfvars, which hcl2 can parse
                        data = hcl2.loads(content)

                    if data: # Added check for data to prevent NoneType error
                        for var_name_orig, value in data.items():
                            var_name = str(var_name_orig) # Ensure string key
                            # Load values from .tfvars files
                            if var_name in self.variables:
                                # Update the existing variable entry with its value
                                self.variables[var_name]['value'] = value
                                self.logger.debug(f"Loaded variable value: {var_name} = {value} from {tfvars_file}")
                            else:
                                # This case should ideally not happen if variables are defined in .tf files first
                                self.logger.warning(f"Variable '{var_name}' found in {tfvars_file} but not defined in .tf files. Storing with value only.")
                                new_auto_var_entry: Dict[str, Any] = {'value': value}
                                self.variables[var_name] = new_auto_var_entry
                    else:
                        self.logger.debug(f"No data loaded from tfvars file: {tfvars_file} (it might be empty or comments only)")                            
            except Exception as e:
                self.logger.error(f"Error parsing tfvars file {tfvars_file}: {e}", exc_info=True)
        
        # Look for auto.tfvars or terraform.tfvars files which are loaded automatically by Terraform
        for auto_file in ['terraform.tfvars', 'terraform.tfvars.json', 'auto.tfvars', 'auto.tfvars.json']:
            auto_file_path = os.path.join(self.terraform_dir, auto_file)
            if os.path.exists(auto_file_path):
                try:
                    with open(auto_file_path, 'r') as f:
                        if auto_file.endswith('.json'):
                            var_values_loaded = yaml.safe_load(f)
                        else:
                            var_values_loaded = hcl2.load(f)
                        
                        # Ensure var_values_loaded is a flat dictionary
                        processed_var_values = {}
                        if isinstance(var_values_loaded, dict):
                            processed_var_values = var_values_loaded
                        elif isinstance(var_values_loaded, list): # Handle if hcl2 parses as list of blocks
                            for item_block in var_values_loaded:
                                if isinstance(item_block, dict):
                                    # This assumes that if it's a list of dicts, they are flat key-value pairs
                                    # representing variables, common in some HCL structures parsed by hcl2.
                                    processed_var_values.update(item_block) 
                        
                        if isinstance(processed_var_values, dict) and processed_var_values: # Check if it's a non-empty dict
                            for var_name_orig, value in processed_var_values.items():
                                var_name = str(var_name_orig) # Ensure string key
                                if var_name in self.variables: # Check with string key
                                    self.variables[var_name]['value'] = value
                                    self.logger.debug(f"Applied auto-loaded value for '{var_name}' from {auto_file_path}")
                                else:
                                    self.logger.warning(f"Variable '{var_name}' from auto-loaded file {auto_file_path} not defined in .tf files. Storing with value only.")
                                    new_auto_var_entry: Dict[str, Any] = {'value': value}
                                    self.variables[var_name] = new_auto_var_entry # Assign with string key
                        else:
                            self.logger.debug(f"Content of auto-loaded file {auto_file_path} did not result in a processable dictionary or was empty. Skipping. Content type: {type(var_values_loaded)}")
                        self.logger.info(f"Processed auto-loaded variable values from {auto_file_path}")
                except Exception as e:
                    self.logger.error(f"Error loading auto-loaded variable values from {auto_file_path}: {e}", exc_info=True)
    
    def _load_resources(self) -> None:
        """
        Load resource definitions from Terraform files.
        """
        self.logger.debug(f"Starting _load_resources for dir: {self.terraform_dir}")
        found_files = self._find_terraform_files()
        self.logger.debug(f"Files found for resource definitions: {found_files}")
        for file_path in found_files:
            self.logger.debug(f"Processing file for resources: {file_path}")
            try:
                with open(file_path, 'r') as tf_file:
                    parsed_hcl = hcl2.load(tf_file)
                    self.logger.debug(f"hcl2.load output for {file_path}: {parsed_hcl}")
                    
                    parsed_dict = self._convert_to_pure_dict(parsed_hcl)
                    self.logger.debug(f"_convert_to_pure_dict output for {file_path}: {parsed_dict}")

                    # Existing resource parsing logic
                    if 'resource' in parsed_dict:
                        self.logger.debug(f"'resource' key found in parsed_dict for {file_path}")
                        for resource_type_dict in parsed_dict['resource']:
                            for res_type, resources_in_type in resource_type_dict.items():
                                if not isinstance(resources_in_type, dict):
                                    self.logger.warning(f"Skipping resource type '{res_type}' in {file_path}: value is not a dict.")
                                    continue
                                for res_name, res_config_list in resources_in_type.items():
                                    res_config = None
                                    if isinstance(res_config_list, list) and len(res_config_list) > 0:
                                        res_config = res_config_list[0]
                                        if not isinstance(res_config, dict):
                                            self.logger.warning(f"Skipping resource '{res_type}.{res_name}' in {file_path}: config item is not a dict.")
                                            continue
                                    elif isinstance(res_config_list, dict):
                                        res_config = res_config_list
                                    else:
                                        self.logger.warning(f"Skipping resource '{res_type}.{res_name}' in {file_path}: config is not a list or dict.")
                                        continue

                                    normalized_name = cast(str, normalize_block(f"{res_type}.{res_name}"))
                                    self.resources[normalized_name] = {
                                        'type': res_type,
                                        'name': res_name,
                                        'config': res_config, # Store raw config, resolution happens later
                                        'file': file_path
                                    }
                                    self.logger.debug(f"Loaded resource definition: {normalized_name} from {file_path}")
                    else:
                        self.logger.debug(f"'resource' key NOT found in parsed_dict for {file_path}")

                    # Add module parsing logic
                    if 'module' in parsed_dict:
                        self.logger.debug(f"'module' key found in parsed_dict for {file_path}")
                        for module_block_list_item in parsed_dict['module']: # 'module' is a list of dicts, each dict is a module block
                            for module_name, module_config_container in module_block_list_item.items():
                                # module_config_container is usually a list containing one dict (the actual module config)
                                module_config = None
                                if isinstance(module_config_container, list) and len(module_config_container) > 0:
                                    module_config = module_config_container[0]
                                elif isinstance(module_config_container, dict): # Less common, but handle if it's a direct dict
                                    module_config = module_config_container
                                
                                if not isinstance(module_config, dict):
                                    self.logger.warning(f"Skipping malformed module '{module_name}' in {file_path}: config item is not a dict or not found correctly.")
                                    continue
                                
                                module_source = module_config.get('source', 'Unknown source')
                                self.modules[module_name] = {
                                    'config': module_config, # Store the actual config block
                                    'file': file_path,
                                    'source': module_source,
                                    'name': module_name
                                }
                                self.logger.debug(f"Loaded module definition: {module_name} from {file_path} with source '{module_source}'")
                    else:
                        self.logger.debug(f"'module' key NOT found in parsed_dict for {file_path}")

                self.logger.debug(f"Successfully processed definitions from {file_path}")
            except Exception as e:
                self.logger.error(f"Error parsing definitions from {file_path}: {e}", exc_info=True)
 
    def _load_outputs(self) -> None:
        """
        Load output definitions from Terraform files.
        """
        for file_path in self._find_terraform_files():
            try:
                with open(file_path, 'r') as f:
                    parsed = hcl2.load(f)
                    parsed = self._convert_to_pure_dict(parsed)
                    self.logger.debug(f"_convert_to_pure_dict output for {file_path}: {json.dumps(parsed, indent=2)}")

                    if 'output' in parsed:
                        self.logger.debug(f"'output' key found in parsed_dict for {file_path}")
                        # The 'output' key contains a list of dictionaries,
                        # each representing an output block with its name as key.
                        for output_block_dict in parsed['output']:
                            for output_name, output_config_list in output_block_dict.items():
                                # output_config_list is usually a list containing one dict (the actual config)
                                if isinstance(output_config_list, list) and len(output_config_list) > 0:
                                    output_config = output_config_list[0]
                                    if not isinstance(output_config, dict):
                                        self.logger.warning(f"Skipping malformed output '{output_name}' in {file_path}: config item is not a dict.")
                                        continue
                                elif isinstance(output_config_list, dict): # Should ideally be a list, but handle if it's a direct dict
                                     output_config = output_config_list
                                else:
                                    self.logger.warning(f"Skipping malformed output '{output_name}' in {file_path}: config is not a list or dict.")
                                    continue

                                self.outputs[output_name] = {
                                    'value': output_config.get('value'),
                                    'description': output_config.get('description'),
                                    'sensitive': output_config.get('sensitive', False)
                                }
                                self.logger.debug(f"Loaded output '{output_name}' from {file_path}")
                    else:
                        self.logger.debug(f"'output' key NOT found in parsed_dict for {file_path}")
                self.logger.debug(f"Loaded outputs from {file_path}")
            except Exception as e:
                self.logger.error(f"Error parsing outputs from {file_path}: {e}", exc_info=True)

    def _find_terraform_files(self) -> List[str]:
        """
        Find all Terraform files in the directory.

        Returns:
            List of paths to Terraform files
        """
        tf_files = []
        for root, _, files in os.walk(self.terraform_dir):
            for file in files:
                if file.endswith('.tf'):
                    tf_files.append(os.path.join(root, file))
        return tf_files
    
    def _resolve_variables(self) -> None:
        """
        Resolve variable references in resource configurations.
        """
        # Resolve resource configurations
        for res_id, res_data in self.resources.items():
            res_config = res_data['config']
            resolved_config = self._resolve_block(res_config, res_data.get('file'))
            self.resources[res_id]['config'] = resolved_config
            self.logger.debug(f"Resolved variables in resource {res_id}")
        
        # Resolve module configurations
        for module_name, module_data in self.modules.items():
            if 'config' in module_data and isinstance(module_data['config'], dict):
                module_config_val = module_data['config']
                # Ensure module_data.get('file') returns str or None for file_path argument
                file_path_val = module_data.get('file')
                resolved_config = self._resolve_block(module_config_val, str(file_path_val) if file_path_val is not None else None)
                
                # Use cast to help type checker with self.modules structure
                module_entry = cast(Dict[str, Any], self.modules[module_name])
                module_entry['config'] = resolved_config
                self.logger.debug(f"Resolved variables in module {module_name}")
            else:
                self.logger.warning(f"Module {module_name} has no 'config' dictionary, or 'config' is not a dict. Skipping variable resolution for this module.")
        
        # Resolve outputs
        for output_name, output_config in self.outputs.items():
            resolved_output = self._resolve_block(output_config, output_config.get('file'))
            self.outputs[output_name] = resolved_output
            self.logger.debug(f"Resolved variables in output {output_name}")

    def _resolve_block(self, block: Dict[str, Any], context_resource_name: Optional[str] = None, visited: Optional[Set[str]] = None) -> Dict[str, Any]:
        """
        Recursively resolve variable references in a block of Terraform configuration.

        Args:
            block: Terraform configuration block to resolve
            context_resource_name: The name of the resource or context for logging/debugging.
            visited: A set of visited variable/reference strings to detect circular dependencies.

        Returns:
            Resolved configuration block
        """
        if visited is None:
            visited = set()
        resolved_block: Dict[str, Any] = {}
        for key, value in block.items():
            resolved_item = self._resolve_value(value, context_resource_name, visited.copy()) # Pass context and visited.copy()
            resolved_block[key] = cast(Any, resolved_item)
        return resolved_block
    
    def _resolve_list(self, lst: List[Any], context_resource_name: Optional[str] = None, visited: Optional[Set[str]] = None) -> List[Any]:
        """
        Recursively resolve variable references in a list within Terraform configuration.

        Args:
            lst: List of Terraform configuration values to resolve
            context_resource_name: The name of the resource or context for logging/debugging.
            visited: A set of visited variable/reference strings to detect circular dependencies.

        Returns:
            Resolved list of values
        """
        if visited is None:
            visited = set()
        resolved_list: List[Any] = []
        for item in lst:
            resolved_sub_item = self._resolve_value(item, context_resource_name, visited.copy()) # Pass context and visited.copy()
            resolved_list.append(cast(Any, resolved_sub_item))
        return resolved_list
    
    def _resolve_value(self, value: Any, context_resource_name: Optional[str] = None, visited: Optional[Set[str]] = None) -> Any:
        """
        Recursively resolve a value, which might be a variable reference, function call, or complex type.

        Args:
            value: The value to resolve.
            context_resource_name: The name of the resource currently being processed, for context.
            visited: A set of visited variable/reference strings to detect circular dependencies.

        Returns:
            The resolved value.
        """
        current_visited = visited.copy() if visited is not None else set()

        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], collections.abc.Mapping):
            self.logger.debug(f"Unwrapping single-element list containing a dict: {value} in context: {context_resource_name}")
            return self._resolve_value(value[0], context_resource_name, current_visited)

        if isinstance(value, collections.abc.Mapping):
            return self._resolve_block(cast(Dict[str, Any], value), context_resource_name, current_visited)
        
        if isinstance(value, list):
            return self._resolve_list(value, context_resource_name, current_visited)

        if isinstance(value, str):
            var_pattern = re.compile(r'\$\{([^}]+)\}') # Adjusted regex to match ${...}
            
            # Check for direct single variable replacement to preserve type
            full_match = var_pattern.fullmatch(value)
            if full_match:
                ref_expression = full_match.group(1)
                if ref_expression in current_visited:
                    self.logger.warning(f"Circular dependency detected for '{ref_expression}' in '{value}' in context '{context_resource_name}'. Returning original string.")
                    return value
                
                new_visited_for_ref = current_visited.copy()
                new_visited_for_ref.add(ref_expression)

                ref_parts = ref_expression.split('.')
                if ref_parts[0] == 'var' and len(ref_parts) > 1:
                    var_name = ref_parts[1]
                    if var_name in self.variables:
                        var_data = self.variables[var_name]
                        val_to_sub = var_data.get('value', var_data.get('default'))
                        if val_to_sub is not None:
                            self.logger.debug(f"Single ref '{value}': Resolving '{var_name}' to its original value/type in context '{context_resource_name}'.")
                            return self._resolve_value(val_to_sub, context_resource_name, new_visited_for_ref)
                        else:
                            self.logger.warning(f"Variable '{var_name}' (single ref '{value}') in context '{context_resource_name}' has no value/default. Returning original string.")
                            return value # Return the original '${var.name}' string
                            
                    else:
                        self.logger.warning(f"Unknown variable '{var_name}' (single ref '{value}') in context '{context_resource_name}'. Known: {list(self.variables.keys())}. Returning original string.")
                        return value
                    
                elif ref_parts[0].startswith('google_') and len(ref_parts) > 2:
                    # e.g. "${google_cloud_run_service.cr-aula-spring.location}"
                    resource_id = f"{ref_parts[0]}.{ref_parts[1]}"
                    attr_name  = ref_parts[2]
                    if resource_id in self.resources:
                        target_block = self.resources[resource_id]['config']
                        resolved_val = target_block.get(attr_name)
                        if resolved_val is not None:
                            return self._resolve_value(resolved_val,
                                                       context_resource_name,
                                                       new_visited_for_ref)
                    return value
                # Add other direct handlers like 'local.', 'module.' here if needed
                else:
                    self.logger.debug(f"Single ref type '{ref_parts[0]}' in '{value}' (context '{context_resource_name}') not directly handled for typed substitution. Returning original string.")
                    return value # Fallback for unhandled single refs (e.g. ${local.name})

            # For interpolated strings or strings that are not single full references
            def replace_match(match_obj):
                full_ref_text = match_obj.group(0)  # e.g., ${var.project_id}
                ref_expression_inner = match_obj.group(1) # e.g., var.project_id

                if ref_expression_inner in current_visited:
                    self.logger.warning(f"Circular dependency detected for '{ref_expression_inner}' during interpolation of '{value}' in context '{context_resource_name}'. Using original ref text '{full_ref_text}'.")
                    return full_ref_text

                new_visited_for_part = current_visited.copy()
                new_visited_for_part.add(ref_expression_inner)
                
                ref_parts_inner = ref_expression_inner.split('.')
                if ref_parts_inner[0] == 'var' and len(ref_parts_inner) > 1:
                    var_name_inner = ref_parts_inner[1]
                    if var_name_inner in self.variables:
                        var_data_inner = self.variables[var_name_inner]
                        val_to_sub_inner = var_data_inner.get('value', var_data_inner.get('default'))
                        if val_to_sub_inner is not None:
                            resolved_part = self._resolve_value(val_to_sub_inner, context_resource_name, new_visited_for_part)
                            self.logger.debug(f"Interpolating '{full_ref_text}' with string part '{str(resolved_part)}' for var '{var_name_inner}' in context: {context_resource_name}")
                            return str(resolved_part)
                        else:
                            self.logger.warning(f"Interpolated var '{var_name_inner}' ('{full_ref_text}') in context '{context_resource_name}' has no value/default. Using original ref text.")
                            return full_ref_text # Return the original '${...}' part
                    else:
                        self.logger.warning(f"Unknown interpolated var '{var_name_inner}' ('{full_ref_text}') in context '{context_resource_name}'. Using original ref text.")
                        return full_ref_text
                # Add other interpolation handlers for 'local.', 'module.' here
                else:
                    self.logger.debug(f"Interpolated ref type '{ref_parts_inner[0]}' ('{full_ref_text}') in context '{context_resource_name}' not supported for substitution. Using original ref text.")
                    return full_ref_text # Return original '${...}' part if not 'var' or other handled types

            resolved_string, num_subs = var_pattern.subn(replace_match, value)
            return resolved_string # Result of subn is always a string, or original if no subs
        
        return value # Not a string, list, or dict; return as is (e.g. int, bool, None)

    def _resolve_variables_in_resource_configs(self):
        """Iterate through all loaded resources and resolve variables in their configurations."""
        for res_id, res_data in self.resources.items():
            self.logger.debug(f"Resolving variables for resource: {res_id}")
            if 'config' in res_data:
                # Pass res_id as the context_resource_name and initialize visited set
                res_data['config'] = self._resolve_value(res_data['config'], context_resource_name=res_id, visited=set())
            else:
                self.logger.warning(f"Resource {res_id} has no 'config' to resolve.")

    def _resolve_variables_in_outputs(self):
        """Iterate through all loaded outputs and resolve variables in their values."""
        for output_name, output_data in self.outputs.items():
            self.logger.debug(f"Resolving variables for output: {output_name}")
            if 'value' in output_data:
                output_data['value'] = self._resolve_value(output_data['value'], context_resource_name=output_name, visited=set()) # Pass visited
            else:
                self.logger.warning(f"Output {output_name} has no 'value' to resolve.")
            self.logger.debug(f"Resolved variables in output {output_name}")

    def get_all_resources(self) -> Dict[str, Dict[str, Any]]:
        return self.resources