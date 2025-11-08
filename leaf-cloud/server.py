import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import yaml
import time
from .leaf_cloud import create_framework
from .main import cleanup, analyze_results as cli_analyze
import argparse
import logging
logging.getLogger("leaf_cloud").setLevel(logging.CRITICAL)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
def generate_recommendations(results):
    """
    Analyzes simulation results and generates recommendations.
    NOTE: This is a placeholder to demonstrate the functionality.
    You should replace this with your actual recommendation logic from the CLI.
    """
    recommendations = []
    metrics_summary = results.get('metrics_summary', {})

    # Recommendation for high latency
    latency = metrics_summary.get('latency', {})
    p95_latency = latency.get('p95')
    if p95_latency is not None and p95_latency > 1000:
        recommendations.append({
            'type': 'performance',
            'severity': 'high',
            'title': 'High Latency Detected',
            'description': f"The p95 latency is {p95_latency:.2f}ms, which is above the 1000ms threshold. "
                           "Consider scaling up your compute resources or optimizing your application code to improve response times."
        })

    # Recommendation for high carbon emissions
    carbon = metrics_summary.get('carbon', {})
    total_co2 = carbon.get('total_kg_co2')
    if total_co2 is not None and total_co2 > 10:
        recommendations.append({
            'type': 'sustainability',
            'severity': 'medium',
            'title': 'High Carbon Emissions',
            'description': f"The simulation predicts {total_co2:.2f} kg CO₂ emissions. "
                           "Consider choosing a cloud region with a lower carbon footprint or scheduling workloads during times of high renewable energy availability."
        })

    # Recommendations for utilization
    scaling = metrics_summary.get('scaling', {})
    avg_utilization = scaling.get('average_utilization')
    if avg_utilization is not None:
        if avg_utilization < 0.2:
            recommendations.append({
                'type': 'cost',
                'severity': 'low',
                'title': 'Low Resource Utilization',
                'description': f"Average resource utilization is only {avg_utilization*100:.2f}%. "
                               "You might be over-provisioned. Consider using smaller instance sizes or a more aggressive autoscaling policy to reduce costs."
            })
        elif avg_utilization > 0.8:
            recommendations.append({
                'type': 'reliability',
                'severity': 'high',
                'title': 'High Resource Utilization',
                'description': f"Average resource utilization is high at {avg_utilization*100:.2f}%. "
                               "This could lead to performance degradation or service outages under load. Consider scaling up your resources."
            })

    # --- START OF MODIFICATION ---
    if not recommendations:
        # If no specific issues were found, provide a summary of the healthy metrics.
        latency_summary = metrics_summary.get('latency', {})
        carbon_summary = metrics_summary.get('carbon', {})
        energy_summary = metrics_summary.get('energy', {})
        scaling_summary = metrics_summary.get('scaling', {})

        avg_latency = latency_summary.get('average')
        total_co2 = carbon_summary.get('total_kg_co2')
        total_kwh = energy_summary.get('total_kwh')
        avg_util = scaling_summary.get('average_utilization')

        summary_points = []
        if avg_latency is not None:
            summary_points.append(f"• Average Latency: {avg_latency:.2f}ms")
        if total_co2 is not None:
            summary_points.append(f"• Carbon Emissions: {total_co2:.2f} kg CO₂")
        if total_kwh is not None:
            summary_points.append(f"• Energy Consumption: {total_kwh:.2f} kWh")
        if avg_util is not None:
            summary_points.append(f"• Average Utilization: {avg_util*100:.2f}%")
            
        description = "Your infrastructure seems to be well-configured for the simulated workload. Here's a summary of the key metrics:\n\n" + "\n".join(summary_points)

        recommendations.append({
            'type': 'general',
            'severity': 'none',
            'title': 'All Good!',
            'description': description
        })
    # --- END OF MODIFICATION ---
    return recommendations


@app.route('/version', methods=['GET'])
def version():
    return jsonify({
        "version": "1.0.0",
        "build_date": "2025-04-21",
        "description": "Layered Eco-centric Analytical Framework for Cloud Infrastructure"
    })

@app.route('/simulate', methods=['POST'])
def simulate():
    """Runs a simulation based on Terraform files."""
    data = request.get_json() or {}
    terraform = data.get('terraform')
    if not terraform:
        return jsonify({"error": "Terraform path ('terraform') is required"}), 400
    config = data.get('config')
    var_files = data.get('var_files')
    workload_rate = data.get('workload_rate', 100.0)
    duration = data.get('duration', 3600.0)
    queue_factor = data.get('queue_factor', 1.0)
    output = data.get('output')

    try:
        framework = create_framework(config)
        framework.load_terraform(terraform, var_files)
        
        # Support for different workload types
        workload_type = data.get('workload_type', 'steady')
        workload_params = data.get('workload_params', {'rate': workload_rate})
        
        # Ensure backward compatibility with workload_rate parameter
        if workload_type == 'steady' and 'rate' not in workload_params:
            workload_params['rate'] = workload_rate
            
        framework.configure_workload({
            "type": workload_type,
            "params": workload_params,
            "duration": duration
        })
        petri_net, model = framework.build_model(workload_rate=workload_rate)

        # ---> START OF MODIFICATION <---
        # Add a check here to ensure the model is not empty.
        # An empty model means Terraform parsing likely failed silently.
        if not model.resource_mapping:
            app.logger.error("Simulation model is empty. No resources were found to simulate. "
                             "Check if the Terraform path is correct and accessible by the server.")
            return jsonify({
                "error": "Simulation model is empty.",
                "details": "This usually means the server failed to parse the Terraform files. "
                           "Please check the server logs for the full error."
            }), 400

        if queue_factor != 1.0:
            app.logger.info(f"Applying queue factor {queue_factor}")
            for resource in model.resource_mapping.values():
                resource.capacity *= queue_factor

        app.logger.info(f"Running simulation for {duration}s with workload rate {workload_rate}")
        results = framework.run_simulation(duration)

        # ... (rest of your function)

        fields_to_keep = [
            "simulation_info", "workload_stats", "resource_utilization",
            "latency_metrics", "energy_metrics", "carbon_metrics", 
            "scaling_metrics", "processed_metrics", "resource_carbon_footprint"
            ]
        lean_results = {key: results[key] for key in fields_to_keep if key in results}
        for key in list(results.keys()):
            if key not in fields_to_keep:
                del results[key]

        if output:
            output_file = framework.export_results(output)
        else:
            output_file = None

        metrics_summary = framework.get_metrics_summary()

        # 1. Carbon
        metrics_summary['carbon']['total_kg_co2'] = metrics_summary['carbon'].pop('total_co2eq_kg')

        # 2. Scaling
        scaling = metrics_summary.get('scaling', {})
        scaling['average_utilization'] = scaling.pop('average_utilization', None)  # if your aggregator already gives it
        scaling['max_pods']           = scaling.pop('max_replicas', 0)
        scaling['scaling_events']     = scaling.pop('autoscaling_events', 0)

        return jsonify({"results": lean_results, "metrics_summary": metrics_summary, "output_file": output_file})

    except Exception as e:
        app.logger.error(f"Simulation failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Simulation failed", "details": str(e)}), 500

@app.route('/analyze', methods=['POST'])
def analyze():
    """Analyzes an existing simulation results file."""
    data = request.get_json() or {}
    results_path = data.get('results')
    metrics = data.get('metrics', ['all'])
    if not results_path:
        return jsonify({"error": "results path is required"}), 400
    try:
        if results_path.endswith('.json'):
            app.logger.info(f"Loading results from JSON: {results_path}")
            with open(results_path) as f:
                results = json.load(f)
        elif results_path.endswith(('.yaml', '.yml')):
            with open(results_path) as f:
                results = yaml.safe_load(f)
        else:
            return jsonify({"error": "Unsupported file format"}), 400

        if not results:
             return jsonify({"error": f"Could not load or parse results file: {results_path}"}), 400

        analysis = {}
        # Basic simulation info
        sim_info = results.get('simulation_info', {})
        analysis['simulation_info'] = sim_info

        # Metrics analysis
        if 'all' in metrics or 'latency' in metrics:
            if 'latency_metrics' in results:
                analysis['latency'] = results['latency_metrics']
        if 'all' in metrics or 'energy' in metrics:
            if 'energy_metrics' in results:
                analysis['energy'] = results['energy_metrics']
        if 'all' in metrics or 'carbon' in metrics:
            if 'carbon_metrics' in results:
                analysis['carbon'] = results['carbon_metrics']
        if 'all' in metrics or 'scaling' in metrics:
            if 'scaling_metrics' in results:
                analysis['scaling'] = results['scaling_metrics']

        return jsonify(analysis)
    except Exception as e:
        app.logger.error(f"Analysis failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Analysis failed", "details": str(e)}), 500

@app.route('/export/simulation', methods=['POST'])
def export_simulation():
    """Runs a simulation and exports results, or exports existing results."""
    data = request.get_json() or {}
    terraform = data.get('terraform')
    output = data.get('output')
    fmt = data.get('format', 'json')
    workload_rate = data.get('workload_rate', 100.0)
    if not terraform and not output:
        return jsonify({"error": "Either terraform or output must be specified"}), 400

    try:
        framework = create_framework(None) # Assuming no separate config for export run
        if terraform:
            app.logger.info(f"Running simulation for export from: {terraform}")
            framework.load_terraform(terraform)
            framework.configure_workload({"type": "steady", "params": {"rate": workload_rate}})
            _, model = framework.build_model()
            # model.export_model("model.json") # Optionally export intermediate model
            results = framework.run_simulation() # Use default duration

            # Determine output path
            if not output:
                timestamp = int(time.time())
                output_dir = framework.orchestrator.config.output_dir
                os.makedirs(output_dir, exist_ok=True)
                output = os.path.join(output_dir, f"simulation_results_{timestamp}.{fmt}")

            # Export results in the requested format
            output_file = framework.export_results(output, file_format=fmt)

            # Load content to return in response
            if fmt == 'json':
                with open(output_file) as f:
                    content = json.load(f)
            elif fmt == 'yaml':
                with open(output_file) as f:
                    content = yaml.safe_load(f)
            else:
                 # Should not happen if export_results validates format
                 return jsonify({"error": f"Format {fmt} not supported"}), 400

            return jsonify({"output_file": output_file, "content": content})
        else:
            # TODO: Implement exporting existing results file if needed
            app.logger.warning("Exporting existing results is not implemented yet.")
            return jsonify({"error": "Exporting existing results not implemented"}), 501
    except Exception as e:
        app.logger.error(f"Export failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Export failed", "details": str(e)}), 500


@app.route('/intermediate', methods=['POST'])
def intermediate_model():
    """Builds and exports the intermediate model from Terraform files."""
    data = request.get_json() or {}
    terraform = data.get('terraform')
    if not terraform:
        return jsonify({"error": "Terraform path ('terraform') is required"}), 400
    config = data.get('config')
    var_files = data.get('var_files')
    output = data.get('output', 'parsed_infrastructure.json') # Default output filename

    try:
        framework = create_framework(config)
        framework.load_terraform(terraform, var_files)
        _, model = framework.build_model() # Build model to get ModelBuilder instance
        model = model.export_tf_resources(output) # Export the intermediate model
        app.logger.info(f"Intermediate model exported to {output}")
        return jsonify({"output_file": output, "model": model})
    except Exception as e:
        app.logger.error(f"Intermediate model generation failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Intermediate model generation failed", "details": str(e)}), 500

@app.route('/diagram/class', methods=['POST'])
def class_diagram():
    """Generates a UML class diagram."""
    data = request.get_json() or {}
    terraform = data.get('terraform')
    output_dir = data.get('output', 'diagrams')
    if not terraform:
        return jsonify({"error": "terraform path is required"}), 400
    framework = create_framework(None)
    framework.load_terraform(terraform)
    framework.build_model()
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"class_diagram_{int(time.time())}.png")
    framework.generate_visualization('class_diagram', path)
    return jsonify({'diagram_file': path})

@app.route('/diagram/deployment', methods=['POST'])
def deployment_diagram():
    """Generates a deployment diagram."""
    data = request.get_json() or {}
    terraform = data.get('terraform')
    output_dir = data.get('output', 'diagrams')
    if not terraform:
        return jsonify({"error": "terraform path is required"}), 400
    framework = create_framework(None)
    framework.load_terraform(terraform)
    framework.build_model()
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"deployment_diagram_{int(time.time())}.png")
    framework.generate_visualization('deployment_diagram', path)
    return jsonify({'diagram_file': path})

@app.route('/recommendations', methods=['POST'])
def recommendations_endpoint():
    """Generates recommendations based on simulation results."""
    data = request.get_json() or {}
    results = data.get('results')
    if not results:
        return jsonify({"error": "simulation results are required"}), 400
    try:
        recommendations_list = generate_recommendations(results)
        return jsonify({"recommendations": recommendations_list})
    except Exception as e:
        app.logger.error(f"Recommendation generation failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Recommendation generation failed", "details": str(e)}), 500


@app.route('/clean', methods=['POST'])
def clean_endpoint():
    """Cleans up generated results, diagrams, and intermediate files."""
    cleanup()
    return jsonify({"status": "cleaned"})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Layered Eco-centric Analytical Framework for Cloud Infrastructure')
    parser.add_argument('--port', type=int, default=5000, help='Port to run the server on')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    parser.add_argument('--host', type=str, default='0.0.0.0')

    args = parser.parse_args()

    print(f"Starting server on {args.host}:{args.port} with debug={args.debug}")
    app.run(host=args.host, port=args.port, debug=args.debug)