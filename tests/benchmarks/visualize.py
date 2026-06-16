import json
import os
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# ==========================================
# Production Infrastructure Color Palette
# ==========================================
plt.style.use('dark_background')
BG_DARK = '#0d1117'           # Main dark background
PANEL_DARK = '#161b22'        # Component panel color
COLOR_CYAN = '#00e5ff'        # Telemetry / VRAM trends
COLOR_GREEN = '#39ff14'       # Active / HTTP 200 OK
COLOR_PINK = '#ff0055'        # Latency spikes / HTTP 429
COLOR_ORANGE = '#ff6600'      # Soft-throttling alerts

plt.rcParams.update({
    'figure.facecolor': BG_DARK,
    'axes.facecolor': BG_DARK,
    'axes.edgecolor': '#30363d',
    'axes.labelcolor': '#8b949e',
    'text.color': '#c9d1d9',
    'xtick.color': '#8b949e',
    'ytick.color': '#8b949e',
    'grid.color': '#21262d',
    'grid.alpha': 0.5,
    'font.family': 'sans-serif',
    'font.weight': 'bold',
})

def read_logs(file_path):
    """Parses structural JSONL benchmarking logs into a pandas DataFrame."""
    data = []
    if not os.path.exists(file_path):
        print(f"[!] Warning: Log file {file_path} not found.")
        return pd.DataFrame()
        
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    if not data:
        return pd.DataFrame()
        
    df = pd.DataFrame(data)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['time_rel'] = (df['timestamp'] - df['timestamp'].min()).dt.total_seconds()
    return df

def plot_latency_comparison(df_base, df_opt, output_dir):
    """Generates bar chart comparing Head-of-Line (HoL) blocking alleviation."""
    if df_base.empty or df_opt.empty:
        print("[!] Warning: Missing baseline or optimized datasets for latency plotting.")
        return

    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    
    # Isolate successful executions to measure valid operational latency
    base_light = df_base[(df_base['req_type'] == 'LIGHTWEIGHT') & (df_base['status'] == 200)]['latency_s']
    base_heavy = df_base[(df_base['req_type'] == 'HEAVY') & (df_base['status'] == 200)]['latency_s']
    
    opt_light = df_opt[(df_opt['req_type'] == 'LIGHTWEIGHT') & (df_opt['status'] == 200)]['latency_s']
    opt_heavy = df_opt[(df_opt['req_type'] == 'HEAVY') & (df_opt['status'] == 200)]['latency_s']
    
    # Calculate exact P95 percentile metrics
    metrics = {
        'Baseline (Single Queue)': [
            base_light.quantile(0.95) if not base_light.empty else 0,
            base_heavy.quantile(0.95) if not base_heavy.empty else 0
        ],
        'Optimized (Dual Queues)': [
            opt_light.quantile(0.95) if not opt_light.empty else 0,
            opt_heavy.quantile(0.95) if not opt_heavy.empty else 0
        ]
    }
    
    categories = ['Lightweight Chat', 'Heavy Context']
    x = range(len(categories))
    width = 0.35
    
    bars1 = ax.bar([i - width/2 for i in x], metrics['Baseline (Single Queue)'], width, 
                   label='Baseline (Single Queue)', color='#30363d', edgecolor='#8b949e', linewidth=1.5)
    
    bars2 = ax.bar([i + width/2 for i in x], metrics['Optimized (Dual Queues)'], width, 
                   label='Airi Router (Dual Queues)', color=COLOR_GREEN, edgecolor='white', linewidth=1)
    
    # Layered blending for visualization clarity
    for bar in bars2:
        bar.set_alpha(0.85)
        ax.bar(bar.get_x() + bar.get_width()/2, bar.get_height(), width, color=COLOR_GREEN, alpha=0.3, zorder=0, align='center')
    
    ax.set_ylabel('P95 Latency (Seconds)', fontsize=12, fontweight='bold')
    ax.set_title('Head-of-Line Blocking Elimination', fontsize=16, fontweight='black', pad=20, color='white')
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=12)
    ax.legend(facecolor=PANEL_DARK, edgecolor='#30363d')
    
    # Add numerical metadata callouts above the bars
    for bar in bars1 + bars2:
        height = bar.get_height()
        if height > 0:
            ax.annotate(f'{height:.1f}s',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 5), textcoords="offset points",
                        ha='center', va='bottom', color='white', fontweight='bold')

    plt.tight_layout()
    out_path = os.path.join(output_dir, 'vs_latency_reduction.png')
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none')
    print(f"[*] Latency comparison chart exported to: {out_path}")


def plot_vram_backpressure(df_opt, output_dir):
    """Plots the VRAM allocation trajectory overlaid with circuit breaker throttling events."""
    if df_opt.empty:
        print("[!] Warning: Optimized dataset is empty. Aborting VRAM telemetry plot.")
        return
        
    fig, ax1 = plt.subplots(figsize=(12, 6), dpi=150)
    
    # --- VRAM Allocation Area Path ---
    vram_df = df_opt[df_opt['vram_percent'] > 0]
    if not vram_df.empty:
        sns.lineplot(data=vram_df, x='time_rel', y='vram_percent', color=COLOR_CYAN, linewidth=2.5, ax=ax1, label='VRAM Usage (%)')
        ax1.fill_between(vram_df['time_rel'], vram_df['vram_percent'], color=COLOR_CYAN, alpha=0.1)
    
    # Threshold Indicators
    ax1.axhline(75.0, color=COLOR_ORANGE, linestyle='--', linewidth=1.5, alpha=0.8, label='WARNING (75%)')
    ax1.axhline(85.0, color=COLOR_PINK, linestyle='-.', linewidth=2, alpha=0.8, label='DANGER (85%)')
    
    ax1.set_xlabel('Time (Seconds)', fontsize=12, fontweight='bold')
    ax1.set_ylabel('GPU VRAM Allocation (%)', color=COLOR_CYAN, fontsize=12, fontweight='bold')
    ax1.set_ylim(0, 100)
    
    # --- Request Lifecycle Event Distribution (Dual Y-Axis) ---
    ax2 = ax1.twinx()
    throttled = df_opt[df_opt['status'] == 429]
    success = df_opt[df_opt['status'] == 200]
    
    if not success.empty:
        ax2.scatter(success['time_rel'], success['latency_s'], color=COLOR_GREEN, s=30, alpha=0.7, label='200 OK (Processed)', edgecolors='none')
    if not throttled.empty:
        ax2.scatter(throttled['time_rel'], throttled['latency_s'], color=COLOR_PINK, s=50, marker='X', alpha=0.9, label='429 Throttled (Protected)')
    
    ax2.set_ylabel('Request Latency (Seconds)', color='#c9d1d9', fontsize=12, fontweight='bold')
    max_lat = df_opt['latency_s'].max() if not df_opt.empty else 10
    ax2.set_ylim(-0.5, max_lat + 1)
    
    # Unified Legend Merging
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left', frameon=True, facecolor=PANEL_DARK, edgecolor='#30363d')
    
    plt.title('Airi Router: VRAM Adaptive Backpressure Lifecycle', fontsize=16, fontweight='black', pad=20, color='white')
    plt.tight_layout()
    
    out_path = os.path.join(output_dir, 'vs_vram_backpressure.png')
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none')
    print(f"[*] VRAM backpressure lifecycle chart exported to: {out_path}")

def main():
    parser = argparse.ArgumentParser(description='Airi Router Benchmark Data Visualizer')
    parser.add_argument('--baseline', type=str, default='tests/benchmarks/logs/baseline_metrics.jsonl', help='Path to baseline JSONL logs')
    parser.add_argument('--optimized', type=str, default='tests/benchmarks/logs/optimized_metrics.jsonl', help='Path to optimized JSONL logs')
    parser.add_argument('--out', type=str, default='tests/benchmarks/logs', help='Output directory for charts')
    args = parser.parse_args()
    
    print("="*60)
    print(" AIRI ROUTER PRODUCTION TELEMETRY VISUALIZER")
    print("="*60)
    
    if not os.path.exists(args.out):
        os.makedirs(args.out)
        
    df_base = read_logs(args.baseline)
    df_opt = read_logs(args.optimized)
    
    if df_base.empty and df_opt.empty:
        print("[!] Error: No log data resolved. Please check benchmark script outputs.")
        return
        
    print(f"[*] Loaded Baseline Data: {len(df_base)} historical records resolved.")
    print(f"[*] Loaded Optimized Data: {len(df_opt)} historical records resolved.")
    
    plot_latency_comparison(df_base, df_opt, args.out)
    plot_vram_backpressure(df_opt, args.out)
    
    print("="*60)
    print("[+] Telemetry pipeline complete. Benchmark analytics successfully synchronized.")

if __name__ == '__main__':
    main()