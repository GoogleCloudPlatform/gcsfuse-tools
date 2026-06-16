// State management
let activeTab = 'launch';
let activeRuns = [];
let selectedActiveRunId = null;
let logPollInterval = null;
let activeRunsPollInterval = null;
let charts = {}; // references to Chart.js instances

// Run comparison state
let comparedData = null;

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    fetchConfigFiles();
    startPollingActiveRuns();
    fetchHistory();
});

// Tab switcher
function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-section').forEach(s => s.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.remove('bg-blue-600', 'text-white');
        b.classList.add('text-slate-400', 'hover:text-white', 'hover:bg-slate-800');
    });

    const activeSection = document.getElementById(`section-${tab}`);
    if (activeSection) activeSection.classList.remove('hidden');

    const activeBtn = document.getElementById(`tab-${tab}`);
    if (activeBtn) {
        activeBtn.classList.add('bg-blue-600', 'text-white');
        activeBtn.classList.remove('text-slate-400', 'hover:text-white', 'hover:bg-slate-800');
    }

    if (tab === 'history') {
        fetchHistory();
    }
}

// Toggle config mode fields
function toggleConfigMode() {
    const mode = document.querySelector('input[name="config_mode"]:checked').value;
    const configsCsvContainer = document.getElementById('configs-csv-container');
    const singleConfigContainer = document.getElementById('single-config-container');
    
    if (mode === 'multi') {
        configsCsvContainer.classList.remove('hidden');
        singleConfigContainer.classList.add('hidden');
        document.getElementById('configs_csv').setAttribute('required', 'true');
    } else {
        configsCsvContainer.classList.add('hidden');
        singleConfigContainer.classList.remove('hidden');
        document.getElementById('configs_csv').removeAttribute('required');
    }
}

// Fetch test configurations from server
async function fetchConfigFiles() {
    try {
        const res = await fetch('/api/configs/files');
        const data = await res.json();

        // Populate drop-downs
        populateSelect('test_csv', data.test_cases, 'Select test cases...');
        populateSelect('fio_job', data.fio_jobs, 'Select FIO job...');
        
        // Filter test cases to extract mount config files
        const mountConfigs = data.test_cases.filter(f => f.includes('mount_config') || f.includes('mount_args') || f.includes('config'));
        populateSelect('configs_csv', mountConfigs, 'Select mount configs...');
    } catch (e) {
        console.error("Failed to load configs:", e);
    }
}

function populateSelect(id, list, placeholder) {
    const el = document.getElementById(id);
    el.innerHTML = `<option value="">-- ${placeholder} --</option>`;
    list.forEach(item => {
        el.innerHTML += `<option value="${item}">${item}</option>`;
    });
}

// Submit run config
async function submitRun(event) {
    event.preventDefault();

    const mode = document.querySelector('input[name="config_mode"]:checked').value;
    const payload = {
        username: document.getElementById('username').value.trim(),
        description: document.getElementById('description').value.trim(),
        executor_vm: document.getElementById('executor_vm').value.trim(),
        zone: document.getElementById('zone').value.trim(),
        project: document.getElementById('project').value.trim(),
        test_csv: document.getElementById('test_csv').value,
        fio_job: document.getElementById('fio_job').value,
        iterations: parseInt(document.getElementById('iterations').value) || 2
    };

    if (mode === 'multi') {
        payload.configs_csv = document.getElementById('configs_csv').value;
    } else {
        payload.commit_hash = document.getElementById('commit_hash').value.trim() || 'master';
        payload.mount_args = document.getElementById('mount_args').value.trim();
    }

    try {
        const res = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            alert("Benchmark run enqueued successfully!");
            switchTab('active');
            pollActiveRuns();
        } else {
            const error = await res.json();
            alert(`Error enqueuing run: ${error.detail}`);
        }
    } catch (e) {
        alert(`Failed to submit: ${e}`);
    }
}

// Polling Active Jobs & Console logs
function startPollingActiveRuns() {
    pollActiveRuns();
    activeRunsPollInterval = setInterval(pollActiveRuns, 5000);
}

async function pollActiveRuns() {
    try {
        const res = await fetch('/api/runs/active');
        const data = await res.json();
        activeRuns = data;

        // Update active badge count
        const badge = document.getElementById('active-badge');
        if (data.length > 0) {
            badge.innerText = data.length;
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }

        renderActiveList();
    } catch (e) {
        console.error("Failed to poll active runs:", e);
    }
}

function renderActiveList() {
    const list = document.getElementById('active-list');
    if (activeRuns.length === 0) {
        list.innerHTML = `<p class="text-slate-400 text-sm italic">No active or queued runs.</p>`;
        stopPollingLogs();
        return;
    }

    list.innerHTML = '';
    activeRuns.forEach(run => {
        const isSelected = run.benchmark_id === selectedActiveRunId;
        const statusColors = {
            'queued': 'bg-orange-500/10 text-orange-400 border-orange-500/20',
            'running': 'bg-blue-500/10 text-blue-400 border-blue-500/20 animate-pulse'
        };

        const item = document.createElement('div');
        item.className = `p-4 border rounded-xl cursor-pointer transition ${
            isSelected ? 'bg-slate-700/50 border-blue-500' : 'bg-slate-800/40 border-slate-700 hover:bg-slate-800/80'
        }`;
        item.onclick = () => selectActiveRun(run.benchmark_id);

        item.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <span class="font-mono text-xs text-slate-400">${run.benchmark_id}</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${statusColors[run.status] || 'bg-slate-700'}">${run.status}</span>
            </div>
            <h4 class="font-bold text-sm text-slate-200 mb-1 truncate">${run.description}</h4>
            <p class="text-xs text-slate-400 mb-2">VM: <span class="font-mono font-bold">${run.executor_vm}</span></p>
            <div class="flex justify-between items-center text-[10px] text-slate-500">
                <span>User: ${run.username}</span>
                <button onclick="cancelRun(event, '${run.benchmark_id}')" class="text-red-400 hover:text-red-300 font-bold uppercase transition"><i class="fa-solid fa-ban mr-1"></i>Cancel</button>
            </div>
        `;
        list.appendChild(item);
    });

    // Default select first item if none selected
    if (!selectedActiveRunId && activeRuns.length > 0) {
        selectActiveRun(activeRuns[0].benchmark_id);
    }
}

function selectActiveRun(id) {
    selectedActiveRunId = id;
    renderActiveList();
    startPollingLogs(id);
}

function startPollingLogs(id) {
    stopPollingLogs();
    pollLogs(id);
    logPollInterval = setInterval(() => pollLogs(id), 3000);
}

function stopPollingLogs() {
    if (logPollInterval) {
        clearInterval(logPollInterval);
        logPollInterval = null;
    }
}

async function pollLogs(id) {
    try {
        const res = await fetch(`/api/runs/${id}/logs`);
        const data = await res.json();
        
        const consoleEl = document.getElementById('logs-console');
        document.getElementById('live-console-meta').innerText = `ID: ${id}`;
        
        consoleEl.innerText = data.logs || "No logs available yet.";
        // Auto scroll to bottom
        consoleEl.scrollTop = consoleEl.scrollHeight;
    } catch (e) {
        console.error("Failed to poll logs:", e);
    }
}

async function cancelRun(event, id) {
    event.stopPropagation(); // prevent selecting row
    if (!confirm(`Are you sure you want to cancel benchmark run ${id}?`)) return;

    try {
        const res = await fetch(`/api/runs/${id}/cancel`, { method: 'POST' });
        if (res.ok) {
            alert(`Cancellation signal sent for ${id}`);
            pollActiveRuns();
        }
    } catch (e) {
        alert(`Failed to cancel: ${e}`);
    }
}

// Fetch History Table
async function fetchHistory() {
    try {
        const res = await fetch('/api/runs/history');
        const data = await res.json();
        renderHistoryRows(data);
    } catch (e) {
        console.error("Failed to fetch history:", e);
    }
}

function renderHistoryRows(runs) {
    const tbody = document.getElementById('history-rows');
    if (runs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-400 italic">No historical runs found.</td></tr>`;
        return;
    }

    tbody.innerHTML = '';
    runs.forEach(run => {
        const dateStr = run.created_at ? new Date(run.created_at).toLocaleString() : 'N/A';
        const statusColors = {
            'completed': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
            'failed': 'bg-rose-500/10 text-rose-400 border-rose-500/20',
            'cancelled': 'bg-slate-500/10 text-slate-400 border-slate-500/20'
        };

        const tr = document.createElement('tr');
        tr.className = "hover:bg-slate-800/30 border-b border-slate-700/50";
        tr.innerHTML = `
            <td class="py-3 px-4 text-center"><input type="checkbox" name="compare-select" value="${run.benchmark_id}" class="compare-chk"></td>
            <td class="py-3 px-4 font-mono font-bold text-slate-300">${run.benchmark_id}</td>
            <td class="py-3 px-4 font-medium text-slate-200">${run.description}</td>
            <td class="py-3 px-4 text-slate-400">${run.username}</td>
            <td class="py-3 px-4 font-mono text-slate-400">${run.executor_vm}</td>
            <td class="py-3 px-4 text-slate-400 text-xs">${dateStr}</td>
            <td class="py-3 px-4"><span class="text-[10px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${statusColors[run.status] || 'bg-slate-700'}">${run.status}</span></td>
            <td class="py-3 px-4 text-center space-x-3">
                <button onclick="cloneRun('${run.benchmark_id}')" class="text-blue-400 hover:text-blue-300 transition" title="Clone configurations"><i class="fa-solid fa-copy"></i></button>
                <button onclick="expandRunDetails(this, '${run.benchmark_id}')" class="text-slate-400 hover:text-slate-200 transition" title="View details"><i class="fa-solid fa-chevron-down"></i></button>
            </td>
        `;
        tbody.appendChild(tr);

        // Add hidden expandable row
        const detailsTr = document.createElement('tr');
        detailsTr.id = `details-${run.benchmark_id}`;
        detailsTr.className = "hidden bg-slate-900/40 border-b border-slate-700/50";
        detailsTr.innerHTML = `
            <td colspan="8" class="py-4 px-6">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-6 text-xs text-slate-400 leading-loose">
                    <div>
                        <span class="font-bold text-slate-300 uppercase tracking-wider block mb-1">GCSFuse Configs</span>
                        <p>Commit: <span class="font-mono text-slate-200">${run.commit_hash}</span></p>
                        <p>Mount Args: <span class="font-mono text-slate-200">${run.mount_args || 'Used mount configs CSV'}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-300 uppercase tracking-wider block mb-1">Files Run</span>
                        <p>CSV: <span class="font-mono text-slate-200">${run.test_csv_name}</span></p>
                        <p>Configs CSV: <span class="font-mono text-slate-200">${run.configs_csv_name || 'N/A'}</span></p>
                        <p>FIO Job: <span class="font-mono text-slate-200">${run.fio_job_name}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-300 uppercase tracking-wider block mb-1">Scope Info</span>
                        <p>Project: <span class="text-slate-200">${run.project}</span></p>
                        <p>Zone: <span class="text-slate-200">${run.zone}</span></p>
                        <p>Iterations: <span class="text-slate-200">${run.iterations}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-300 uppercase tracking-wider block mb-1">Timestamps</span>
                        <p>Created: <span class="text-slate-200">${run.created_at}</span></p>
                        <p>Started: <span class="text-slate-200">${run.started_at || 'N/A'}</span></p>
                        <p>Finished: <span class="text-slate-200">${run.completed_at || 'N/A'}</span></p>
                    </div>
                </div>
            </td>
        `;
        tbody.appendChild(detailsTr);
    });
}

function expandRunDetails(btn, id) {
    const row = document.getElementById(`details-${id}`);
    const icon = btn.querySelector('i');
    if (row.classList.contains('hidden')) {
        row.classList.remove('hidden');
        icon.className = "fa-solid fa-chevron-up";
    } else {
        row.classList.add('hidden');
        icon.className = "fa-solid fa-chevron-down";
    }
}

// Clone configurations to launch panel
async function cloneRun(id) {
    try {
        const res = await fetch(`/api/runs/${id}/config`);
        const run = await res.json();

        // Load inputs
        document.getElementById('username').value = run.username;
        document.getElementById('description').value = `Cloned: ${run.description}`;
        document.getElementById('executor_vm').value = run.executor_vm;
        document.getElementById('zone').value = run.zone;
        document.getElementById('project').value = run.project;
        document.getElementById('iterations').value = run.iterations;

        // Force dropdown options selection
        document.getElementById('test_csv').value = run.test_csv_name;
        document.getElementById('fio_job').value = run.fio_job_name;

        if (run.configs_csv_name) {
            document.querySelector('input[name="config_mode"][value="multi"]').checked = true;
            toggleConfigMode();
            document.getElementById('configs_csv').value = run.configs_csv_name;
        } else {
            document.querySelector('input[name="config_mode"][value="single"]').checked = true;
            toggleConfigMode();
            document.getElementById('commit_hash').value = run.commit_hash;
            document.getElementById('mount_args').value = run.mount_args;
        }

        switchTab('launch');
    } catch (e) {
        alert(`Failed to clone configuration: ${e}`);
    }
}

// Checkboxes Table Toggle Select All
function toggleSelectAll(master) {
    document.querySelectorAll('.compare-chk').forEach(c => c.checked = master.checked);
}

// Compare Selected runs via API & Plotting
async function compareSelected() {
    const selectedIds = Array.from(document.querySelectorAll('.compare-chk:checked')).map(c => c.value);
    if (selectedIds.length < 1) {
        alert("Please select at least 1 benchmark run to visualize.");
        return;
    }

    try {
        const res = await fetch(`/api/runs/compare?ids=${selectedIds.join(',')}`);
        if (!res.ok) throw new Error("Comparison request failed");
        
        comparedData = await res.json();
        
        document.getElementById('chart-workspace').classList.remove('hidden');
        replotCharts();
        
        // Scroll workspace into view
        document.getElementById('chart-workspace').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        alert(`Failed to compare runs: ${e}`);
    }
}

// Plotting Engine using Chart.js
function replotCharts() {
    if (!comparedData) return;

    const xAxisMode = document.getElementById('chart-x-axis').value;

    // Destructure data for comparison
    const runIds = Object.keys(comparedData);
    
    // Get unique list of test case parameters (e.g. read|1|1g|1m|1|1|0)
    const allParams = new Set();
    runIds.forEach(id => {
        comparedData[id].forEach(row => allParams.add(row.param_str));
    });
    const sortedParams = Array.from(allParams).sort();

    // Get unique list of configs (e.g. grpc, http1)
    const allConfigs = new Set();
    runIds.forEach(id => {
        comparedData[id].forEach(row => allConfigs.add(row.config));
    });
    const sortedConfigs = Array.from(allConfigs).sort();

    let labels = [];
    let datasetsBw = [];
    let datasetsLat = [];

    // Chart.js color palette
    const colors = [
        '#3b82f6', '#10b981', '#ef4444', '#f97316', '#8b5cf6', '#ec4899', '#f59e0b', '#06b6d4'
    ];

    if (xAxisMode === 'test-cases') {
        // X-axis: Test Cases
        // Series/Legend: RunID + Config combination
        labels = sortedParams;

        let seriesIdx = 0;
        runIds.forEach(runId => {
            sortedConfigs.forEach(conf => {
                const bwData = [];
                const latData = [];
                let hasData = false;

                sortedParams.forEach(param => {
                    // Find matching row for this run, config, and test case
                    const match = comparedData[runId].find(r => r.param_str === param && r.config === conf);
                    if (match) {
                        hasData = true;
                        bwData.push(match.read_bw || match.write_bw || 0);
                        latData.push(match.read_lat || match.write_lat || 0);
                    } else {
                        bwData.push(0);
                        latData.push(0);
                    }
                });

                if (hasData) {
                    const labelName = runIds.length === 1 ? conf : `${runId} (${conf})`;
                    const color = colors[seriesIdx % colors.length];

                    datasetsBw.push({
                        label: labelName,
                        data: bwData,
                        backgroundColor: color + '90', // alpha transparency for bars
                        borderColor: color,
                        borderWidth: 1.5
                    });

                    datasetsLat.push({
                        label: labelName,
                        data: latData,
                        fill: false,
                        borderColor: color,
                        tension: 0.2,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    seriesIdx++;
                }
            });
        });
    } else {
        // X-axis: Configurations (grpc, http1)
        // Series/Legend: RunID + Test Case combination
        labels = sortedConfigs;

        let seriesIdx = 0;
        runIds.forEach(runId => {
            sortedParams.forEach(param => {
                const bwData = [];
                const latData = [];
                let hasData = false;

                sortedConfigs.forEach(conf => {
                    const match = comparedData[runId].find(r => r.param_str === param && r.config === conf);
                    if (match) {
                        hasData = true;
                        bwData.push(match.read_bw || match.write_bw || 0);
                        latData.push(match.read_lat || match.write_lat || 0);
                    } else {
                        bwData.push(0);
                        latData.push(0);
                    }
                });

                if (hasData) {
                    const labelName = runIds.length === 1 ? param : `${runId} [${param.split('|').slice(0,4).join('|')}]`;
                    const color = colors[seriesIdx % colors.length];

                    datasetsBw.push({
                        label: labelName,
                        data: bwData,
                        backgroundColor: color + '90',
                        borderColor: color,
                        borderWidth: 1.5
                    });

                    datasetsLat.push({
                        label: labelName,
                        data: latData,
                        fill: false,
                        borderColor: color,
                        tension: 0.2,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    seriesIdx++;
                }
            });
        });
    }

    // Render Throughput Chart (Bar Chart)
    renderChart('throughput-chart', 'bar', labels, datasetsBw, 'Throughput (MB/s)');
    
    // Render Latency Chart (Line Chart)
    renderChart('latency-chart', 'line', labels, datasetsLat, 'Latency (ms)');
}

function renderChart(canvasId, type, labels, datasets, yLabel) {
    if (charts[canvasId]) {
        charts[canvasId].destroy(); // Destroy previous instance
    }

    const ctx = document.getElementById(canvasId).getContext('2d');
    charts[canvasId] = new Chart(ctx, {
        type: type,
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    grid: { color: '#33415530' },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 9 },
                        maxRotation: 60,
                        minRotation: 30
                    }
                },
                y: {
                    grid: { color: '#33415530' },
                    ticks: { color: '#94a3b8' },
                    title: {
                        display: true,
                        text: yLabel,
                        color: '#94a3b8',
                        font: { weight: 'bold' }
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#cbd5e1',
                        font: { size: 9 },
                        boxWidth: 12
                    }
                },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: '#334155',
                    borderWidth: 1
                }
            }
        }
    });
}
