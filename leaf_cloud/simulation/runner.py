"""Simulation runner module for LEAF-Cloud.

This module provides the core functionality for running simulations in the LEAF-Cloud framework.
It connects the CLI interface to the actual simulation engine and handles the execution flow.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TypeVar, Type

from ..exceptions import LeafCloudError, SimulationError, ValidationError
from ..config import LEAFCloudConfig
# Lazy import to avoid circular dependency
# from ..leaf import LEAFCloud

# Initialize module logger
logger = logging.getLogger(__name__)

def run_simulation(
    terraform_dir: Union[str, Path],
    config_path: Optional[Union[str, Path]] = None,
    workload_config: Optional[Dict[str, Any]] = None,
    duration: Optional[float] = None,
    iterations: Optional[int] = None,
    output_dir: Optional[Union[str, Path]] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """Run a simulation with the given configuration.
    
    Args:
        terraform_dir: Path to the directory containing Terraform files.
        config_path: Optional path to the LEAF-Cloud configuration file.
        workload_config: Optional configuration for the workload generator.
        duration: Optional simulation duration in seconds.
        iterations: Optional number of simulation iterations.
        output_dir: Optional directory to save simulation results.
        **kwargs: Additional simulation parameters.
        
    Returns:
        A dictionary containing the simulation results.
        
    Raises:
        SimulationError: If the simulation fails.
    """
    start_time = time.time()
    
    try:
        # Initialize LEAF-Cloud framework (lazy import)
        from ..leaf import LEAFCloud
        logger.info("Initializing LEAF-Cloud framework")
        framework = LEAFCloud(config_path=config_path)
        
        # Load Terraform configuration
        logger.info(f"Loading Terraform configuration from {terraform_dir}")
        framework.load_terraform(terraform_dir)
        
        # Build the model
        logger.info("Building simulation model")
        framework.build_model()
        
        # Configure workload if provided
        if workload_config:
            logger.info(f"Configuring workload: {workload_config}")
            framework.configure_workload(workload_config)
        
        # Set simulation parameters
        sim_params = {}
        if duration is not None:
            sim_params['duration'] = duration
        if iterations is not None:
            sim_params['iterations'] = iterations
        
        # Add latency settings if provided
        if kwargs.get('latency_stochastic'):
            # Update the framework's config directly for latency settings
            latency_config = framework.config.models.latency
            latency_config.stochastic_variation['enabled'] = True
            latency_config.stochastic_variation['distribution'] = kwargs.get('latency_distribution', 'uniform')
            
            # Set distribution parameters
            if kwargs.get('latency_distribution') == 'uniform':
                latency_config.stochastic_variation['params']['uniform'].update({
                    'min': kwargs.get('latency_uniform_min', -0.1),
                    'max': kwargs.get('latency_uniform_max', 0.1)
                })
            elif kwargs.get('latency_distribution') == 'normal':
                latency_config.stochastic_variation['params']['normal'].update({
                    'mean': kwargs.get('latency_normal_mean', 0.0),
                    'stddev': kwargs.get('latency_normal_stddev', 0.05)
                })
            
            # Set resource types to apply latency to
            latency_config.stochastic_variation['apply_to'] = kwargs.get('latency_apply_to', ['all'])
            
            logger.info(f"Enabled stochastic latency with {kwargs.get('latency_distribution', 'uniform')} distribution")
        
        # Add end-to-end latency settings if provided
        if kwargs.get('end_to_end_latency'):
            # Update the framework's config directly for end-to-end latency settings
            latency_config = framework.config.models.latency
            latency_config.end_to_end_enabled = True
            
            # Set user distribution if provided
            user_distribution = kwargs.get('user_distribution')
            if user_distribution:
                latency_config.user_distribution = user_distribution
                logger.info(f"Configured user distribution: {user_distribution}")
            
            logger.info("Enabled end-to-end latency modeling")
        
        # Add any additional parameters
        for key, value in kwargs.items():
            if key not in ('terraform_dir', 'config_path', 'workload_config', 'output_dir', 
                          'latency_stochastic', 'latency_distribution', 'latency_uniform_min', 
                          'latency_uniform_max', 'latency_normal_mean', 'latency_normal_stddev', 
                          'latency_apply_to', 'end_to_end_latency', 'user_distribution'):
                sim_params[key] = value
        
        # Run the simulation
        logger.info(f"Running simulation with parameters: {sim_params}")
        results = framework.run_simulation(**sim_params)
        
        # Process and return results
        if isinstance(results, list) and results:
            # If we have a list of results, return the last one
            result = results[-1]
            
            # Save results to output directory if specified
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                result_file = output_path / f"simulation_results_{timestamp}.json"
                
                # Export the result
                if hasattr(result, 'export') and callable(result.export):
                    result_filepath = result.export(result_file, fmt='json')
                    logger.info(f"Results saved to {result_filepath}")
                else:
                    # Fallback if export method is not available
                    try:
                        if hasattr(result, 'to_dict') and callable(result.to_dict):
                            result_dict = result.to_dict()
                        elif hasattr(result, '__dict__'):
                            result_dict = result.__dict__
                        else:
                            result_dict = {"result": str(result)}
                            
                        with open(result_file, 'w') as f:
                            json.dump(result_dict, f, indent=2, default=str)
                        logger.info(f"Results saved to {result_file}")
                    except Exception as e:
                        logger.error(f"Failed to save results: {e}")
            
            # Convert result to dictionary if needed
            if hasattr(result, 'to_dict') and callable(result.to_dict):
                return result.to_dict()
            elif hasattr(result, '__dict__'):
                return result.__dict__
            else:
                return {"result": str(result)}
        elif isinstance(results, dict):
            # If results is already a dictionary, return it directly
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                result_file = output_path / f"simulation_results_{timestamp}.json"
                
                try:
                    with open(result_file, 'w') as f:
                        json.dump(results, f, indent=2, default=str)
                    logger.info(f"Results saved to {result_file}")
                except Exception as e:
                    logger.error(f"Failed to save results: {e}")
                    
            return results
        elif hasattr(results, 'to_dict') and callable(results.to_dict):
            # If results has a to_dict method, use it
            return results.to_dict()
        elif hasattr(results, '__dict__'):
            # If results has a __dict__ attribute, use it
            return results.__dict__
        else:
            # Fallback for any other type
            logger.warning(f"Unexpected result type: {type(results)}")
            return {
                "status": "completed", 
                "result": str(results),
                "workload_config": workload_config,
                "parameters": sim_params,
                "timestamp": datetime.now().isoformat()
            }
            
    except Exception as e:
        elapsed_time = time.time() - start_time
        error_msg = f"Simulation failed after {elapsed_time:.2f} seconds: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise SimulationError(error_msg) from e