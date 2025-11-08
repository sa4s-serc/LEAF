import Layout from './components/Layout';
import SimulationForm from './components/SimulationForm';
import './App.css';

function App() {
  console.log('App rendered');
  
  return (
    <Layout>
      <SimulationForm />
    </Layout>
  );
}

export default App;
