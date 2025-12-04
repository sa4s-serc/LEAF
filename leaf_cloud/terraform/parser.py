"""
Terraform Configuration Parser Module

This module provides functionality to parse and process Terraform configuration files.
It extracts resources, variables, and outputs from Terraform files and provides
utilities for resolving variable references and processing module configurations.

The main class, TerraformParser, handles the parsing of Terraform configurations
and provides access to the parsed data in a structured format.
"""

# Standard library imports
import collections.abc
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, cast
from collections import defaultdict

# Third-party imports
import hcl2
import yaml
import codecs

# Local application imports
from ..utils.helpers import normalize_block
from .filters import is_non_operational
from ..exceptions import TerraformParseError

"""
terraform/parser.py

This module provides functionality to parse Terraform configuration files,
extract resource definitions and attributes, and handle variable resolution.
It serves as the first step in the LEAF-Cloud model building process by
providing structured Terraform data to the model builder.
"""
# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------
# Matches any Terraform interpolation:  ${ ... }
_INTERP_RE = re.compile(r"\$\{([^}]+)\}")
# Matches only simple  ${var.foo} so we can do a light‑weight replace in
# plan‑json mode without risking complex evaluation
_SIMPLE_VAR_RE = re.compile(r"\$\{var\.([A-Za-z0-9_-]+)\}")
class TerraformParser:
    """
    Parses Terraform configuration files and extracts resources, variables,
    outputs, and other relevant information.
    """

    def __init__(
        self, terraform_path: str, var_files: Optional[List[str]] = None
    ):
        """
        Initialize the TerraformParser with a path to Terraform configurations.

        Args:
            terraform_path: Path to the directory containing Terraform configurations or a plan.json file
            var_files: Optional list of Terraform variable files (.tfvars) to use for resolution
        """
        self.terraform_path = terraform_path
        # Decide parsing mode once; used throughout the instance
        self.is_plan = (
            os.path.isfile(terraform_path) and terraform_path.endswith(".json")
        )
        self.var_files = var_files or []
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.variables: Dict[str, Dict[str, Any]] = {}
        self.outputs: Dict[str, Dict[str, Any]] = {}
        self.modules: Dict[str, Dict[str, Any]] = {}
        self.providers: Dict[str, Dict[str, Any]] = {}  # Added providers dictionary
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)  # Default level, can be changed
        self.logger.info("Parsing Terraform directory: %s", self.terraform_path)
        self.logger.info("Using variable files: %s", self.var_files)

    def parse_all(self) -> Dict[str, Any]:
        """
        Parse all Terraform files in the directory or plan.json file and extract resources, variables, and outputs.

        Returns:
            Dictionary containing parsed Terraform resources, variables, and outputs
        """
        self.logger.info(
            "Starting Terraform parsing for directory: %s", self.terraform_path
        )
        
        # Determine if the path is a directory or a JSON file
        if os.path.isdir(self.terraform_path):
            self._load_variable_definitions()
            self._load_variable_values()  # Loads defaults and then tfvars
            self._load_resources()  # Also loads modules now
            self._load_outputs()
            self._resolve_variables()  # Resolves variables in resources, modules, and outputs
        elif self.is_plan:
            self._parse_terraform_plan_json()
        else:
            raise ValueError(f"Invalid Terraform path: {self.terraform_path}. Must be a directory or a .json file.")

        self.logger.info("Terraform parsing completed.")
        return {
            "resources": self.resources,
            "variables": self.variables,
            "outputs": self.outputs,
            "modules": self.modules,  # Added modules to the output
        }

    def _parse_terraform_plan_json(self) -> None:
        """
        Parse a Terraform plan JSON file and populate self.resources / self.variables / self.outputs.
        Works even if the file has a BOM.
        """
        try:
            if not os.path.exists(self.terraform_path):
                raise FileNotFoundError(f"Terraform plan file not found: {self.terraform_path}")

            if os.path.getsize(self.terraform_path) == 0:
                self.logger.warning("Terraform plan file is empty: %s", self.terraform_path)
                return

            # --- BOM‑aware load ----------------------------------------------------
            import codecs
            raw = Path(self.terraform_path).read_bytes()
            if   raw.startswith(codecs.BOM_UTF8):      text = raw[len(codecs.BOM_UTF8):].decode("utf-8")
            elif raw.startswith(codecs.BOM_UTF16_LE):  text = raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le")
            elif raw.startswith(codecs.BOM_UTF16_BE):  text = raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be")
            elif raw.startswith(codecs.BOM_UTF32_LE):  text = raw[len(codecs.BOM_UTF32_LE):].decode("utf-32-le")
            elif raw.startswith(codecs.BOM_UTF32_BE):  text = raw[len(codecs.BOM_UTF32_BE):].decode("utf-32-be")
            else:                                      text = raw.decode("utf-8")

            plan_data = json.loads(text)
            self.logger.info("Successfully loaded plan.json with BOM-aware loading")

        except json.JSONDecodeError as e:
            self.logger.error("JSON decode error in plan.json: %s", e)
            self.logger.warning("Could not parse plan.json file. Continuing without resources.")
            return
        except Exception as e:
            self.logger.error("Error reading plan.json: %s", e)
            self.logger.warning("Could not parse plan.json file. Continuing without resources.")
            return

        # -------------------------------------------------------------------------
        # Helpers
        # -------------------------------------------------------------------------
        def _skip(res_type: str) -> bool:
            # Use the same centralized logic used by the model builder
            return is_non_operational(res_type)

        def _add_resource(address: str, r_type: str, r_name: str, values: Dict[str, Any]) -> None:
            if _skip(r_type):
                self.logger.debug("Skipping non-operational resource: %s.%s", r_type, r_name)
                return
            res_id = address if address else f"{r_type}.{r_name}"
            self.resources[res_id] = {
                "type":   r_type,
                "name":   r_name,
                "config": values or {},
                "file":   self.terraform_path,
            }
            self.logger.debug("Loaded resource: %s", res_id)

        def _walk_module(module: Dict[str, Any], prefix: str = "") -> None:
            # resources
            for res in module.get("resources", []):
                addr   = res.get("address", "")
                r_type = res.get("type", "")
                r_name = res.get("name", "")
                vals   = res.get("values", {})
                _add_resource(addr or f"{prefix}{r_type}.{r_name}", r_type, r_name, vals)

            # recurse
            for child in module.get("child_modules", []):
                child_prefix = child.get("address", prefix)
                _walk_module(child, prefix=child_prefix + "." if child_prefix else prefix)

        # -------------------------------------------------------------------------
        # Variables
        # -------------------------------------------------------------------------
        if "variables" in plan_data:
            for var_name, var_data in plan_data["variables"].items():
                self.variables[var_name] = {
                    "value": var_data.get("value"),
                    "type":  "string",
                    "file":  self.terraform_path,
                }
                self.logger.debug("Loaded variable: %s", var_name)

        # -------------------------------------------------------------------------
        # Resources (planned_values preferred)
        # -------------------------------------------------------------------------
        if "planned_values" in plan_data and "root_module" in plan_data["planned_values"]:
            _walk_module(plan_data["planned_values"]["root_module"])
        # Fallback: resource_changes (only if nothing found)
        if not self.resources and "resource_changes" in plan_data:
            for res in plan_data["resource_changes"]:
                r_type = res.get("type", "")
                r_name = res.get("name", "")
                after  = res.get("after", {})
                if not after and "change" in res:
                    after = res["change"].get("after", {})
                _add_resource(f"{r_type}.{r_name}", r_type, r_name, after)

        # -------------------------------------------------------------------------
        # Outputs
        # -------------------------------------------------------------------------
        if "output_changes" in plan_data:
            for out_name, out_data in plan_data["output_changes"].items():
                self.outputs[out_name] = {
                    "value":       out_data.get("after"),
                    "description": "",
                    "sensitive":   out_data.get("after_sensitive", False),
                }
                self.logger.debug("Loaded output: %s", out_name)

        self.logger.info("Successfully parsed plan: %d resources, %d variables, %d outputs",
                        len(self.resources), len(self.variables), len(self.outputs))


    def _extract_module_resources(self, modules: List[Dict[str, Any]]) -> None:
        """
        Extract resources from child modules recursively.

        Args:
            modules: List of module configurations
        """
        for module in modules:
            if "resources" in module:
                for resource in module["resources"]:
                    res_type = resource.get("type", "")
                    res_name = resource.get("name", "")
                    res_id = f"{res_type}.{res_name}"

                    # Extract values as the config
                    res_config = resource.get("values", {})

                    # Add module address to the resource ID for uniqueness
                    if "address" in module:
                        module_address = module["address"]
                        res_id = f"{module_address}.{res_id}"

                    self.resources[res_id] = {
                        "type": res_type,
                        "name": res_name,
                        "config": res_config,
                        "file": self.terraform_path,
                    }
                    self.logger.debug(f"Loaded resource from module: {res_id}")

            # Process nested child modules recursively
            if "child_modules" in module:
                self._extract_module_resources(module["child_modules"])

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
            return {
                str(k): self._convert_to_pure_dict(v) for k, v in data.items()
            }
        if isinstance(
            data, collections.abc.Sequence
        ):  # Excludes str due to the check above
            return [self._convert_to_pure_dict(item) for item in data]
        return data  # Primitives, booleans, numbers, None, etc.

    def _load_variable_definitions(self) -> None:
        """
        Load variable definitions from Terraform files.
        """
        self.logger.debug("Starting _load_variable_definitions")
        found_files = self._find_terraform_files()
        self.logger.debug("Files found for variable definitions: %s", found_files)
        for file_path in found_files:
            self.logger.debug("Processing file for variables: %s", file_path)
            try:
                with open(file_path, "r", encoding='utf-8') as tf_file:
                    parsed_hcl = hcl2.load(tf_file)
                    self.logger.debug("hcl2.load output for %s: %s", file_path, parsed_hcl)
                    parsed_dict = self._convert_to_pure_dict(parsed_hcl)
                    self.logger.debug("_convert_to_pure_dict output for %s: %s", file_path, parsed_dict)

                    # Process variable definitions
                    if "variable" in parsed_dict:
                        self.logger.debug("'variable' key found in parsed_dict for %s", file_path)
                        for var_block in parsed_dict["variable"]:
                            for var_name, var_config in var_block.items():
                                if not isinstance(var_config, dict):
                                    self.logger.warning("Skipping malformed variable '%s' in %s", var_name, file_path)
                                    continue

                                # Initialize variable if it doesn't exist
                                if var_name not in self.variables:
                                    self.variables[var_name] = {}

                                # Update with the most specific definition (last one wins)
                                self.variables[var_name].update({
                                    "description": var_config.get("description"),
                                    "type": var_config.get("type", "any"),
                                    "default": var_config.get("default"),
                                    "file": file_path
                                })
                    else:
                        self.logger.debug(
                            "'variable' key NOT found in parsed_dict for %s",
                            file_path
                        )
                self.logger.debug(
                    "Successfully processed variable definitions from %s",
                    file_path
                )
            except (IOError, OSError) as e:
                self.logger.error(
                    "I/O error reading variable definitions from %s: %s",
                    file_path, str(e)
                )
            except (hcl2.parser.ParsingError, hcl2.lexer.LexError) as e:
                self.logger.error(
                    "Failed to parse HCL2 in %s: %s",
                    file_path, str(e)
                )
            except (ValueError, TypeError) as e:
                self.logger.error(
                    "Invalid data format in %s: %s",
                    file_path, str(e)
                )
            except Exception as e:  # pylint: disable=broad-except
                self.logger.error(
                    "Unexpected error processing variable definitions from %s: %s",
                    file_path, str(e),
                    exc_info=True
                )

    def _load_variable_values(self) -> None:
        """
        Load variable values from tfvars files.
        """
        # First, set defaults from variable definitions
        for var_name, var_config in self.variables.items():
            if "default" in var_config and var_config["default"] is not None:
                self.variables[var_name]["value"] = var_config["default"]

        # Then override with values from tfvars files
        tfvars_files_to_load = []
        
        # Recursively find all .tfvars files in the directory tree
        for root, _, files in os.walk(self.terraform_path):
            for file in files:
                if file.endswith(".tfvars") or file.endswith(".tfvars.json"):
                    tfvars_files_to_load.append(os.path.join(root, file))
        
        # Add explicitly specified var files
        if self.var_files:
            for var_file_path in self.var_files:
                if os.path.exists(var_file_path):
                    tfvars_files_to_load.append(var_file_path)
                else:
                    self.logger.warning(
                        "Specified var-file '%s' not found.",
                        var_file_path
                    )
        
        # Sort to ensure consistent processing order
        tfvars_files_to_load.sort()

        for tfvars_file in tfvars_files_to_load:
            try:
                with open(tfvars_file, "r", encoding='utf-8') as f:
                    content = f.read()
                    # Determine if JSON or HCL format for .tfvars
                    if tfvars_file.endswith(".json"):
                        # Using yaml.safe_load for JSON as well, as it's a superset
                        data = yaml.safe_load(content)
                    else:  # Assuming HCL-like format for .tfvars, which hcl2 can parse
                        data = hcl2.loads(content)

                    if data:  # Added check for data to prevent NoneType error
                        for var_name_orig, value in data.items():
                            var_name = str(var_name_orig)  # Ensure string key
                            # Load values from .tfvars files
                            if var_name in self.variables:
                                # Update the existing variable entry with its value
                                self.variables[var_name]["value"] = value
                                self.logger.debug(
                                    "Loaded variable value: %s = %s from %s",
                                    var_name, value, tfvars_file
                                )
                            else:
                                # This case should ideally not happen if variables are defined in .tf files first
                                self.logger.warning(
                                    "Variable '%s' found in %s but not defined in .tf files. Storing with value only.",
                                    var_name, tfvars_file
                                )
                                new_auto_var_entry: Dict[str, Any] = {
                                    "value": value
                                }
                                # Assign with string key
                                self.variables[var_name] = new_auto_var_entry
                    else:
                        self.logger.debug(
                            "No data loaded from tfvars file: %s (it might be empty or comments only)",
                            tfvars_file
                        )
            except (IOError, OSError) as e:
                self.logger.error(
                    "I/O error reading variable values from %s: %s",
                    tfvars_file, str(e)
                )
            except yaml.YAMLError as e:
                self.logger.error(
                    "YAML parsing error in %s: %s",
                    tfvars_file, str(e)
                )
            except (hcl2.parser.ParsingError, hcl2.lexer.LexError) as e:
                self.logger.error(
                    "HCL2 parsing error in %s: %s",
                    tfvars_file, str(e)
                )
            except (ValueError, TypeError) as e:
                self.logger.error(
                    "Invalid data format in %s: %s",
                    tfvars_file, str(e)
                )
            except Exception as e:  # pylint: disable=broad-except
                self.logger.error(
                    "Unexpected error loading variable values from %s: %s",
                    tfvars_file, str(e),
                    exc_info=True
                )

    def _load_resources(self) -> None:
        """
        Load resource definitions from Terraform files.
        """
        self.logger.debug(
            "Starting _load_resources for dir: %s", self.terraform_path
        )
        found_files = self._find_terraform_files()
        self.logger.debug(
            "Files found for resource definitions: %s", found_files
        )
        for file_path in found_files:
            self.logger.debug("Processing file for resources: %s", file_path)
            try:
                with open(file_path, "r", encoding='utf-8') as tf_file:
                    parsed_hcl = hcl2.load(tf_file)
                    self.logger.debug(
                        "hcl2.load output for %s: %s", file_path, parsed_hcl
                    )

                    parsed_dict = self._convert_to_pure_dict(parsed_hcl)
                    self.logger.debug(
                        "_convert_to_pure_dict output for %s: %s", file_path, parsed_dict
                    )

                    # Existing resource parsing logic
                    if "resource" in parsed_dict:
                        self.logger.debug(
                            "'resource' key found in parsed_dict for %s", file_path
                        )
                        for resource_type_dict in parsed_dict["resource"]:
                            for (
                                res_type,
                                resources_in_type,
                            ) in resource_type_dict.items():
                                if not isinstance(resources_in_type, dict):
                                    self.logger.warning(
                                        "Skipping resource type '%s' in %s: value is not a dict.",
                                        res_type, file_path
                                    )
                                    continue
                                for (
                                    res_name,
                                    res_config_list,
                                ) in resources_in_type.items():
                                    # Early filter: skip non-operational resource types
                                    if is_non_operational(res_type):
                                        self.logger.debug(
                                            "Skipping non-operational resource during HCL parse: %s.%s",
                                            res_type,
                                            res_name,
                                        )
                                        continue
                                    res_config = None
                                    if isinstance(res_config_list, list) and len(res_config_list) > 0:
                                        res_config = res_config_list[0]
                                        if not isinstance(res_config, dict):
                                            self.logger.warning(
                                                "Skipping resource '%s.%s' in %s: "
                                                "config item is not a dict.",
                                                res_type, res_name, file_path
                                            )
                                            continue
                                    elif isinstance(res_config_list, dict):
                                        res_config = res_config_list
                                    else:
                                        self.logger.warning(
                                            "Skipping resource '%s.%s' in %s: "
                                            "config is not a list or dict.",
                                            res_type, res_name, file_path
                                        )
                                        continue

                                    normalized_name = cast(
                                        str,
                                        normalize_block(
                                            "%s.%s" % (res_type, res_name)
                                        ),
                                    )
                                    self.resources[normalized_name] = {
                                        "type": res_type,
                                        "name": res_name,
                                        "config": res_config,  # Store raw config, resolution happens later
                                        "file": file_path,
                                    }
                                    self.logger.debug(
                                        "Loaded resource definition: %s from %s",
                                        normalized_name, file_path
                                    )
                    else:
                        self.logger.debug(
                            "'resource' key NOT found in parsed_dict for %s",
                            file_path
                        )

                    # Add module parsing logic
                    if "module" in parsed_dict:
                        self.logger.debug(
                            "'module' key found in parsed_dict for %s",
                            file_path
                        )
                        # 'module' is a list of dicts, each dict is a module block
                        for module_block_list_item in parsed_dict["module"]:
                            for (
                                module_name,
                                module_config_container,
                            ) in module_block_list_item.items():
                                # module_config_container is usually a list containing one dict (the actual module config)
                                module_config = None
                                if (
                                    isinstance(module_config_container, list)
                                    and len(module_config_container) > 0
                                ):
                                    module_config = module_config_container[0]
                                # Less common, but handle if it's a direct dict
                                elif isinstance(module_config_container, dict):
                                    module_config = module_config_container

                                if not isinstance(module_config, dict):
                                    self.logger.warning(
                                        "Skipping malformed module '%s' in %s: config item is not a dict or not found correctly." % (module_name, file_path)
                                    )
                                    continue

                                module_source = module_config.get(
                                    "source", "Unknown source"
                                )
                                self.modules[module_name] = {
                                    "config": module_config,  # Store the actual config block
                                    "file": file_path,
                                    "source": module_source,
                                    "name": module_name,
                                }
                                self.logger.debug(
                                    "Loaded module definition: %s from %s with source '%s'",
                                    module_name, file_path, module_source
                                )
                    else:
                        self.logger.debug(
                            "'module' key NOT found in parsed_dict for %s", file_path
                        )

                self.logger.debug(
                    "Successfully processed definitions from %s", file_path
                )
            except Exception as e:
                self.logger.error(
                    "Error parsing definitions from %s: %s",
                    file_path, e,
                    exc_info=True
                )

    def _load_outputs(self) -> None:
        """
        Load output definitions from Terraform files.
        
        This method iterates through all Terraform files in the configured directory,
        parses their contents, and extracts output definitions for later use.
        """
        for file_path in self._find_terraform_files():
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    parsed = hcl2.load(f)
                    parsed = self._convert_to_pure_dict(parsed)
                    self.logger.debug(
                        "_convert_to_pure_dict output for %s: %s",
                        file_path, json.dumps(parsed, indent=2)
                    )

                    if "output" in parsed:
                        self.logger.debug(
                            "'output' key found in parsed_dict for %s", file_path
                        )
                        # The 'output' key contains a list of dictionaries,
                        # each representing an output block with its name as key.
                        for output_block_dict in parsed["output"]:
                            for (
                                output_name,
                                output_config_list,
                            ) in output_block_dict.items():
                                # output_config_list is usually a list containing one dict (the actual config)
                                if (
                                    isinstance(output_config_list, list)
                                    and len(output_config_list) > 0
                                ):
                                    output_config = output_config_list[0]
                                    if not isinstance(output_config, dict):
                                        self.logger.warning(
                                            "Skipping malformed output '%s' in %s: config item is not a dict.",
                                            output_name, file_path
                                        )
                                        continue
                                # Should ideally be a list, but handle if it's a direct dict
                                elif isinstance(output_config_list, dict):
                                    output_config = output_config_list
                                else:
                                    self.logger.warning(
                                        "Skipping malformed output '%s' in %s: config is not a list or dict.",
                                        output_name, file_path
                                    )
                                    continue

                                self.outputs[output_name] = {
                                    "value": output_config.get("value"),
                                    "description": output_config.get(
                                        "description"
                                    ),
                                    "sensitive": output_config.get(
                                        "sensitive", False
                                    ),
                                }
                                self.logger.debug(
                                    "Loaded output '%s' from %s",
                                    output_name, file_path
                                )
                    else:
                        self.logger.debug(
                            "'output' key NOT found in parsed_dict for %s", file_path
                        )
                self.logger.debug(
                    "Loaded outputs from %s", file_path
                )
            except Exception as e:
                self.logger.error(
                    "Error parsing %s: %s", file_path, str(e)
                )
                self.logger.debug(
                    "Error details:", exc_info=True
                )

    def _find_terraform_files(self) -> List[str]:
        """
        Find all Terraform (*.tf) files in the configured directory.
        No‑op for plan‑json mode.
        """
        if self.is_plan:        # plan.json already loaded; no .tf files needed
            return []
        tf_files: List[str] = []
        for root, _, files in os.walk(self.terraform_path):
            for file in files:
                if file.endswith(".tf"):
                    tf_files.append(os.path.join(root, file))
        return tf_files


    def _resolve_variables(self) -> None:
        """
        Resolve variable references in resource configurations.
        
        This method iterates through all loaded resources and resolves any variable
        references in their configurations using the loaded variable values.
        """
        # ---------------------------------------------------------------------
        # Build a quick lookup table:  type -> name -> config
        # This is used by the HCL (directory) resolver for ${google_x.y.attr}
        # references.
        # ---------------------------------------------------------------------
        self._resource_index: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        for res in self.resources.values():
            self._resource_index[res["type"]][res["name"]] = res.get("config", {})

        # Resolve resource configurations
        for res_id, res_data in self.resources.items():
            res_config = res_data["config"]
            resolved_config = self._resolve_block(
                res_config, res_data.get("file")
            )
            self.resources[res_id]["config"] = resolved_config
            self.logger.debug("Resolved variables in resource %s", res_id)

        # Resolve module configurations
        for module_name, module_data in self.modules.items():
            if "config" in module_data and isinstance(
                module_data["config"], dict
            ):
                module_config_val = module_data["config"]
                resolved_module_config = self._resolve_block(
                    module_config_val, module_data.get("file")
                )
                self.modules[module_name]["config"] = resolved_module_config
                self.logger.debug("Resolved variables in module %s", module_name)


        # Resolve output values (skip for plan mode – values are already final)
        if not self.is_plan:
            self._resolve_variables_in_outputs()

    def _resolve_variables_in_outputs(self) -> None:
        """
        Resolve variable references in output values.
        
        This method iterates through all loaded outputs and resolves any variable
        references in their values using the loaded variable values.
        """
        for output_name, output_data in self.outputs.items():
            if "value" in output_data:
                output_value = output_data["value"]
                if isinstance(output_value, str):
                    # Try to resolve variable references in the output value
                    resolved_value = self._resolve_value(
                        output_value, context_resource_name=None
                    )
                    self.outputs[output_name]["value"] = resolved_value
                    self.logger.debug(
                        "Resolved variables in output %s", output_name
                    )

    def _resolve_block(
        self,
        block: Dict[str, Any],
        context_resource_name: Optional[str] = None,
        visited: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Recursively resolve variable references in a block.
        
        Args:
            block: The block to resolve
            context_resource_name: Optional name of the resource for context
            visited: Set of already visited references to prevent infinite recursion
            
        Returns:
            The resolved block
        """
        if visited is None:
            visited = set()

        if not isinstance(block, dict):
            return block

        resolved_block = {}
        for key, value in block.items():
            resolved_block[key] = self._resolve_value(
                value, context_resource_name, visited
            )

        return resolved_block

    def _resolve_list(
        self,
        lst: List[Any],
        context_resource_name: Optional[str] = None,
        visited: Optional[Set[str]] = None,
    ) -> List[Any]:
        """
        Recursively resolve variable references in a list.
        
        Args:
            lst: The list to resolve
            context_resource_name: Optional name of the resource for context
            visited: Set of already visited references to prevent infinite recursion
            
        Returns:
            The resolved list
        """
        if visited is None:
            visited = set()

        resolved_list = []
        for item in lst:
            resolved_list.append(
                self._resolve_value(item, context_resource_name, visited)
            )

        return resolved_list

    def _resolve_value(
        self,
        value: Any,
        context_resource_name: Optional[str] = None,
        visited: Optional[Set[str]] = None,
    ) -> Any:
        """
        Resolve a value that might contain variable references.
        
        Args:
            value: The value to resolve
            context_resource_name: Optional name of the resource for context
            visited: Set of already visited references to prevent infinite recursion
            
        Returns:
            The resolved value
        """
        if visited is None:
            visited = set()

        # Handle different types of values
        if isinstance(value, dict):
            return self._resolve_block(value, context_resource_name, visited)
        elif isinstance(value, list):
            return self._resolve_list(value, context_resource_name, visited)
        elif isinstance(value, str):
            # Fast path for plan‑json: only ${var.foo}
            if self.is_plan:
                return _SIMPLE_VAR_RE.sub(
                    lambda m: str(self.variables.get(m.group(1), {}).get("value", m.group(0))),
                    value,
                )

            # Full interpolation resolver for HCL directories
            def _replace(match_obj):
                expr = match_obj.group(1).strip()
                resolved = self._eval_expr(expr, visited)
                return str(resolved) if not isinstance(resolved, (dict, list)) else json.dumps(resolved)

            return _INTERP_RE.sub(_replace, value)
        else:
            # For other types (int, float, bool, None), return as is
            return value
    
    # ---------------------------------------------------------------------
    # Very small expression evaluator for HCL‐mode interpolations
    # ---------------------------------------------------------------------
    def _eval_expr(self, expr: str, visited: Set[str]) -> Any:
        """
        Supports:
          * var.xxx
          * resource_type.resource_name.attr[.subattr...]
        Unknown expressions are returned unchanged.
        """
        # Guard against infinite recursion
        if expr in visited:
            self.logger.warning("Circular reference while resolving %s", expr)
            return "${" + expr + "}"
        visited.add(expr)

        parts = expr.split(".")
        head = parts[0]

        # ------------------  variables  ------------------
        if head == "var" and len(parts) >= 2:
            var_name = parts[1]
            return self.variables.get(var_name, {}).get("value", "${" + expr + "}")

        # ------------------  resources  ------------------
        # e.g. google_sql_database_instance.db.id
        if head in self._resource_index and len(parts) >= 3:
            res_name = parts[1]
            attr_path = parts[2:]
            cfg = self._resource_index.get(head, {}).get(res_name)
            if cfg is None:
                return "${" + expr + "}"
            # Walk the attribute path
            cur: Any = cfg
            for p in attr_path:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:  # path dead‑ends
                    return "${" + expr + "}"
            return cur

        # ------------------  fallback  ------------------
        return "${" + expr + "}"
    def get_all_resources(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all parsed resources.
        
        Returns:
            Dictionary containing all parsed resources
        """
        return self.resources