import os
import matplotlib 
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def plot_time_series(x, y, title, xlabel, ylabel, output_path):
    """
    Plot a simple time-series line chart and save to file.

    Args:
        x (list of float): Time points
        y (list of float): Values corresponding to time points
        title (str): Plot title
        xlabel (str): X-axis label
        ylabel (str): Y-axis label
        output_path (str): File path to save the plot (PNG)
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.figure()
    plt.plot(x, y, marker='o')
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()