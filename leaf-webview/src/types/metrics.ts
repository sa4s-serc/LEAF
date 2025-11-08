export interface CarbonMetrics {
  by_resource_type: Record<string, number>;
  total_kg_co2: number;
}

export interface EnergyMetrics {
  by_resource_type: Record<string, number>;
  total_kwh: number;
}

export interface LatencyMetrics {
  average: number;
  p95: number;
  p99: number;
}

export interface ScalingMetrics {
  average_utilization: number;
  max_pods: number;
  scaling_events: number;
}

export interface MetricsSummary {
  carbon: CarbonMetrics;
  energy: EnergyMetrics;
  latency: LatencyMetrics;
  scaling: ScalingMetrics;
}

export interface ResourceUtilization {
  avg: number;
  max: number;
  min: number;
}

// --- START OF MODIFICATION ---
export interface ResourceCarbonFootprint {
  total_kg_co2e: number;
  avg_emission_rate_kg_co2e_per_hour: number;
  max_emission_rate_kg_co2e_per_hour_interval: number;
  min_emission_rate_kg_co2e_per_hour_interval: number;
}
// --- END OF MODIFICATION ---

export interface SimulationResults {
  metrics_summary: MetricsSummary;
  results: {
    carbon_metrics: {
      regional_emissions: Record<string, number>;
      resource_emissions: Record<string, number>;
      total_carbon_emissions: number;
    };
    // --- START OF MODIFICATION ---
    resource_carbon_footprint: Record<string, ResourceCarbonFootprint>;
    // --- END OF MODIFICATION ---
    energy_metrics: {
      by_resource_category: Record<string, number>;
      by_resource_type: Record<string, number>;
      resource_energy: Record<string, number>;
      total_kwh: number;
    };
    latency_metrics: {
      average: number;
      latencies: number[];
      percentile_95: number;
      percentile_99: number;
    };
    resource_utilization: Record<string, ResourceUtilization>;
    scaling_metrics: ScalingMetrics;
    simulation_info: {
      active_tokens: number;
      completed_tokens: number;
      duration: number;
      execution_time: number;
      simulation_time_reached: number;
      total_tokens: number;
    };
    workload_stats: Record<string, {
      avg_rate: number;
      duration_seconds: number;
      max_rate: number;
      min_rate: number;
      std_dev_rate: number;
      tokens_per_second: number;
      total_tokens: number;
    }>;
  };
}