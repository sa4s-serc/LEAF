import { Card, CardContent, Typography, Grid, Box } from '@mui/material';

interface MetricItemProps {
  label: string;
  value: number;
  unit?: string;
}

function MetricItem({ label, value, unit }: MetricItemProps) {
  return (
    <Box sx={{ textAlign: 'center', p: 2 }}>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        {label}
      </Typography>
      <Typography variant="h5" color="primary">
        {value.toFixed(2)}{unit && ` ${unit}`}
      </Typography>
    </Box>
  );
}

interface PerformanceMetricsCardProps {
  title: string;
  metrics: Record<string, number>;
  units?: Record<string, string>;
}

export default function PerformanceMetricsCard({ title, metrics, units = {} }: PerformanceMetricsCardProps) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          {title}
        </Typography>
        <Grid container spacing={2}>
          {Object.entries(metrics).map(([key, value]) => (
            <Grid item xs={12} sm={6} md={4} key={key}>
              <MetricItem
                label={key.split('_').map(word => 
                  word.charAt(0).toUpperCase() + word.slice(1)
                ).join(' ')}
                value={value}
                unit={units[key]}
              />
            </Grid>
          ))}
        </Grid>
      </CardContent>
    </Card>
  );
}