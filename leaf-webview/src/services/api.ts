import axios, { AxiosError } from 'axios';
import { transformApiError, validateSimulationResponse } from './errorHandling';
import { SimulationResults } from '../types/metrics';

const API_BASE_URL = 'http://localhost:5000';

const axiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor for logging
axiosInstance.interceptors.request.use(
  (config) => {
    console.log('API Request:', {
      method: config.method,
      url: config.url,
      data: config.data,
    });
    return config;
  },
  (error) => {
    console.error('API Request Error:', error);
    return Promise.reject(error);
  }
);

// Add response interceptor for logging
axiosInstance.interceptors.response.use(
  (response) => {
    console.log('API Response:', {
      status: response.status,
      data: response.data,
    });
    return response;
  },
  (error) => {
    console.error('API Response Error:', {
      status: error.response?.status,
      data: error.response?.data,
      message: error.message,
    });
    return Promise.reject({
      ...error,
      userMessage: error.response?.data?.error || 'An unexpected error occurred',
      details: error.response?.data?.details
    });
  }
);

export interface SimulationParams {
  terraform: string;
  config?: string;
  var_files?: string[];
  workload_rate?: number;
  duration?: number;
  queue_factor?: number;
  output?: string;
}

export type SimulationResponse = SimulationResults;

// --- START OF MODIFICATION ---
export interface Recommendation {
  type: string;
  severity: 'high' | 'medium' | 'low' | 'none';
  title: string;
  description: string;
}
// --- END OF MODIFICATION ---

export const api = {
  getVersion: async () => {
    console.debug('Fetching API version');
    try {
      const response = await axiosInstance.get('/version');
      return response.data;
    } catch (error) {
      throw transformApiError(error as AxiosError);
    }
  },

  runSimulation: async (params: SimulationParams) => {
    console.debug('Running simulation with params:', params);
    try {
      const response = await axiosInstance.post('/simulate', params);
      if (!validateSimulationResponse(response.data)) {
        throw new Error('Invalid simulation response format');
      }
      return response.data as SimulationResponse;
    } catch (error) {
      if (error instanceof Error && error.message === 'Invalid simulation response format') {
        throw {
          message: 'Invalid response from server',
          details: 'The server response did not match the expected format'
        };
      }
      throw transformApiError(error as AxiosError);
    }
  },

  // --- START OF MODIFICATION ---
  getRecommendations: async (results: SimulationResponse) => {
    console.debug('Fetching recommendations for results:', results);
    try {
      const response = await axiosInstance.post('/recommendations', { results });
      return response.data.recommendations as Recommendation[];
    } catch (error) {
      throw transformApiError(error as AxiosError);
    }
  },
  // --- END OF MODIFICATION ---

  generateDiagrams: async (terraform: string) => {
    console.debug('Generating diagrams for terraform path:', terraform);
    try {
      const [classDiagram, deploymentDiagram] = await Promise.all([
        axiosInstance.post('/diagram/class', { terraform }),
        axiosInstance.post('/diagram/deployment', { terraform })
      ]);

      if (!classDiagram.data.diagram_file || !deploymentDiagram.data.diagram_file) {
        throw new Error('Invalid diagram response format');
      }

      return {
        classDiagram: classDiagram.data.diagram_file,
        deploymentDiagram: deploymentDiagram.data.diagram_file
      };
    } catch (error) {
      if (error instanceof Error && error.message === 'Invalid diagram response format') {
        throw {
          message: 'Invalid diagram response',
          details: 'The server response did not include the expected diagram files'
        };
      }
      throw transformApiError(error as AxiosError);
    }
  },

  analyzeResults: async (resultsPath: string, metrics: string[] = ['all']) => {
    console.debug('Analyzing results:', { resultsPath, metrics });
    try {
      const response = await axiosInstance.post('/analyze', {
        results: resultsPath,
        metrics
      });
      return response.data;
    } catch (error) {
      throw transformApiError(error as AxiosError);
    }
  }
};