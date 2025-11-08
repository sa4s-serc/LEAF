import { Box, Card, CardContent, Typography, useTheme } from '@mui/material';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import SkeletonChart from './SkeletonChart';

interface ResourceMetricsCardProps {
  title: string;
  total: number;
  unit: string;
  data: Record<string, number>;
  loading?: boolean;
  durationInHours?: number;
}

export default function ResourceMetricsCard({ 
  title, 
  total, 
  unit, 
  data, 
  loading = false,
  durationInHours
}: ResourceMetricsCardProps) {
  const theme = useTheme();
  
  console.log('ResourceMetricsCard rendered:', { title, total, loading });

  if (loading) {
    return <SkeletonChart />;
  }
  
  const chartData = Object.entries(data).map(([name, value]) => ({
    name,
    value: Number(
      (typeof value === 'number' ? value : 0)
        .toFixed(3)
    )
  }));

  const isEnergyCard = title === "Energy Consumption";
  const averagePowerInWatts = isEnergyCard && durationInHours && durationInHours > 0 ? (total / durationInHours) * 1000 : 0;

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          {title}
        </Typography>
        <Typography variant="h4" color="primary" gutterBottom>
          {isEnergyCard ? `${averagePowerInWatts.toFixed(2)} W (Avg Power)` : `${total.toFixed(2)} ${unit}`}
        </Typography>
        
        <Box sx={{ height: 300, mt: 2 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <XAxis 
                dataKey="name"
                angle={-45}
                textAnchor="end"
                height={70}
                interval={0}
                tick={{ fill: theme.palette.text.primary, fontSize: 12 }}
              />
              <YAxis tick={{ fill: theme.palette.text.primary }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: theme.palette.background.paper,
                  border: `1px solid ${theme.palette.divider}`,
                  borderRadius: 4
                }}
              />
              <Bar dataKey="value" fill={theme.palette.primary.main} />
            </BarChart>
          </ResponsiveContainer>
        </Box>
      </CardContent>
    </Card>
  );
}