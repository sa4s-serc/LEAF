import { Paper, Typography, Box, Chip } from '@mui/material';
import { SimulationResults } from '../types/metrics';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import WarningIcon from '@mui/icons-material/Warning';

interface AnalysisSummaryProps {
  data: SimulationResults;
}

export default function AnalysisSummary({ data }: AnalysisSummaryProps) {
  // Debug incoming data
  console.log('AnalysisSummary metrics_summary:', data.metrics_summary);

  const getUtilizationStatus = (utilization: number) => {
    if (utilization > 0.8) return { color: 'error', icon: <WarningIcon />, text: 'High' };
    if (utilization > 0.6) return { color: 'warning', icon: <TrendingUpIcon />, text: 'Moderate' };
    return { color: 'success', icon: <TrendingDownIcon />, text: 'Low' };
  };

  const avgUtil = data.metrics_summary.scaling?.average_utilization ?? 0;
  const utilizationStatus = getUtilizationStatus(avgUtil);

  // Helper to safely format numbers
  const fmt = (value: number | undefined, digits = 2) =>
    typeof value === 'number' ? value.toFixed(digits) : '–';

  const durationInHours = data.results.simulation_info.duration / 3600;
  const totalCo2Kg = data.metrics_summary.carbon?.total_kg_co2;
  const hourlyGrams = durationInHours > 0 && totalCo2Kg != null ? (totalCo2Kg / durationInHours) * 1000 : undefined;
  const energy = data.metrics_summary.energy?.total_kwh;
  const p95 = data.metrics_summary.latency?.p95;
  const averagePowerInWatts = durationInHours > 0 && energy != null ? (energy / durationInHours) * 1000 : undefined;


  return (
    <Paper sx={{ p: 2, mb: 3, borderRadius: 2 }}>
      <Typography variant="h6" gutterBottom>
        Analysis Summary
      </Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        <Chip
          label={`Utilization: ${utilizationStatus.text}`}
          color={utilizationStatus.color as any}
          icon={utilizationStatus.icon}
        />

        <Chip
          label={`Carbon: ${fmt(hourlyGrams)} g CO₂e/hour`}
          color={hourlyGrams != null && hourlyGrams > 100 ? 'warning' : 'success'}
        />

        <Chip
          label={`Avg Power: ${fmt(averagePowerInWatts)} W`}
          color={averagePowerInWatts != null && averagePowerInWatts > 1000 ? 'warning' : 'success'}
        />

        <Chip
          label={`Latency p95: ${fmt(p95)} ms`}
          color={p95 != null && p95 > 1000 ? 'error' : 'success'}
        />
      </Box>
    </Paper>
  );
}