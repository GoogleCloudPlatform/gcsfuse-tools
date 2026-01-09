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
    parser.add_argument('input', nargs='?', default='good_reports',
                       help='Input: CSV file or directory containing CSV files (default: good_reports)')
    parser.add_argument('--output-file', default='results/throughput_comparison.png',
                       help='Output file path (default: results/throughput_comparison.png)')
    parser.add_argument('--metric', '--metrics', nargs='+', 
                       default=['read_bw', 'avg_cpu', 'peak_cpu', 'avg_mem', 'peak_mem', 'avg_page_cache', 'peak_page_cache', 'avg_sys_cpu', 'peak_sys_cpu'],
                       choices=['read_bw', 'write_bw', 'avg_cpu', 'peak_cpu', 'avg_mem', 'peak_mem', 'avg_page_cache', 'peak_page_cache', 'avg_sys_cpu', 'peak_sys_cpu'],
                       help='Metric(s) to plot - single or multiple (default: all except write_bw)')
    parser.add_argument('--mode', default='auto', choices=['auto', 'combined', 'per-config'],
                       help='Plot mode: auto (detect from input), combined (all on same graph), or per-config (separate graph per config)')
    args = parser.parse_args()
    
    # Determine which metrics to plot
    metrics_to_plot = args.metric
    input_path = args.input
    
    # Check if input is a file or directory
    if not os.path.exists(input_path):
        print(f"ERROR: Input path does not exist: {input_path}")
        sys.exit(1)
    
    if os.path.isfile(input_path):
        # Input is a single CSV file
        print(f"Input: Single CSV file - {input_path}")
        
        # Read CSV to check if it has a Config column
        try:
            df = pd.read_csv(input_path)
            has_config_column = 'Config' in df.columns
        except Exception as e:
            print(f"ERROR: Failed to read CSV file: {e}")
            sys.exit(1)
        
        # Determine mode
        if args.mode == 'auto':
            # Default to combined mode for all cases
            mode = 'combined'
            if has_config_column:
                print(f"Auto-detected mode: combined (will plot all configs on same graph)")
            else:
                print(f"Auto-detected mode: combined (single dataset)")
        else:
            mode = args.mode
            if mode == 'per-config' and not has_config_column:
                print("ERROR: per-config mode requires CSV with 'Config' column")
                sys.exit(1)
        
        if mode == 'per-config':
            plot_per_config_from_single_csv(input_path, args.output_file, metrics_to_plot)
        else:
            # Combined mode - plot all configs on same graph
            plot_combined_mode_single_file(input_path, args.output_file, metrics_to_plot)
    
    elif os.path.isdir(input_path):
        # Input is a directory
        print(f"Input: Directory - {input_path}")
        
        if args.mode == 'per-config':
            print("ERROR: per-config mode requires a single CSV file, not a directory")
            sys.exit(1)
        
        plot_combined_mode(input_path, args.output_file, metrics_to_plot)
    
    else:
        print(f"ERROR: Input path is neither a file nor directory: {input_path}")
        sys.exit(1)


def plot_per_config_from_single_csv(csv_file, output_file_base, metrics_to_plot):
    """Generate separate plots for each config from a multi-config CSV"""
    
    if not os.path.exists(csv_file):
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)
    
    print(f"Loading multi-config CSV: {csv_file}")
    df = pd.read_csv(csv_file)
    
    # Check if Config column exists
    if 'Config' not in df.columns:
        print("ERROR: CSV file does not have a 'Config' column. This mode requires multi-config reports.")
        sys.exit(1)
    
    # Get unique configs
    configs = df['Config'].unique()
    print(f"Found {len(configs)} configurations: {', '.join(configs)}")
    
    # Map metric argument to column name and display info
    metric_map = {
        'read_bw': ('Read BW (MB/s)', 'Read Throughput (MB/s)'),
        'write_bw': ('Write BW (MB/s)', 'Write Throughput (MB/s)'),
        'avg_cpu': ('Avg CPU (%)', 'Average GCSFuse CPU (%)'),
        'peak_cpu': ('Peak CPU (%)', 'Peak GCSFuse CPU (%)'),
        'avg_mem': ('Avg Mem (MB)', 'Average GCSFuse Memory (MB)'),
        'peak_mem': ('Peak Mem (MB)', 'Peak GCSFuse Memory (MB)'),
        'avg_page_cache': ('Avg PgCache (GB)', 'Average Page Cache (GB)'),
        'peak_page_cache': ('Peak PgCache (GB)', 'Peak Page Cache (GB)'),
        'avg_sys_cpu': ('Avg Sys CPU (%)', 'Average System CPU (%)'),
        'peak_sys_cpu': ('Peak Sys CPU (%)', 'Peak System CPU (%)')
    }
    
    # Determine subplot layout based on number of metrics (vertical arrangement)
    num_metrics = len(metrics_to_plot)
    nrows, ncols = num_metrics, 1
    fig_height = 8 * num_metrics  # 8 inches per subplot
    
    # Color palette for different configs
    colors = plt.cm.tab10(range(len(configs)))
    line_styles = ['-', '--', '-.', ':', '-', '--', '-.', ':', '-', '--']
    markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p', '*', 'h']
    
    # Prepare output directory
    output_dir = os.path.dirname(output_file_base) or '.'
    output_basename = os.path.splitext(os.path.basename(output_file_base))[0]
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate a plot for each config
    for config_idx, config in enumerate(configs):
        config_df = df[df['Config'] == config].copy()
        
        if config_df.empty:
            print(f"Warning: No data for config {config}")
            continue
        
        print(f"\nGenerating plot for config: {config} ({len(config_df)} test cases)")
        
        # Create figure with subplots
        fig, axes = plt.subplots(nrows, ncols, figsize=(20, fig_height))
        
        # Flatten axes array for easier iteration
        if num_metrics == 1:
            axes = [axes]
        else:
            axes = axes.flatten()
        
        # Sort by test parameters
        if 'BS|FSize|IOD|IOType|Jobs|NrFiles' in config_df.columns:
            config_df['sort_key'] = config_df['BS|FSize|IOD|IOType|Jobs|NrFiles'].apply(sort_key)
            config_df = config_df.sort_values('sort_key')
        
        # Get test case labels (x-axis)
        test_labels = config_df['BS|FSize|IOD|IOType|Jobs|NrFiles'].tolist()
        x_positions = list(range(len(test_labels)))
        
        # Plot each metric in a separate subplot
        for metric_idx, metric in enumerate(metrics_to_plot):
            ax = axes[metric_idx]
            column_name, y_label = metric_map[metric]
            
            if column_name not in config_df.columns:
                print(f"Warning: Column '{column_name}' not found in CSV")
                continue
            
            # Extract metric values
            y_values = []
            valid_x = []
            for idx, (x_pos, val) in enumerate(zip(x_positions, config_df[column_name])):
                if pd.notna(val) and val != '-':
                    y_values.append(float(val))
                    valid_x.append(x_pos)
            
            if not y_values:
                print(f"Warning: No valid data for metric {metric} in config {config}")
                continue
            
            # Plot the data
            ax.plot(valid_x, y_values, 
                    marker=markers[config_idx % len(markers)],
                    linestyle='-',
                    linewidth=2.5, 
                    markersize=8, 
                    color=colors[config_idx],
                    alpha=0.8,
                    label=config)
            
            # Customize subplot
            ax.set_xlabel('Test Case', fontsize=10, fontweight='bold')
            ax.set_ylabel(y_label, fontsize=10, fontweight='bold')
            ax.set_title(f'{y_label} - Config: {config}', fontsize=12, fontweight='bold')
            ax.legend(loc='best', fontsize=9)
            ax.grid(True, alpha=0.3, linestyle='--')
            
            # Set x-axis labels
            ax.set_xticks(x_positions)
            ax.set_xticklabels(test_labels, rotation=90, ha='right', fontsize=7)
        
        # Add overall figure title
        fig.suptitle(f'Performance Metrics for Config: {config}\\nX-axis: BS|FileSize|IODepth|IOType|Jobs|NrFiles  •  Sorted by: IO Type, Threads, File Size', 
                     fontsize=13, fontweight='bold', y=0.995)
        
        # Adjust layout to prevent label cutoff
        plt.tight_layout(rect=[0, 0, 1, 0.99])  # Leave space for suptitle
        
        # Save plot with config name in filename
        safe_config_name = config.replace('/', '_').replace(' ', '_')
        output_file = os.path.join(output_dir, f"{output_basename}_{safe_config_name}.png")
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"  Saved: {output_file}")
        plt.close(fig)
    
    print(f"\n✓ Generated {len(configs)} per-config plots in {output_dir}/")


def plot_combined_mode_single_file(csv_file, output_file, metrics_to_plot):
    """Plot a single CSV file in combined mode - if it has Config column, plot each config as separate series"""
    
    if not os.path.exists(csv_file):
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)
    
    print(f"Plotting single CSV file: {csv_file}")
    
    # Read the CSV
    df = pd.read_csv(csv_file)
    
    # Check if it has a Config column
    has_config_column = 'Config' in df.columns
    
    if has_config_column:
        # Group by config and plot each as a separate series
        configs = df['Config'].unique()
        print(f"Found {len(configs)} configs: {', '.join(configs)}")
        csv_groups = [(config, df[df['Config'] == config]) for config in configs]
    else:
        # Single dataset
        file_name = os.path.basename(csv_file).replace('.csv', '')
        csv_groups = [(file_name, df)]
    
    # Map metric argument to column name and display info
    metric_map = {
        'read_bw': ('Read BW (MB/s)', 'Read Throughput (MB/s)'),
        'write_bw': ('Write BW (MB/s)', 'Write Throughput (MB/s)'),
        'avg_cpu': ('Avg CPU (%)', 'Average GCSFuse CPU (%)'),
        'peak_cpu': ('Peak CPU (%)', 'Peak GCSFuse CPU (%)'),
        'avg_mem': ('Avg Mem (MB)', 'Average GCSFuse Memory (MB)'),
        'peak_mem': ('Peak Mem (MB)', 'Peak GCSFuse Memory (MB)'),
        'avg_page_cache': ('Avg PgCache (GB)', 'Average Page Cache (GB)'),
        'peak_page_cache': ('Peak PgCache (GB)', 'Peak Page Cache (GB)'),
        'avg_sys_cpu': ('Avg Sys CPU (%)', 'Average System CPU (%)'),
        'peak_sys_cpu': ('Peak Sys CPU (%)', 'Peak System CPU (%)')
    }
    
    # Determine subplot layout based on number of metrics (vertical arrangement)
    num_metrics = len(metrics_to_plot)
    nrows, ncols = num_metrics, 1
    fig_height = 8 * num_metrics  # 8 inches per subplot
    
    # Create figure with subplots
    fig, axes = plt.subplots(nrows, ncols, figsize=(20, fig_height))
    
    # Flatten axes array for easier iteration
    if num_metrics == 1:
        axes = [axes]
    else:
        axes = axes.flatten()
    
    # Color palette and line styles
    colors = plt.cm.tab10(range(len(csv_groups)))
    line_styles = ['-', '--', '-.', ':', '-', '--', '-.', ':', '-', '--']
    markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p', '*', 'h']
    
    # Plot each metric in a separate subplot
    for metric_idx, metric in enumerate(metrics_to_plot):
        ax = axes[metric_idx]
        column_name, y_label = metric_map[metric]
        
        all_data = []
        
        # Read and process each group (config or file)
        for idx, (group_name, group_df) in enumerate(csv_groups):
            # Extract relevant columns
            if 'BS|FSize|IOD|IOType|Jobs|NrFiles' in group_df.columns and column_name in group_df.columns:
                # Create data with sort key
                for _, row in group_df.iterrows():
                    param_str = row['BS|FSize|IOD|IOType|Jobs|NrFiles']
                    metric_value = row[column_name]
                    
                    # Skip if metric_value is not a number or is '-'
                    if pd.isna(metric_value) or metric_value == '-':
                        continue
                    
                    all_data.append({
                        'param': param_str,
                        'metric_value': float(metric_value),
                        'group': group_name,
                        'sort_key': sort_key(param_str),
                        'color_idx': idx
                    })
        
        if not all_data:
            print(f"No valid data found for metric: {metric}")
            continue
        
        # Convert to DataFrame and sort
        plot_df = pd.DataFrame(all_data)
        plot_df = plot_df.sort_values('sort_key')
        
        # Get unique parameter strings in sorted order
        unique_params = plot_df['param'].unique()
        x_positions = {param: i for i, param in enumerate(unique_params)}
        
        # Plot each group's data
        for group_name in plot_df['group'].unique():
            group_data = plot_df[plot_df['group'] == group_name]
            color_idx = group_data['color_idx'].iloc[0]
            
            x_vals = [x_positions[p] for p in group_data['param']]
            y_vals = group_data['metric_value'].values
            
            ax.plot(x_vals, y_vals, 
                    marker=markers[color_idx % len(markers)],
                    linestyle=line_styles[color_idx % len(line_styles)],
                    linewidth=2.5, 
                    markersize=8, 
                    label=group_name, 
                    color=colors[color_idx],
                    alpha=0.8)
        
        # Draw thin red dotted lines connecting points at the same x position
        for x_pos in range(len(unique_params)):
            # Get all y values at this x position
            y_values = []
            for group_name in plot_df['group'].unique():
                group_data = plot_df[plot_df['group'] == group_name]
                param = unique_params[x_pos]
                matching = group_data[group_data['param'] == param]
                if not matching.empty:
                    y_values.append(matching['metric_value'].iloc[0])
            
            # Draw vertical line connecting all points at this x position
            if len(y_values) > 1:
                ax.plot([x_pos] * len(y_values), y_values, 
                        color='red', linestyle=':', linewidth=1.5, alpha=0.5, zorder=1)
        
        # Customize subplot
        ax.set_xlabel('Test Configuration (File Size | IO Type | Threads)', fontsize=10, fontweight='bold')
        ax.set_ylabel(y_label, fontsize=10, fontweight='bold')
        ax.set_title(f'{y_label} Comparison', fontsize=12, fontweight='bold')
        ax.legend(loc='best', fontsize=8)
        ax.grid(True, alpha=0.3, linestyle='--')
        
        # Set x-axis labels
        ax.set_xticks(range(len(unique_params)))
        ax.set_xticklabels(unique_params, rotation=90, ha='right', fontsize=7)
    
    # Add overall figure title
    fig.suptitle('X-axis: BS|FileSize|IODepth|IOType|Jobs|NrFiles  •  Sorted by: IO Type (randread → read), Threads (1→48→96), File Size (ascending)', 
                 fontsize=13, fontweight='bold', y=0.995)
    
    # Adjust layout to prevent label cutoff
    plt.tight_layout(rect=[0, 0, 1, 0.99])  # Leave space for suptitle
    
    # Save plot
    # Create parent directory if needed
    parent_dir = os.path.dirname(output_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"\nPlot saved to: {output_file}")


def plot_combined_mode(reports_dir, output_file, metrics_to_plot):
    """Original combined plotting mode - all configs on same graph"""
    
    # Find all CSV files in source directory
    csv_files = glob.glob(os.path.join(reports_dir, "*.csv"))
    
    if not csv_files:
        print(f"No CSV files found in {reports_dir}/")
        return
    
    print(f"Found {len(csv_files)} CSV files:")
    for f in csv_files:
        print(f"  - {os.path.basename(f)}")
    
    # Determine subplot layout based on number of metrics (vertical arrangement)
    num_metrics = len(metrics_to_plot)
    nrows, ncols = num_metrics, 1
    fig_height = 8 * num_metrics  # 8 inches per subplot
    
    # Create figure with subplots
    fig, axes = plt.subplots(nrows, ncols, figsize=(20, fig_height))
    
    # Flatten axes array for easier iteration
    if num_metrics == 1:
        axes = [axes]
    else:
        axes = axes.flatten()
    
    # Color palette and line styles
    colors = plt.cm.tab10(range(len(csv_files)))
    line_styles = ['-', '--', '-.', ':', '-', '--', '-.', ':', '-', '--']
    markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p', '*', 'h']
    
    # Map metric argument to column name and display info
    metric_map = {
        'read_bw': ('Read BW (MB/s)', 'Read Throughput (MB/s)'),
        'write_bw': ('Write BW (MB/s)', 'Write Throughput (MB/s)'),
        'avg_cpu': ('Avg CPU (%)', 'Average GCSFuse CPU (%)'),
        'peak_cpu': ('Peak CPU (%)', 'Peak GCSFuse CPU (%)'),
        'avg_mem': ('Avg Mem (MB)', 'Average GCSFuse Memory (MB)'),
        'peak_mem': ('Peak Mem (MB)', 'Peak GCSFuse Memory (MB)'),
        'avg_page_cache': ('Avg PgCache (GB)', 'Average Page Cache (GB)'),
        'peak_page_cache': ('Peak PgCache (GB)', 'Peak Page Cache (GB)'),
        'avg_sys_cpu': ('Avg Sys CPU (%)', 'Average System CPU (%)'),
        'peak_sys_cpu': ('Peak Sys CPU (%)', 'Peak System CPU (%)')
    }
    
    # Plot each metric in a separate subplot
    for metric_idx, metric in enumerate(metrics_to_plot):
        ax = axes[metric_idx]
        column_name, y_label = metric_map[metric]
        
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
            print(f"No valid data found for metric: {metric}")
            continue
        
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
            
            ax.plot(x_vals, y_vals, 
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
                ax.plot([x_pos] * len(y_values), y_values, 
                        color='red', linestyle=':', linewidth=1.5, alpha=0.5, zorder=1)
        
        # Customize subplot
        ax.set_xlabel('Test Configuration (File Size | IO Type | Threads)', fontsize=10, fontweight='bold')
        ax.set_ylabel(y_label, fontsize=10, fontweight='bold')
        ax.set_title(f'{y_label} Comparison', fontsize=12, fontweight='bold')
        ax.legend(loc='best', fontsize=8)
        ax.grid(True, alpha=0.3, linestyle='--')
        
        # Set x-axis labels
        ax.set_xticks(range(len(unique_params)))
        ax.set_xticklabels(unique_params, rotation=90, ha='right', fontsize=7)
    
    # Add overall figure title explaining x-axis and sort order
    fig.suptitle('X-axis: BS|FileSize|IODepth|IOType|Jobs|NrFiles  •  Sorted by: IO Type (randread → read), Threads (1→48→96), File Size (ascending)', 
                 fontsize=13, fontweight='bold', y=0.995)
    
    # Adjust layout to prevent label cutoff
    plt.tight_layout(rect=[0, 0, 1, 0.99])  # Leave space for suptitle
    
    # Save plot
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
