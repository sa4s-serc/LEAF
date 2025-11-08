import { AppBar, Toolbar, Typography, Box } from '@mui/material';

export default function NavigationBar() {
  console.log('NavigationBar rendered');
  
  return (
    <AppBar position="static" elevation={0} sx={{ borderBottom: 1, borderColor: 'divider' }}>
      <Toolbar>
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <img 
            src="/leaf.svg" 
            alt="LEAF Cloud Logo" 
            style={{ height: 32, marginRight: 16 }}
            onError={(e) => console.error('Failed to load logo:', e)}
          />
          <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
            LEAF Cloud Tool
          </Typography>
        </Box>
      </Toolbar>
    </AppBar>
  );
}