import { AxiosError } from 'axios';

export interface ApiError {
  message: string;
  details?: string;
  status?: number;
}

export function transformApiError(error: AxiosError): ApiError {
  console.error('API Error:', error);

  if (error.response) {
    // Server responded with error
    const data = error.response.data as any;
    return {
      message: data.error || 'Server error occurred',
      details: data.details || error.message,
      status: error.response.status
    };
  } else if (error.request) {
    // Request was made but no response
    return {
      message: 'No response from server',
      details: 'Please check your connection and try again',
      status: 0
    };
  } else {
    // Request setup error
    return {
      message: 'Failed to make request',
      details: error.message,
      status: 0
    };
  }
}

export function validateSimulationResponse(data: any): boolean {
  console.log('Validating simulation response:', data);
  
  if (!data || typeof data !== 'object') {
    console.error('Invalid response: not an object');
    return false;
  }

  if (!data.metrics_summary || typeof data.metrics_summary !== 'object') {
    console.error('Invalid response: missing metrics_summary');
    return false;
  }

  const requiredMetrics = ['carbon', 'energy', 'latency', 'scaling'];
  for (const metric of requiredMetrics) {
    if (!data.metrics_summary[metric]) {
      console.error(`Invalid response: missing ${metric} metrics`);
      return false;
    }
  }

  if (!data.results || typeof data.results !== 'object') {
    console.error('Invalid response: missing results');
    return false;
  }

  return true;
}