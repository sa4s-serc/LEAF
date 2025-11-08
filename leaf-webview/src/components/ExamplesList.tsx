import { Card, CardContent, Typography, List, ListItem, ListItemButton, ListItemText, useTheme } from '@mui/material';
import { useState, useCallback } from 'react';

interface Example {
  name: string;
  path: string;
  description: string;
}

const EXAMPLES: Example[] = [
  {
    name: 'Simple Deployment',
    path: './examples/simple_deployment',
    description: 'Basic cloud function with API Gateway and SQL database'
  },
  {
    name: 'Lakeside Architecture',
    path: './examples/lakeside_arch',
    description: 'Data lake architecture with processing and storage components'
  },
  {
    name: 'Modified Lakeside',
    path: './examples/lakeside_arch_modified',
    description: 'Enhanced data lake architecture with additional components'
  }
];

interface ExamplesListProps {
  onSelect: (path: string) => void;
}

export default function ExamplesList({ onSelect }: ExamplesListProps) {
  const theme = useTheme();
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const handleSelect = useCallback((index: number) => {
    setSelectedIndex(index);
    onSelect(EXAMPLES[index].path);
  }, [onSelect]);

  return (
    <Card variant="outlined" sx={{ mt: 2 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Example Projects
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Select an example Terraform project to run the simulation
        </Typography>
        <List>
          {EXAMPLES.map((example, index) => (
            <ListItem key={example.path} disablePadding divider>
              <ListItemButton 
                selected={selectedIndex === index}
                onClick={() => handleSelect(index)}
                sx={{
                  borderRadius: 1,
                  '&.Mui-selected': {
                    backgroundColor: theme.palette.action.selected,
                    '&:hover': {
                      backgroundColor: theme.palette.action.selected,
                    },
                  },
                }}
              >
                <ListItemText
                  primary={example.name}
                  secondary={example.description}
                  primaryTypographyProps={{
                    fontWeight: selectedIndex === index ? 600 : 400,
                  }}
                />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </CardContent>
    </Card>
  );
}