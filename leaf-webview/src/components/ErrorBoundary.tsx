import React from 'react';
import { Alert, Box, Button } from '@mui/material';

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  public state: State = {
    hasError: false
  };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <Box sx={{ m: 2 }}>
          <Alert 
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => window.location.reload()}>
                RELOAD
              </Button>
            }
          >
            Something went wrong. Please try reloading the page.
          </Alert>
        </Box>
      );
    }

    return this.props.children;
  }
}