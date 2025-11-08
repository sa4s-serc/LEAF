import {
  Card,
  CardContent,
  Typography,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Chip,
  Box,
  useTheme,
} from '@mui/material';
import { Recommendation } from '../services/api';
import InfoIcon from '@mui/icons-material/Info';
import WarningIcon from '@mui/icons-material/Warning';
import ErrorIcon from '@mui/icons-material/Error';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';

interface RecommendationsViewerProps {
  recommendations: Recommendation[];
}

const severityConfig = {
  high: { icon: <ErrorIcon color="error" />, color: 'error' as const },
  medium: { icon: <WarningIcon color="warning" />, color: 'warning' as const },
  low: { icon: <InfoIcon color="info" />, color: 'info' as const },
  none: { icon: <CheckCircleIcon color="success" />, color: 'success' as const },
};

export default function RecommendationsViewer({ recommendations }: RecommendationsViewerProps) {
  const theme = useTheme();

  return (
    <Card sx={{ mt: 3 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Recommendations
        </Typography>
        <List>
          {recommendations.map((rec, index) => {
            const config = severityConfig[rec.severity] || severityConfig.low;
            return (
              <ListItem key={index} alignItems="flex-start" sx={{
                borderLeft: `4px solid ${theme.palette[config.color].main}`,
                mb: 2,
                p: 2,
                borderRadius: 1,
                bgcolor: 'action.hover'
              }}>
                <ListItemIcon sx={{ mt: 0.5 }}>
                  {config.icon}
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                      <Typography variant="subtitle1" component="span" sx={{ fontWeight: 'bold', mr: 2 }}>
                        {rec.title}
                      </Typography>
                      <Chip label={rec.severity} color={config.color} size="small" />
                    </Box>
                  }
                  // --- START OF MODIFICATION ---
                  secondary={
                    <Typography component="p" variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
                      {rec.description}
                    </Typography>
                  }
                  // --- END OF MODIFICATION ---
                />
              </ListItem>
            );
          })}
        </List>
      </CardContent>
    </Card>
  );
}