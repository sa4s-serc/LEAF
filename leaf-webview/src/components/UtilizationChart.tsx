import { Card, CardContent, Typography, Box, useTheme } from '@mui/material';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { ResourceUtilization } from '../types/metrics';
import SkeletonChart from './SkeletonChart';

interface UtilizationChartProps {
  resourceUtilization: Record<string, ResourceUtilization>;
  loading?: boolean;
}

export default function UtilizationChart({ 
  resourceUtilization,
  loading = false 
}: UtilizationChartProps) {
  const theme = useTheme();

  console.log('UtilizationChart rendered:', { loading, resourceCount: Object.keys(resourceUtilization).length });

  if (loading) {
    return <SkeletonChart height={400} />;
  }

  const chartData = Object.entries(resourceUtilization).map(([name, data]) => ({
    name: name.split('.').pop() || name,
    average: Number((data.avg * 100).toFixed(2)),
    max: Number((data.max * 100).toFixed(2)),
    min: Number((data.min * 100).toFixed(2))
  }));

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Resource Utilization
        </Typography>
        <Box sx={{ height: 400, mt: 2 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={chartData}
              margin={{ top: 5, right: 30, left: 20, bottom: 100 }}
            >
              <XAxis
                dataKey="name"
                angle={-45}
                textAnchor="end"
                height={100}
                tick={{ fill: theme.palette.text.primary, fontSize: 12 }}
              />
              <YAxis
                tick={{ fill: theme.palette.text.primary }}
                label={{ 
                  value: 'Utilization (%)', 
                  angle: -90, 
                  position: 'insideLeft',
                  fill: theme.palette.text.primary 
                }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: theme.palette.background.paper,
                  border: `1px solid ${theme.palette.divider}`,
                  borderRadius: 4
                }}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey="average"
                stroke={theme.palette.primary.main}
                strokeWidth={2}
                dot={{ r: 4 }}
              />
              <Line
                type="monotone"
                dataKey="max"
                stroke={theme.palette.error.main}
                strokeWidth={2}
                dot={{ r: 4 }}
              />
              <Line
                type="monotone"
                dataKey="min"
                stroke={theme.palette.success.main}
                strokeWidth={2}
                dot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </Box>
      </CardContent>
    </Card>
  );
}