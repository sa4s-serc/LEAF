import { Alert, AlertTitle, Box } from '@mui/material';

interface ErrorAlertProps {
  error: string | null;
  details?: string;
}

export default function ErrorAlert({ error, details }: ErrorAlertProps) {
  console.log('ErrorAlert rendered with:', { error, details });

  if (!error) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Alert severity="error">
        <AlertTitle>{error}</AlertTitle>
        {details && (
          <Box component="pre" sx={{ 
            mt: 1, 
            p: 1, 
            bgcolor: 'rgba(0,0,0,0.1)', 
            borderRadius: 1,
            fontSize: '0.875rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word'
          }}>
            {details}
          </Box>
        )}
      </Alert>
    </Box>
  );
}