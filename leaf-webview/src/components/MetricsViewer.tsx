import { Grid, Box } from '@mui/material';
import { SimulationResults } from '../types/metrics';
import ResourceMetricsCard from './ResourceMetricsCard';
import PerformanceMetricsCard from './PerformanceMetricsCard';
import UtilizationChart from './UtilizationChart';
import SkeletonChart from './SkeletonChart';
import AnalysisSummary from './AnalysisSummary';

interface MetricsViewerProps {
  data: SimulationResults;
  loading?: boolean;
}

export default function MetricsViewer({ data, loading = false }: MetricsViewerProps) {
  const { metrics_summary, results } = data;

  console.log('MetricsViewer rendered:', { loading, hasData: !!data });

  const durationInHours = results.simulation_info.duration / 3600;
  const totalCarbonKgPerHour = durationInHours > 0 ? metrics_summary.carbon.total_kg_co2 / durationInHours : 0;
  const totalCarbonGramsPerHour = totalCarbonKgPerHour * 1000;

  const resourceCarbonGramsPerHourData =
    results.resource_carbon_footprint
      ? Object.entries(results.resource_carbon_footprint).reduce((acc, [name, metrics]) => {
          acc[name] = metrics.avg_emission_rate_kg_co2e_per_hour * 1000;
          return acc;
        }, {} as Record<string, number>)
      : {};

  const resourceEnergyWattsData =
    results.energy_metrics.resource_energy && durationInHours > 0
      ? Object.entries(results.energy_metrics.resource_energy).reduce((acc, [name, kwh]) => {
          acc[name] = (kwh / durationInHours) * 1000; // Convert kWh to average Watts
          return acc;
        }, {} as Record<string, number>)
      : {};

  return (
    <Box>
      {/* Analysis Summary */}
      {!loading && <AnalysisSummary data={data} />}

      <Grid container spacing={3}>
        {/* Carbon Metrics */}
        <Grid item xs={12} lg={6}>
          <ResourceMetricsCard
            title="Carbon Emissions"
            total={totalCarbonGramsPerHour}
            unit="g CO₂e/hour"
            data={resourceCarbonGramsPerHourData}
            loading={loading}
          />
        </Grid>

        {/* Energy Metrics */}
        <Grid item xs={12} lg={6}>
          <ResourceMetricsCard
            title="Energy Consumption"
            total={metrics_summary.energy.total_kwh}
            unit="kWh"
            data={resourceEnergyWattsData}
            loading={loading}
            durationInHours={durationInHours}
          />
        </Grid>

        {/* Resource Utilization Chart */}
        <Grid item xs={12}>
          <UtilizationChart 
            resourceUtilization={results.resource_utilization}
            loading={loading}
          />
        </Grid>

        {/* Performance Metrics card is now commented out as requested */}
        {/*
        <Grid item xs={12} md={6}>
          {loading ? (
            <SkeletonChart />
          ) : (
            <PerformanceMetricsCard
              title="Latency Metrics"
              metrics={{
                average: metrics_summary.latency.average,
                p95: metrics_summary.latency.p95,
                p99: metrics_summary.latency.p99
              }}
              units={{
                average: 'ms',
                p95: 'ms',
                p99: 'ms'
              }}
            />
          )}
        </Grid>
        */}


        {/* Scaling Metrics */}
        <Grid item xs={12} md={6}>
          {loading ? (
            <SkeletonChart />
          ) : (
            <PerformanceMetricsCard
              title="Scaling Metrics"
              metrics={{
                average_utilization: metrics_summary.scaling.average_utilization * 100,
                max_pods: metrics_summary.scaling.max_pods,
                scaling_events: metrics_summary.scaling.scaling_events
              }}
              units={{
                average_utilization: '%'
              }}
            />
          )}
        </Grid>

        {/* Simulation Info */}
        <Grid item xs={12}>
          {loading ? (
            <SkeletonChart />
          ) : (
            <PerformanceMetricsCard
              title="Simulation Information"
              metrics={{
                duration: results.simulation_info.duration,
                execution_time: results.simulation_info.execution_time,
                completed_tokens: results.simulation_info.completed_tokens,
                total_tokens: results.simulation_info.total_tokens
              }}
              units={{
                duration: 's',
                execution_time: 's'
              }}
            />
          )}
        </Grid>

        {/* Workload Stats */}
        <Grid item xs={12}>
          {loading ? (
            <SkeletonChart />
          ) : (
            <PerformanceMetricsCard
              title="Workload Statistics"
              metrics={Object.entries(results.workload_stats).reduce((acc, [key, stats]) => ({
                ...acc,
                [`${key}_avg_rate`]: stats.avg_rate,
                [`${key}_max_rate`]: stats.max_rate,
                [`${key}_total_tokens`]: stats.total_tokens,
              }), {})}
              units={Object.entries(results.workload_stats).reduce((acc, [key]) => ({
                ...acc,
                [`${key}_avg_rate`]: 'req/s',
                [`${key}_max_rate`]: 'req/s',
              }), {})}
            />
          )}
        </Grid>
      </Grid>
    </Box>
  );
}