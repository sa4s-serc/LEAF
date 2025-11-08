import { Card, CardContent, Skeleton, Box } from '@mui/material';

interface SkeletonChartProps {
  height?: number;
}

export default function SkeletonChart({ height = 300 }: SkeletonChartProps) {
  console.log('SkeletonChart rendered with height:', height);
  
  return (
    <Card>
      <CardContent>
        <Skeleton variant="text" width="40%" height={32} sx={{ mb: 1 }} />
        <Skeleton variant="text" width="20%" height={48} sx={{ mb: 2 }} />
        <Box sx={{ height }}>
          <Skeleton variant="rectangular" width="100%" height="100%" />
        </Box>
      </CardContent>
    </Card>
  );
}