#!/usr/bin/env python3
"""Plot read throughput from benchmark reports"""

import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import re
import sys
import argparse
from pathlib import Path


def parse_file_size(size_str):
    """Convert file size string to numeric value for sorting"""
    size_str = size_str.lower()
    if 'k' in size_str:
        return float(size_str.replace('k', '')) / 1024  # Convert to MB
    elif 'm' in size_str:
        return float(size_str.replace('m', ''))
    elif 'g' in size_str:
        return float(size_str.replace('g', '')) * 1024  # Convert to MB
    return float(size_str)


def parse_params(param_str):
    """Parse compressed parameter string into components"""
    parts = param_str.split('|')
    if len(parts) >= 6:
        return {
            'bs': parts[0],
            'file_size': parts[1],
            'io_depth': parts[2],
            'io_type': parts[3],
            'threads': int(parts[4]),
            'nr_files': int(parts[5])
        }
    return None


def sort_key(param_str):
    """Generate sort key for parameter string: (io_type, threads, file_size_numeric)"""
    params = parse_params(param_str)
    if params:
        file_size_val = parse_file_size(params['file_size'])
        io_type_order = 0 if params['io_type'] == 'randread' else 1
        return (io_type_order, params['threads'], file_size_val)
    return (0, 0, 0)


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Plot read throughput from benchmark reports')
    parser.add_argument('--src', default='good_reports', 
                       help='Source directory containing CSV files (default: good_reports)')
    parser.add_argument('--output-file', default='results/throughput_comparison.png',
                       help='Output file path (default: results/throughput_comparison.png)')
    parser.add_argument('--metric', default='read_bw', 
                       choices=['read_bw', 'write_bw', 'avg_cpu', 'peak_cpu', 'avg_mem', 'peak_mem'],
                       help='Metric to plot (default: read_bw)')
    args = parser.parse_args()
    
    # Find all CSV files in source directory
    reports_dir = args.src
    csv_files = glob.glob(os.path.join(reports_dir, "*.csv"))
    
    if not csv_files:
        print(f"No CSV files found in {reports_dir}/")
        return
    
    print(f"Found {len(csv_files)} CSV files:")
    for f in csv_files:
        print(f"  - {os.path.basename(f)}")
    
    # Create figure with larger size for readability
    plt.figure(figsize=(20, 10))
    
    # Color palette and line styles
    colors = plt.cm.tab10(range(len(csv_files)))
    line_styles = ['-', '--', '-.', ':', '-', '--', '-.', ':', '-', '--']
    markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p', '*', 'h']
    
    # Map metric argument to column name and display info
    metric_map = {
        'read_bw': ('Read BW (MB/s)', 'Read Throughput (MB/s)'),
        'write_bw': ('Write BW (MB/s)', 'Write Throughput (MB/s)'),
        'avg_cpu': ('Avg CPU (%)', 'Average CPU (%)'),
        'peak_cpu': ('Peak CPU (%)', 'Peak CPU (%)'),
        'avg_mem': ('Avg Mem (MB)', 'Average Memory (MB)'),
        'peak_mem': ('Peak Mem (MB)', 'Peak Memory (MB)')
    }
    
    column_name, y_label = metric_map[args.metric]
    
    all_data = []
    
    # Read and process each CSV file
    for idx, csv_file in enumerate(csv_files):
        df = pd.read_csv(csv_file)
        
        # Get file name for legend
        file_name = os.path.basename(csv_file).replace('.csv', '')
        
        # Extract relevant columns
        if 'BS|FSize|IOD|IOType|Jobs|NrFiles' in df.columns and column_name in df.columns:
            # Create data with sort key
            for _, row in df.iterrows():
                param_str = row['BS|FSize|IOD|IOType|Jobs|NrFiles']
                metric_value = row[column_name]
                
                # Skip if metric_value is not a number or is '-'
                if pd.isna(metric_value) or metric_value == '-':
                    continue
                
                all_data.append({
                    'param': param_str,
                    'metric_value': float(metric_value),
                    'file': file_name,
                    'sort_key': sort_key(param_str),
                    'color_idx': idx
                })
    
    if not all_data:
        print("No valid data found in CSV files")
        return
    
    # Convert to DataFrame and sort
    plot_df = pd.DataFrame(all_data)
    plot_df = plot_df.sort_values('sort_key')
    
    # Get unique parameter strings in sorted order
    unique_params = plot_df['param'].unique()
    x_positions = {param: i for i, param in enumerate(unique_params)}
    
    # Plot each file's data
    for file_name in plot_df['file'].unique():
        file_data = plot_df[plot_df['file'] == file_name]
        color_idx = file_data['color_idx'].iloc[0]
        
        x_vals = [x_positions[p] for p in file_data['param']]
        y_vals = file_data['metric_value'].values
        
        plt.plot(x_vals, y_vals, 
                marker=markers[color_idx % len(markers)],
                linestyle=line_styles[color_idx % len(line_styles)],
                linewidth=2.5, 
                markersize=8, 
                label=file_name, 
                color=colors[color_idx],
                alpha=0.8)
    
    # Draw thin red dotted lines connecting points at the same x position
    for x_pos in range(len(unique_params)):
        # Get all y values at this x position
        y_values = []
        for file_name in plot_df['file'].unique():
            file_data = plot_df[plot_df['file'] == file_name]
            param = unique_params[x_pos]
            matching = file_data[file_data['param'] == param]
            if not matching.empty:
                y_values.append(matching['metric_value'].iloc[0])
        
        # Draw vertical line connecting all points at this x position
        if len(y_values) > 1:
            plt.plot([x_pos] * len(y_values), y_values, 
                    color='red', linestyle=':', linewidth=1.5, alpha=0.5, zorder=1)
    
    # Customize plot
    plt.xlabel('Test Configuration (File Size | IO Type | Threads)', fontsize=12, fontweight='bold')
    plt.ylabel(y_label, fontsize=12, fontweight='bold')
    plt.title(f'{y_label} Comparison Across Benchmark Reports', fontsize=14, fontweight='bold')
    plt.legend(loc='best', fontsize=10)
    plt.grid(True, alpha=0.3, linestyle='--')
    
    # Set x-axis labels
    plt.xticks(range(len(unique_params)), unique_params, rotation=90, ha='right', fontsize=8)
    
    # Adjust layout to prevent label cutoff
    plt.tight_layout()
    
    # Save plot
    output_file = args.output_file
    
    # Create parent directory if needed
    parent_dir = os.path.dirname(output_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"\nPlot saved to: {output_file}")
    
    # Show plot
    plt.show()


if __name__ == "__main__":
    main()
