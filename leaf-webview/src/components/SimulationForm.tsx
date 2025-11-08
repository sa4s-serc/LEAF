import { useState, useEffect, useCallback } from 'react';
import {
  Box,
  TextField,
  Button,
  Card,
  CardContent,
  Typography,
  Grid,
  Tab,
  Tabs,
  Paper,
  CardMedia,
  useTheme,
  CircularProgress,
  Tooltip,
  IconButton
} from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import { api, SimulationParams, SimulationResponse, Recommendation } from '../services/api';
import MetricsViewer from './MetricsViewer';
import ErrorAlert from './ErrorAlert';
import LoadingOverlay from './LoadingOverlay';
import ExamplesList from './ExamplesList';
import RecommendationsViewer from './RecommendationsViewer';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      {...other}
    >
      {value === index && (
        <Box sx={{ p: 3 }}>
          {children}
        </Box>
      )}
    </div>
  );
}

const PARAMETER_DESCRIPTIONS = {
  terraform: 'The path to your Terraform project directory containing .tf files. You can use one of the example projects provided below.',
  workload_rate: 'Average number of requests per second to simulate. Higher rates will generate more load on your infrastructure.',
  duration: 'Total duration of the simulation in seconds. Longer durations provide more accurate results but take more time.',
  queue_factor: 'Multiplier for resource queue capacity. Values > 1 increase capacity, < 1 decrease capacity.'
};

export default function SimulationForm() {
  const theme = useTheme();
  const [simLoading, setSimLoading] = useState(false);
  const [diagramsLoading, setDiagramsLoading] = useState(false);
  const [error, setError] = useState<{ message: string; details?: string } | null>(null);
  const [results, setResults] = useState<SimulationResponse | null>(null);
  const [diagrams, setDiagrams] = useState<{
    classDiagram?: string;
    deploymentDiagram?: string;
  }>({});
  const [tabValue, setTabValue] = useState(0);
  const [params, setParams] = useState<SimulationParams>({
    terraform: '',
    workload_rate: 5,
    duration: 30,
    queue_factor: 1.0,
  });
  // --- START OF MODIFICATION ---
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  const [recsLoading, setRecsLoading] = useState(false);
  // --- END OF MODIFICATION ---

  useEffect(() => {
    console.log('SimulationForm state update:', {
      simLoading,
      diagramsLoading,
      error,
      results: results ? 'Present' : 'None',
      diagrams,
      tabValue,
      params,
      // --- START OF MODIFICATION ---
      recsLoading,
      recommendations: recommendations ? 'Present' : 'None'
      // --- END OF MODIFICATION ---
    });
  }, [simLoading, diagramsLoading, error, results, diagrams, tabValue, params, recsLoading, recommendations]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    console.log('Submitting simulation with params:', params);
    setSimLoading(true);
    setError(null);
    // --- START OF MODIFICATION ---
    setResults(null);
    setRecommendations(null);
    // --- END OF MODIFICATION ---
    
    try {
      const response = await api.runSimulation(params);
      console.log('Simulation response:', response);
      setResults(response);
      
      // Generate diagrams in parallel but after simulation
      setDiagramsLoading(true);
      try {
        console.log('Generating diagrams for:', params.terraform);
        const diagramsResult = await api.generateDiagrams(params.terraform);
        console.log('Diagrams generated:', diagramsResult);
        setDiagrams(diagramsResult);
      } catch (diagramError) {
        console.error('Diagram generation failed:', diagramError);
        const error = diagramError as Error;
        setError(prev => ({
          message: prev?.message || 'Failed to generate diagrams',
          details: error.message || 'Unknown error occurred during diagram generation'
        }));
      } finally {
        setDiagramsLoading(false);
      }
    } catch (err) {
      console.error('Simulation failed:', err);
      const error = err as { response?: { data?: { error?: string; details?: string } } };
      setError({
        message: error.response?.data?.error || 'Failed to run simulation',
        details: error.response?.data?.details || 'An unknown error occurred'
      });
      setResults(null);
    } finally {
      setSimLoading(false);
    }
  };

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    console.log('Tab changed to:', newValue);
    setTabValue(newValue);
  };

  const handleInputChange = (field: keyof SimulationParams) => (
    e: React.ChangeEvent<HTMLInputElement>
  ) => {
    const value = e.target.type === 'number' ? Number(e.target.value) : e.target.value;
    console.log('Input changed:', { field, value });
    setParams(prev => ({ ...prev, [field]: value }));
  };

  const handleExampleSelect = useCallback((path: string) => {
    console.log('Selected example project:', path);
    setParams(prev => ({
      ...prev,
      terraform: path
    }));
  }, []);

  // --- START OF MODIFICATION ---
  const handleGetRecommendations = async () => {
    if (!results) return;

    console.log('Fetching recommendations');
    setRecsLoading(true);
    setError(null);
    try {
        const recs = await api.getRecommendations(results);
        console.log('Recommendations received:', recs);
        setRecommendations(recs);
    } catch (err) {
        console.error('Failed to get recommendations:', err);
        const error = err as { response?: { data?: { error?: string; details?: string } } };
        setError({
            message: error.response?.data?.error || 'Failed to get recommendations',
            details: error.response?.data?.details || 'An unknown error occurred'
        });
        setRecommendations(null);
    } finally {
        setRecsLoading(false);
    }
  };
  // --- END OF MODIFICATION ---

  const renderParameterHelp = (param: keyof typeof PARAMETER_DESCRIPTIONS) => (
    <Tooltip title={PARAMETER_DESCRIPTIONS[param]} arrow placement="top">
      <IconButton size="small" sx={{ ml: 1 }}>
        <HelpOutlineIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  );

  // Main content render
  return (
    <Box sx={{ width: '100%' }}>
      <Card>
        <CardContent>
          <Typography variant="h5" gutterBottom>
            Run Infrastructure Simulation
          </Typography>
          
          <Box component="form" onSubmit={handleSubmit}>
            <Grid container spacing={3}>
              <Grid item xs={12}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  <TextField
                    required
                    fullWidth
                    label="Terraform Project Path"
                    value={params.terraform}
                    onChange={handleInputChange('terraform')}
                    helperText="Path to the Terraform project on the server"
                  />
                  {renderParameterHelp('terraform')}
                </Box>
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  <TextField
                    fullWidth
                    type="number"
                    label="Workload Rate"
                    value={params.workload_rate}
                    onChange={handleInputChange('workload_rate')}
                    helperText="Requests per second"
                    inputProps={{ min: 1 }}
                  />
                  {renderParameterHelp('workload_rate')}
                </Box>
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  <TextField
                    fullWidth
                    type="number"
                    label="Duration"
                    value={params.duration}
                    onChange={handleInputChange('duration')}
                    helperText="Simulation duration in seconds"
                    inputProps={{ min: 1 }}
                  />
                  {renderParameterHelp('duration')}
                </Box>
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  <TextField
                    fullWidth
                    type="number"
                    label="Queue Factor"
                    value={params.queue_factor}
                    onChange={handleInputChange('queue_factor')}
                    helperText="Resource queue capacity multiplier"
                    inputProps={{ step: "0.1", min: 0.1 }}
                  />
                  {renderParameterHelp('queue_factor')}
                </Box>
              </Grid>
            </Grid>

            {error && !recsLoading && !simLoading && (
              <ErrorAlert error={error.message} details={error.details} />
            )}

            <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                type="submit"
                variant="contained"
                disabled={simLoading || !params.terraform}
                sx={{
                  position: 'relative',
                  minWidth: 150,
                }}
              >
                {simLoading ? (
                  <>
                    <CircularProgress
                      size={24}
                      sx={{
                        position: 'absolute',
                        top: '50%',
                        left: '50%',
                        marginTop: '-12px',
                        marginLeft: '-12px',
                      }}
                    />
                    Simulating...
                  </>
                ) : (
                  'Run Simulation'
                )}
              </Button>
            </Box>
          </Box>

          <ExamplesList onSelect={handleExampleSelect} />
        </CardContent>
      </Card>
      
      {/* --- START OF MODIFICATION --- */}
      {simLoading && <LoadingOverlay message="Running simulation..." />}
      {/* --- END OF MODIFICATION --- */}

      {(results || diagrams.classDiagram || diagrams.deploymentDiagram) && (
        <Paper sx={{ mt: 3 }}>
          <Tabs 
            value={tabValue} 
            onChange={handleTabChange} 
            centered
            sx={{
              borderBottom: 1,
              borderColor: 'divider',
              bgcolor: theme.palette.background.paper
            }}
          >
            <Tab label="Metrics" />
            <Tab label="Class Diagram" />
            <Tab label="Deployment Diagram" />
          </Tabs>

          <TabPanel value={tabValue} index={0}>
            {results && <MetricsViewer data={results} loading={simLoading} />}
          </TabPanel>

          <TabPanel value={tabValue} index={1}>
            {diagramsLoading ? (
              <LoadingOverlay message="Generating class diagram..." />
            ) : diagrams.classDiagram ? (
              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Class Diagram
                  </Typography>
                  <CardMedia
                    component="img"
                    image={diagrams.classDiagram}
                    alt="Class Diagram"
                    sx={{ 
                      maxWidth: '100%', 
                      height: 'auto',
                      borderRadius: 1,
                      boxShadow: theme.shadows[1]
                    }}
                    onError={(e) => {
                      console.error('Failed to load class diagram:', e);
                      setError({
                        message: 'Failed to load class diagram',
                        details: 'The diagram image could not be loaded'
                      });
                    }}
                  />
                </CardContent>
              </Card>
            ) : null}
          </TabPanel>

          <TabPanel value={tabValue} index={2}>
            {diagramsLoading ? (
              <LoadingOverlay message="Generating deployment diagram..." />
            ) : diagrams.deploymentDiagram ? (
              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Deployment Diagram
                  </Typography>
                  <CardMedia
                    component="img"
                    image={diagrams.deploymentDiagram}
                    alt="Deployment Diagram"
                    sx={{ 
                      maxWidth: '100%', 
                      height: 'auto',
                      borderRadius: 1,
                      boxShadow: theme.shadows[1]
                    }}
                    onError={(e) => {
                      console.error('Failed to load deployment diagram:', e);
                      setError({
                        message: 'Failed to load deployment diagram',
                        details: 'The diagram image could not be loaded'
                      });
                    }}
                  />
                </CardContent>
              </Card>
            ) : null}
          </TabPanel>
        </Paper>
      )}

      {/* --- START OF MODIFICATION --- */}
      {results && !simLoading && (
        <Box sx={{ mt: 3, textAlign: 'center' }}>
            <Button
                variant="contained"
                onClick={handleGetRecommendations}
                disabled={recsLoading}
                size="large"
                color="secondary"
            >
                {recsLoading ? (
                    <>
                        <CircularProgress size={24} sx={{ color: 'white', mr: 1 }} />
                        Generating Recommendations...
                    </>
                ) : (
                    'Generate Recommendations'
                )}
            </Button>
        </Box>
      )}

      {recsLoading && <LoadingOverlay message="Analyzing for recommendations..." />}

      {recommendations && !recsLoading && (
          <RecommendationsViewer recommendations={recommendations} />
      )}
      {/* --- END OF MODIFICATION --- */}
    </Box>
  );
}