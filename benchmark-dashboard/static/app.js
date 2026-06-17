// State management
let activeTab = 'launch';
let activeRuns = [];
let selectedActiveRunId = null;
let logPollInterval = null;
let activeRunsPollInterval = null;
let progressPollInterval = null;
let charts = {}; // references to Chart.js instances

// Run comparison state
let comparedData = null;
let localProject = null;

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    checkAuthentication();
    fetchConfigFiles();
    detectLocalProject();
    startPollingActiveRuns();
    fetchHistory();

    const execVmEl = document.getElementById('executor_vm');
    if (execVmEl) {
        execVmEl.addEventListener('blur', (e) => {
            resolveTargetVMDetails(e.target.value.trim());
        });
    }
});

async function resolveTargetVMDetails(vmName) {
    if (!vmName) return;
    
    const projEl = document.getElementById('project');
    const zoneEl = document.getElementById('zone');
    
    try {
        if (projEl) projEl.value = "Detecting project...";
        if (zoneEl) {
            zoneEl.value = "Detecting zone...";
            zoneEl.disabled = true;
            zoneEl.className = "w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-slate-500 cursor-not-allowed text-sm";
        }
        
        const res = await fetch(`/api/configs/detect-project?name=${encodeURIComponent(vmName)}`);
        const data = await res.json();
        
        if (projEl) projEl.value = data.project || "";
        
        if (zoneEl) {
            if (data.zone) {
                zoneEl.value = data.zone;
                zoneEl.disabled = true;
                zoneEl.className = "w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-slate-500 cursor-not-allowed text-sm";
            } else {
                zoneEl.value = "";
                zoneEl.disabled = false;
                zoneEl.placeholder = "e.g. us-central1-c";
                zoneEl.className = "w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-slate-800 focus:outline-none focus:border-blue-600 transition text-sm cursor-text";
            }
        }
    } catch (e) {
        console.error("Failed GCE project detection:", e);
        if (projEl) projEl.value = localProject || "gcs-fuse-test";
        if (zoneEl) {
            zoneEl.value = "";
            zoneEl.disabled = false;
            zoneEl.placeholder = "e.g. us-central1-c";
            zoneEl.className = "w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-slate-800 focus:outline-none focus:border-blue-600 transition text-sm cursor-text";
        }
    }
}

async function detectLocalProject() {
    try {
        const res = await fetch('/api/configs/project');
        const data = await res.json();
        localProject = data.project;
        
        const projEl = document.getElementById('project');
        if (projEl) {
            projEl.value = localProject;
        }
    } catch (e) {
        console.error("Failed to detect GCE project:", e);
    }
}

// Authentication System (LDAP)
function checkAuthentication() {
    const ldap = localStorage.getItem("ldap_user");
    const overlay = document.getElementById("signin-overlay");
    if (ldap) {
        overlay.classList.add("hidden");
        document.getElementById("nav-ldap-name").innerText = ldap;
        document.getElementById("nav-ldap-avatar").innerText = ldap.charAt(0);
        document.getElementById("username").value = ldap;
    } else {
        overlay.classList.remove("hidden");
    }
}

function handleSignIn(event) {
    event.preventDefault();
    const ldap = document.getElementById("ldap_input").value.trim().toLowerCase();
    if (ldap) {
        localStorage.setItem("ldap_user", ldap);
        checkAuthentication();
    }
}

function handleSignOut() {
    if (confirm("Are you sure you want to log out?")) {
        localStorage.removeItem("ldap_user");
        checkAuthentication();
    }
}

// Tab switcher
function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-section').forEach(s => s.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.remove('bg-blue-600', 'text-white', 'shadow-sm');
        b.classList.add('text-slate-600', 'hover:text-slate-900', 'hover:bg-slate-100');
    });

    const activeSection = document.getElementById(`section-${tab}`);
    if (activeSection) activeSection.classList.remove('hidden');

    const activeBtn = document.getElementById(`tab-${tab}`);
    if (activeBtn) {
        activeBtn.classList.add('bg-blue-600', 'text-white', 'shadow-sm');
        activeBtn.classList.remove('text-slate-600', 'hover:text-slate-900', 'hover:bg-slate-100');
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
    const previewWrapper = document.getElementById('configs-csv-preview-wrapper');
    
    if (mode === 'multi') {
        configsCsvContainer.classList.remove('hidden');
        singleConfigContainer.classList.add('hidden');
        previewWrapper.classList.remove('hidden');
        document.getElementById('configs_csv').setAttribute('required', 'true');
    } else {
        configsCsvContainer.classList.add('hidden');
        singleConfigContainer.classList.remove('hidden');
        previewWrapper.classList.add('hidden');
        document.getElementById('configs_csv').removeAttribute('required');
    }
}

// Fetch test configurations from server
async function fetchConfigFiles() {
    try {
        const res = await fetch('/api/configs/files');
        const data = await res.json();

        // Populate drop-downs
        // Filter out mount configs from the main test cases dropdown
        const testCases = data.test_cases.filter(f => !f.includes('custom_mount_configs') && !f.includes('mount_configs'));
        populateSelect('test_csv', testCases, 'Select test cases...');
        populateSelect('fio_job', data.fio_jobs, 'Select FIO job...');
        
        // Filter test cases to extract mount config files
        const mountConfigs = data.test_cases.filter(f => f.includes('mount_config') || f.includes('mount_args') || f.includes('config') || f.includes('custom_mount_configs'));
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
        username: localStorage.getItem("ldap_user") || "anonymous",
        description: document.getElementById('description').value.trim(),
        executor_vm: document.getElementById('executor_vm').value.trim(),
        zone: document.getElementById('zone').value.trim(),
        project: document.getElementById('project').value.trim(),
        artifacts_bucket: document.getElementById('artifacts_bucket').value.trim(),
        test_data_bucket: document.getElementById('test_data_bucket').value.trim(),
        test_csv: document.getElementById('test_csv').value,
        fio_job: document.getElementById('fio_job').value,
        iterations: parseInt(document.getElementById('iterations').value) || 2
    };

    if (document.getElementById('enable_mig_templates').checked) {
        payload.single_thread_vm_type = document.getElementById('single_thread_vm_type').value.trim() || null;
        payload.multi_thread_vm_type = document.getElementById('multi_thread_vm_type').value.trim() || null;
    }

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

// Polling Active Jobs & Console logs (Configured to 10s to keep it lightweight)
function startPollingActiveRuns() {
    pollActiveRuns();
    activeRunsPollInterval = setInterval(pollActiveRuns, 10000);
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
        stopPollingProgress();
        document.getElementById('monitor-progress-container').classList.add('hidden');
        return;
    }

    list.innerHTML = '';
    activeRuns.forEach(run => {
        const isSelected = run.benchmark_id === selectedActiveRunId;
        const statusColors = {
            'queued': 'bg-orange-100 text-orange-700 border-orange-200',
            'running': 'bg-blue-100 text-blue-700 border-blue-200 animate-pulse'
        };

        const item = document.createElement('div');
        item.className = `p-4 border rounded-xl cursor-pointer transition ${
            isSelected ? 'bg-blue-50/50 border-blue-500 shadow-sm' : 'bg-white border-slate-200 hover:bg-slate-50'
        }`;
        item.onclick = () => selectActiveRun(run.benchmark_id);

        item.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <span class="font-mono text-xs text-slate-500 font-bold">${run.benchmark_id}</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${statusColors[run.status] || 'bg-slate-100'}">${run.status}</span>
            </div>
            <h4 class="font-bold text-sm text-slate-800 mb-1 truncate">${run.description}</h4>
            <p class="text-xs text-slate-500 mb-2">VM: <span class="font-mono font-bold text-slate-700">${run.executor_vm}</span></p>
            <div class="flex justify-between items-center text-[10px] text-slate-400">
                <span>User: ${run.username}</span>
                <button onclick="cancelRun(event, '${run.benchmark_id}')" class="text-red-600 hover:text-red-800 font-bold uppercase transition"><i class="fa-solid fa-ban mr-1"></i>Cancel</button>
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
    startPollingProgress(id);
}

function startPollingLogs(id) {
    stopPollingLogs();
    pollLogs(id);
    logPollInterval = setInterval(() => pollLogs(id), 10000); // Poll logs every 10s
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

// GCS Progress Polling loop
function startPollingProgress(id) {
    stopPollingProgress();
    pollProgress(id);
    progressPollInterval = setInterval(() => pollProgress(id), 10000); // Poll GCS progress every 10s
}

function stopPollingProgress() {
    if (progressPollInterval) {
        clearInterval(progressPollInterval);
        progressPollInterval = null;
    }
}

async function pollProgress(id) {
    try {
        const res = await fetch(`/api/runs/${id}/progress`);
        const data = await res.json();
        
        const container = document.getElementById('monitor-progress-container');
        const checklist = document.getElementById('workers-checklist');
        
        if (data.total_jobs === 0) {
            container.classList.add('hidden');
            checklist.classList.add('hidden');
            return;
        }

        container.classList.remove('hidden');
        checklist.classList.remove('hidden');

        // Update progress bar
        const total = data.total_jobs;
        const sPct = (data.completed_jobs / total) * 100;
        const fPct = (data.failed_jobs / total) * 100;
        const pPct = (data.pending_jobs / total) * 100;

        document.getElementById('progress-bar-success').style.width = `${sPct}%`;
        document.getElementById('progress-bar-failed').style.width = `${fPct}%`;
        document.getElementById('progress-bar-pending').style.width = `${pPct}%`;

        document.getElementById('monitor-progress-text').innerText = `${data.completed_jobs} / ${total} Completed`;
        document.getElementById('progress-val-success').innerText = data.completed_jobs;
        document.getElementById('progress-val-failed').innerText = data.failed_jobs;
        document.getElementById('progress-val-pending').innerText = data.pending_jobs;

        // Render Workers checklist cards
        checklist.innerHTML = '';
        const vms = Object.keys(data.vms);
        vms.forEach(vm => {
            const progress = data.vms[vm];
            const vmStateColors = {
                'completed': 'text-emerald-600 bg-emerald-50 border-emerald-200',
                'running': 'text-blue-600 bg-blue-50 border-blue-200 animate-pulse',
                'failed': 'text-red-600 bg-red-50 border-red-200'
            };

            const vmStatusIcon = {
                'completed': '<i class="fa-solid fa-circle-check mr-1.5"></i>',
                'failed': '<i class="fa-solid fa-circle-xmark mr-1.5"></i>',
                'running': '<i class="fa-solid fa-spinner fa-spin mr-1.5"></i>'
            };

            const div = document.createElement('div');
            div.className = `p-2.5 border rounded-lg flex items-center justify-between ${vmStateColors[progress.status] || 'bg-slate-50 border-slate-200'}`;
            div.innerHTML = `
                <span class="font-semibold flex items-center truncate max-w-[150px]">
                    ${vmStatusIcon[progress.status] || '<i class="fa-solid fa-circle-notch mr-1.5"></i>'}
                    ${vm.split('-').slice(-2).join('-')}
                </span>
                <span class="font-mono text-[10px] font-bold">
                    ${progress.completed} / ${progress.total}
                </span>
            `;
            checklist.appendChild(div);
        });

    } catch (e) {
        console.error("Failed to poll GCS progress:", e);
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
            fetchHistory();
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
            'completed': 'bg-emerald-100 text-emerald-700 border-emerald-250',
            'failed': 'bg-rose-100 text-rose-700 border-rose-200',
            'cancelled': 'bg-slate-100 text-slate-500 border-slate-200'
        };

        const tr = document.createElement('tr');
        tr.className = "hover:bg-slate-50 border-b border-slate-200";
        tr.innerHTML = `
            <td class="py-3 px-4 text-center"><input type="checkbox" name="compare-select" value="${run.benchmark_id}" class="compare-chk"></td>
            <td class="py-3 px-4 font-mono font-bold text-slate-800">${run.benchmark_id}</td>
            <td class="py-3 px-4 font-semibold text-slate-700">${run.description}</td>
            <td class="py-3 px-4 text-slate-600">${run.username}</td>
            <td class="py-3 px-4 font-mono text-slate-600 text-xs">${run.executor_vm}</td>
            <td class="py-3 px-4 text-slate-500 text-xs">${dateStr}</td>
            <td class="py-3 px-4"><span class="text-[10px] font-bold px-2 py-0.5 rounded border uppercase tracking-wider ${statusColors[run.status] || 'bg-slate-100'}">${run.status}</span></td>
            <td class="py-3 px-4 text-center space-x-3">
                <button onclick="cloneRun('${run.benchmark_id}')" class="text-blue-600 hover:text-blue-800 transition" title="Clone configurations"><i class="fa-solid fa-copy text-sm"></i></button>
                <button onclick="expandRunDetails(this, '${run.benchmark_id}')" class="text-slate-400 hover:text-slate-600 transition" title="View details"><i class="fa-solid fa-chevron-down text-sm"></i></button>
            </td>
        `;
        tbody.appendChild(tr);

        // Add hidden expandable row
        const detailsTr = document.createElement('tr');
        detailsTr.id = `details-${run.benchmark_id}`;
        detailsTr.className = "hidden bg-slate-50 border-b border-slate-200";
        detailsTr.innerHTML = `
            <td colspan="8" class="py-4 px-6 max-w-full overflow-hidden">
                <div class="flex flex-wrap gap-x-16 gap-y-6 text-xs text-slate-600 leading-loose justify-start">
                    <div>
                        <span class="font-bold text-slate-700 uppercase tracking-wider block mb-1">GCSFuse Configs</span>
                        <p>Commit: <span class="font-mono text-slate-850 font-bold">${run.commit_hash}</span></p>
                        <p>Mount Args: <span class="font-mono text-slate-850">${run.mount_args || 'Used mount configs CSV'}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-700 uppercase tracking-wider block mb-1">Files Run</span>
                        <p>CSV: <span class="font-mono text-slate-850 break-all">${run.test_csv_name}</span></p>
                        <p>Configs CSV: <span class="font-mono text-slate-850 break-all">${run.configs_csv_name || 'N/A'}</span></p>
                        <p>FIO Job: <span class="font-mono text-slate-855 break-all">${run.fio_job_name}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-700 uppercase tracking-wider block mb-1">Scope Info</span>
                        <p>Project: <span class="text-slate-850">${run.project}</span></p>
                        <p>Zone: <span class="text-slate-850">${run.zone}</span></p>
                        <p>Iterations: <span class="text-slate-855">${run.iterations}</span></p>
                    </div>
                    <div>
                        <span class="font-bold text-slate-700 uppercase tracking-wider block mb-1">Timestamps</span>
                        <p>Created: <span class="text-slate-850">${run.created_at}</span></p>
                        <p>Started: <span class="text-slate-850">${run.started_at || 'N/A'}</span></p>
                        <p>Finished: <span class="text-slate-850">${run.completed_at || 'N/A'}</span></p>
                        <p>Duration: <span class="font-bold text-slate-850">${calculateDuration(run.started_at, run.completed_at)}</span></p>
                    </div>
                </div>

                <!-- Analysis Actions -->
                <div class="mt-4 flex items-center space-x-3">
                    <button id="btn-plot-${run.benchmark_id}" onclick="plotOnDemand('${run.benchmark_id}')" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-lg text-xs shadow flex items-center transition-colors">
                        <i class="fa-solid fa-chart-line mr-1.5"></i> Analyse & Plot Graphs
                    </button>
                    <a href="/api/runs/${run.benchmark_id}/report-view" target="_blank" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-lg text-xs shadow flex items-center transition-colors">
                        <i class="fa-solid fa-file-pdf mr-1.5"></i> Export PDF Report
                    </a>
                </div>

                <!-- Run Level Charts Container -->
                <div class="mt-4 pt-4 border-t border-slate-200 grid grid-cols-1 md:grid-cols-2 gap-4 hidden" id="charts-container-${run.benchmark_id}">
                    <div class="h-64 bg-white p-4 border border-slate-250 rounded-lg shadow-sm">
                        <canvas id="throughput-chart-${run.benchmark_id}"></canvas>
                    </div>
                    <div class="h-64 bg-white p-4 border border-slate-250 rounded-lg shadow-sm">
                        <canvas id="latency-chart-${run.benchmark_id}"></canvas>
                    </div>
                    <div class="h-64 bg-white p-4 border border-slate-250 rounded-lg shadow-sm">
                        <canvas id="peak-bw-chart-${run.benchmark_id}"></canvas>
                    </div>
                    <div class="h-64 bg-white p-4 border border-slate-250 rounded-lg shadow-sm">
                        <canvas id="cpu-chart-${run.benchmark_id}"></canvas>
                    </div>
                    <div class="h-64 bg-white p-4 border border-slate-250 rounded-lg shadow-sm">
                        <canvas id="mem-chart-${run.benchmark_id}"></canvas>
                    </div>
                </div>

                <!-- Completed Outputs -->
                <div class="mt-4 pt-4 border-t border-slate-200 max-w-full overflow-hidden">
                    <span class="font-bold text-slate-700 uppercase tracking-wider block mb-2"><i class="fa-solid fa-folder-open mr-1.5 text-blue-600"></i>Test Case Outputs</span>
                    <div id="test-outputs-${run.benchmark_id}" class="text-xs text-slate-600 max-h-48 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-2 mt-1">
                        Loading completed test cases...
                    </div>
                </div>

                <!-- Live/Orchestrator logs -->
                <div class="mt-4 pt-4 border-t border-slate-200 max-w-full overflow-hidden">
                    <span class="font-bold text-slate-700 uppercase tracking-wider block mb-2"><i class="fa-solid fa-terminal mr-1.5 text-blue-600"></i>Orchestrator Logs</span>
                    <pre class="w-full bg-slate-900 text-emerald-400 p-4 rounded-lg font-mono text-[11px] overflow-x-auto max-h-[600px] whitespace-pre" id="logs-history-${run.benchmark_id}">Loading logs...</pre>
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
        icon.className = "fa-solid fa-chevron-up text-sm";
        fetchHistoryLogs(id);
        fetchHistoryTestOutputs(id);
    } else {
        row.classList.add('hidden');
        icon.className = "fa-solid fa-chevron-down text-sm";
    }
}

async function fetchHistoryLogs(id) {
    const logEl = document.getElementById(`logs-history-${id}`);
    if (!logEl) return;
    try {
        const res = await fetch(`/api/runs/${id}/logs`);
        const data = await res.json();
        logEl.textContent = data.logs || "No logs returned.";
    } catch (e) {
        logEl.textContent = `Failed to fetch logs: ${e}`;
    }
}

async function fetchHistoryTestOutputs(id) {
    const listEl = document.getElementById(`test-outputs-${id}`);
    if (!listEl) return;
    try {
        const res = await fetch(`/api/runs/${id}/progress`);
        const data = await res.json();
        
        let html = '';
        let hasCompleted = false;
        
        for (const [vmFull, vmData] of Object.entries(data.vms || {})) {
            const jobs = vmData.jobs || [];
            for (const job of jobs) {
                if (job.status === 'SUCCESS' || job.status === 'FAILED/TIMEOUT') {
                    hasCompleted = true;
                    const statusColor = job.status === 'SUCCESS' ? 'text-emerald-600 bg-emerald-50 border-emerald-250' : 'text-rose-605 bg-rose-50 border-rose-250';
                    const paramStr = job.signature.join(', ');
                    html += `
                        <div class="flex items-center justify-between p-2 bg-white border border-slate-200 rounded-lg shadow-sm">
                            <div class="flex items-center space-x-2 truncate pr-2">
                                <span class="px-1.5 py-0.5 text-[9px] font-bold border rounded uppercase ${statusColor}">${job.status === 'SUCCESS' ? 'Success' : 'Fail'}</span>
                                <span class="font-bold text-slate-700">Test #${job.id}:</span>
                                <span class="text-slate-500 font-mono truncate" title="${paramStr}">${paramStr}</span>
                            </div>
                            <div class="flex items-center space-x-1.5 shrink-0">
                                <button onclick="viewGcsFile('${id}', '${vmFull}', 'test-${job.id}', 'fio_output_1.json')" class="px-2 py-0.5 bg-slate-50 hover:bg-slate-100 text-slate-700 font-bold rounded text-[10px] border border-slate-300 transition-colors">FIO Json</button>
                                <button onclick="viewGcsFile('${id}', '${vmFull}', 'test-${job.id}', 'gcsfuse_mount_1.log')" class="px-2 py-0.5 bg-slate-50 hover:bg-slate-100 text-slate-700 font-bold rounded text-[10px] border border-slate-300 transition-colors">Mount Log</button>
                            </div>
                        </div>
                    `;
                }
            }
        }
        
        if (!hasCompleted) {
            listEl.innerHTML = `<p class="text-slate-400 italic">No completed test case outputs available yet.</p>`;
        } else {
            listEl.innerHTML = html;
        }
    } catch (e) {
        listEl.innerHTML = `<p class="text-rose-500">Failed to load test outputs: ${e}</p>`;
    }
}

function viewGcsFile(runId, vm, testDir, filename) {
    const overlay = document.createElement('div');
    overlay.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-6";
    overlay.id = "gcs-file-modal";
    
    overlay.innerHTML = `
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl h-[80vh] flex flex-col overflow-hidden border border-slate-200">
            <div class="px-6 py-4 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
                <div>
                    <h3 class="font-bold text-slate-800 text-sm flex items-center"><i class="fa-solid fa-file-lines text-blue-600 mr-2"></i> ${filename}</h3>
                    <p class="text-[10px] text-slate-500 font-mono mt-0.5">${runId} / ${vm} / ${testDir}</p>
                </div>
                <button onclick="document.getElementById('gcs-file-modal').remove()" class="text-slate-450 hover:text-slate-700 transition-colors">
                    <i class="fa-solid fa-xmark text-lg"></i>
                </button>
            </div>
            <div class="p-6 overflow-auto flex-1 bg-slate-950 font-mono text-[11px] leading-relaxed text-slate-300 select-text" id="gcs-file-content">
                Loading file content from GCS...
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    
    fetch(`/api/runs/${runId}/results/${vm}/${testDir}/${filename}`)
        .then(res => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
        })
        .then(data => {
            const contentEl = document.getElementById('gcs-file-content');
            if (data.content) {
                contentEl.textContent = data.content;
            } else {
                contentEl.textContent = JSON.stringify(data, null, 2);
            }
        })
        .catch(e => {
            document.getElementById('gcs-file-content').innerHTML = `
                <div class="text-rose-400 p-4 font-sans text-xs">
                    <h4 class="font-bold mb-1">Failed to read file from GCS</h4>
                    <p class="mb-3">${e}</p>
                    <p class="text-slate-400 text-[10px] italic">Note: File might not be uploaded yet if the iteration did not complete or failed without log capture.</p>
                </div>
            `;
        });
}

// Clone configurations to launch panel
async function cloneRun(id) {
    try {
        const res = await fetch(`/api/runs/${id}/config`);
        const run = await res.json();

        // Load inputs
        document.getElementById('description').value = `Cloned: ${run.description}`;
        document.getElementById('executor_vm').value = run.executor_vm;
        document.getElementById('zone').value = run.zone;
        document.getElementById('project').value = run.project;
        document.getElementById('iterations').value = run.iterations;

        document.getElementById('artifacts_bucket').value = run.artifacts_bucket;
        document.getElementById('test_data_bucket').value = run.test_data_bucket;

        if (run.single_thread_vm_type || run.multi_thread_vm_type) {
            document.getElementById('enable_mig_templates').checked = true;
            toggleMigTemplates();
            document.getElementById('single_thread_vm_type').value = run.single_thread_vm_type || '';
            document.getElementById('multi_thread_vm_type').value = run.multi_thread_vm_type || '';
        } else {
            document.getElementById('enable_mig_templates').checked = false;
            toggleMigTemplates();
            document.getElementById('single_thread_vm_type').value = '';
            document.getElementById('multi_thread_vm_type').value = '';
        }

        // Force dropdown options selection
        document.getElementById('test_csv').value = run.test_csv_name;
        previewConfigFile(run.test_csv_name, 'test-csv-preview');
        document.getElementById('fio_job').value = run.fio_job_name;
        previewConfigFile(run.fio_job_name, 'fio-job-preview');

        if (run.configs_csv_name) {
            document.querySelector('input[name="config_mode"][value="multi"]').checked = true;
            toggleConfigMode();
            document.getElementById('configs_csv').value = run.configs_csv_name;
            previewConfigFile(run.configs_csv_name, 'configs-csv-preview');
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

// Plotting Engine using Chart.js (Styled for Light Theme)
function replotCharts() {
    if (!comparedData) return;

    const xAxisMode = document.getElementById('chart-x-axis').value;
    const runIds = Object.keys(comparedData);
    
    const allParams = new Set();
    runIds.forEach(id => {
        comparedData[id].forEach(row => allParams.add(row.param_str));
    });
    const sortedParams = Array.from(allParams).sort();

    const allConfigs = new Set();
    runIds.forEach(id => {
        comparedData[id].forEach(row => allConfigs.add(row.config));
    });
    const sortedConfigs = Array.from(allConfigs).sort();

    let labels = [];
    let datasetsBw = [];
    let datasetsLat = [];
    let datasetsPeakBw = [];
    let datasetsCpu = [];
    let datasetsMem = [];

    const colors = [
        '#1a73e8', '#1e8e3e', '#d93025', '#f97316', '#8b5cf6', '#ec4899', '#f59e0b', '#06b6d4'
    ];

    if (xAxisMode === 'test-cases') {
        labels = sortedParams;
        let seriesIdx = 0;
        runIds.forEach(runId => {
            sortedConfigs.forEach(conf => {
                const bwData = [];
                const latData = [];
                const peakBwData = [];
                const cpuData = [];
                const memData = [];
                let hasData = false;

                sortedParams.forEach(param => {
                    const match = comparedData[runId].find(r => r.param_str === param && r.config === conf);
                    if (match) {
                        hasData = true;
                        bwData.push(match.read_bw || match.write_bw || 0);
                        latData.push(match.read_lat || match.write_lat || 0);
                        peakBwData.push(match.peak_bw || match.read_bw || match.write_bw || 0);
                        cpuData.push(match.cpu || 0);
                        memData.push(match.mem || 0);
                    } else {
                        bwData.push(0);
                        latData.push(0);
                        peakBwData.push(0);
                        cpuData.push(0);
                        memData.push(0);
                    }
                });

                if (hasData) {
                    const labelName = runIds.length === 1 ? conf : `${runId} (${conf})`;
                    const color = colors[seriesIdx % colors.length];

                    datasetsBw.push({
                        label: labelName,
                        data: bwData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    datasetsLat.push({
                        label: labelName,
                        data: latData,
                        fill: false,
                        borderColor: color,
                        tension: 0.15,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    datasetsPeakBw.push({
                        label: labelName,
                        data: peakBwData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    datasetsCpu.push({
                        label: labelName,
                        data: cpuData,
                        fill: false,
                        borderColor: color,
                        tension: 0.15,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    datasetsMem.push({
                        label: labelName,
                        data: memData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    seriesIdx++;
                }
            });
        });
    } else {
        labels = sortedConfigs;
        let seriesIdx = 0;
        runIds.forEach(runId => {
            sortedParams.forEach(param => {
                const bwData = [];
                const latData = [];
                const peakBwData = [];
                const cpuData = [];
                const memData = [];
                let hasData = false;

                sortedConfigs.forEach(conf => {
                    const match = comparedData[runId].find(r => r.param_str === param && r.config === conf);
                    if (match) {
                        hasData = true;
                        bwData.push(match.read_bw || match.write_bw || 0);
                        latData.push(match.read_lat || match.write_lat || 0);
                        peakBwData.push(match.peak_bw || match.read_bw || match.write_bw || 0);
                        cpuData.push(match.cpu || 0);
                        memData.push(match.mem || 0);
                    } else {
                        bwData.push(0);
                        latData.push(0);
                        peakBwData.push(0);
                        cpuData.push(0);
                        memData.push(0);
                    }
                });

                if (hasData) {
                    const labelName = runIds.length === 1 ? param : `${runId} [${param.split('|').slice(0,4).join('|')}]`;
                    const color = colors[seriesIdx % colors.length];

                    datasetsBw.push({
                        label: labelName,
                        data: bwData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    datasetsLat.push({
                        label: labelName,
                        data: latData,
                        fill: false,
                        borderColor: color,
                        tension: 0.15,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    datasetsPeakBw.push({
                        label: labelName,
                        data: peakBwData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    datasetsCpu.push({
                        label: labelName,
                        data: cpuData,
                        fill: false,
                        borderColor: color,
                        tension: 0.15,
                        pointRadius: 4,
                        borderWidth: 2
                    });

                    datasetsMem.push({
                        label: labelName,
                        data: memData,
                        backgroundColor: color + 'bf',
                        borderColor: color,
                        borderWidth: 1
                    });

                    seriesIdx++;
                }
            });
        });
    }

    renderChart('throughput-chart', 'bar', labels, datasetsBw, 'Throughput (MB/s)');
    renderChart('latency-chart', 'line', labels, datasetsLat, 'Latency (ms)');
    renderChart('peak-bw-chart', 'bar', labels, datasetsPeakBw, 'Peak Throughput (MB/s)');
    renderChart('cpu-chart', 'line', labels, datasetsCpu, 'CPU Usage (%)');
    renderChart('mem-chart', 'bar', labels, datasetsMem, 'RSS Memory (MB)');
}

function renderChart(canvasId, type, labels, datasets, yLabel) {
    if (charts[canvasId]) {
        charts[canvasId].destroy();
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
                    grid: { color: '#e2e8f0' },
                    ticks: {
                        color: '#475569',
                        font: { size: 9 },
                        maxRotation: 45,
                        minRotation: 20
                    }
                },
                y: {
                    grid: { color: '#e2e8f0' },
                    ticks: { color: '#475569' },
                    title: {
                        display: true,
                        text: yLabel,
                        color: '#475569',
                        font: { weight: 'bold' }
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#334155',
                        font: { size: 9 },
                        boxWidth: 12
                    }
                },
                tooltip: {
                    backgroundColor: '#ffffff',
                    titleColor: '#0f172a',
                    bodyColor: '#334155',
                    borderColor: '#cbd5e1',
                    borderWidth: 1
                }
            }
        }
    });
}

// Toggle MIG VM templates container
function toggleMigTemplates() {
    const checked = document.getElementById('enable_mig_templates').checked;
    const container = document.getElementById('mig-template-container');
    if (checked) {
        container.classList.remove('hidden');
    } else {
        container.classList.add('hidden');
    }
}

// Preview file content dynamically (No accordions required, directly loads on grid block)
async function previewConfigFile(path, elementId) {
    const el = document.getElementById(elementId);
    if (!path) {
        el.innerText = "No file selected.";
        return;
    }

    try {
        el.innerText = "Loading preview...";
        const res = await fetch(`/api/configs/preview?path=${encodeURIComponent(path)}`);
        if (res.ok) {
            const data = await res.json();
            el.innerText = data.content;
        } else {
            el.innerText = "Error loading file preview.";
        }
    } catch (e) {
        el.innerText = `Failed to fetch file content: ${e}`;
    }
}

// Custom FIO Config Modal Controls
function openFioModal() {
    document.getElementById('fio-modal').classList.remove('hidden');
    // Pre-populate template to guide user
    if (!document.getElementById('fio_content').value) {
        document.getElementById('fio_content').value = `[global]
ioengine=libaio
direct=1
fdatasync=1
invalidate=1
rw=read
bs=1m
size=1g
numjobs=1
time_based=0

[job1]
filename=test_file_0
`;
    }
}

function closeFioModal() {
    document.getElementById('fio-modal').classList.add('hidden');
    document.getElementById('fio_filename').value = '';
}

async function submitFioConfig(event) {
    event.preventDefault();
    const filename = document.getElementById('fio_filename').value.trim();
    const content = document.getElementById('fio_content').value;

    try {
        const res = await fetch('/api/configs/fio-jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, content })
        });

        if (res.ok) {
            const data = await res.json();
            alert("Custom FIO template saved successfully!");
            closeFioModal();
            // Reload select options to show new FIO file
            await fetchConfigFiles();
            // Select the newly added FIO file
            document.getElementById('fio_job').value = data.path;
            previewConfigFile(data.path, 'fio-job-preview');
        } else {
            const err = await res.json();
            alert(`Error saving FIO config: ${err.detail}`);
        }
    } catch (e) {
        alert(`Failed to save: ${e}`);
    }
}

// Custom CSV Modals
function openCsvModal(type) {
    document.getElementById('csv-modal').classList.remove('hidden');
    document.getElementById('csv_target_type').value = type;
    
    const title = document.getElementById('csv-modal-title');
    const filenameInput = document.getElementById('csv_filename');
    const contentText = document.getElementById('csv_content');
    
    filenameInput.value = '';
    
    if (type === 'test_cases') {
        title.innerHTML = '<i class="fa-solid fa-file-csv mr-2.5 text-emerald-600"></i>Create Custom Test Cases CSV';
        filenameInput.placeholder = 'e.g. read_custom_tests.csv';
        contentText.placeholder = 'IOType,Jobs,FSize,BS,IOD,NrFiles,Direct\nread,1,1g,1m,1,1,0\nread,4,1g,256k,8,4,1';
        contentText.value = 'IOType,Jobs,FSize,BS,IOD,NrFiles,Direct\nread,1,1g,1m,1,1,0\n';
    } else {
        title.innerHTML = '<i class="fa-solid fa-file-shield mr-2.5 text-indigo-650"></i>Create Custom GCSFuse Mount Configs CSV';
        filenameInput.placeholder = 'e.g. grpc_and_kernel_mounts.csv';
        contentText.placeholder = 'config,gcsfuse-commit,gcsfuse-mount-args\ngrpc,master,--client-protocol=grpc --implicit-dirs\nkernel,master,--implicit-dirs';
        contentText.value = 'config,gcsfuse-commit,gcsfuse-mount-args\ngrpc,master,--client-protocol=grpc --implicit-dirs\n';
    }
}

function closeCsvModal() {
    document.getElementById('csv-modal').classList.add('hidden');
    document.getElementById('csv_filename').value = '';
    document.getElementById('csv_content').value = '';
}

async function submitCsvConfig(event) {
    event.preventDefault();
    const type = document.getElementById('csv_target_type').value;
    const filename = document.getElementById('csv_filename').value.trim();
    const content = document.getElementById('csv_content').value;
    
    const apiEndpoint = type === 'test_cases' ? '/api/configs/test-cases' : '/api/configs/mount-configs';
    
    try {
        const res = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, content })
        });
        
        if (res.ok) {
            const data = await res.json();
            alert("Custom CSV file saved successfully!");
            closeCsvModal();
            
            // Reload selects
            await fetchConfigFiles();
            
            // Auto select new file & trigger preview
            if (type === 'test_cases') {
                document.getElementById('test_csv').value = data.path;
                previewConfigFile(data.path, 'test-csv-preview');
            } else {
                document.getElementById('configs_csv').value = data.path;
                previewConfigFile(data.path, 'configs-csv-preview');
            }
        } else {
            const err = await res.json();
            alert(`Error saving CSV file: ${err.detail}`);
        }
    } catch (e) {
        alert(`Failed to save CSV: ${e}`);
    }
}

function calculateDuration(start, end) {
    if (!start || !end || start === 'N/A' || end === 'N/A') return 'N/A';
    try {
        const sDate = new Date(start);
        const eDate = new Date(end);
        const diffMs = eDate - sDate;
        if (diffMs < 0 || isNaN(diffMs)) return 'N/A';
        
        const diffSec = Math.floor(diffMs / 1000);
        const hrs = Math.floor(diffSec / 3600);
        const mins = Math.floor((diffSec % 3600) / 60);
        const secs = diffSec % 60;
        
        let str = '';
        if (hrs > 0) str += `${hrs}h `;
        if (mins > 0 || hrs > 0) str += `${mins}m `;
        str += `${secs}s`;
        return str.trim();
    } catch {
        return 'N/A';
    }
}

async function plotOnDemand(runId) {
    const btn = document.getElementById(`btn-plot-${runId}`);
    const chartContainer = document.getElementById(`charts-container-${runId}`);
    if (!btn || !chartContainer) return;
    
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-1.5"></i> Plotting...`;
    btn.disabled = true;
    
    try {
        const res = await fetch(`/api/runs/compare?ids=${runId}`);
        const data = await res.json();
        
        if (!data[runId] || data[runId].length === 0) {
            alert("No FIO performance data found for this run yet. Ensure at least one test case successfully finished.");
            btn.innerHTML = `<i class="fa-solid fa-chart-line mr-1.5"></i> Analyse & Plot Graphs`;
            btn.disabled = false;
            return;
        }
        
        chartContainer.classList.remove('hidden');
        renderRowCharts(runId, data);
        btn.innerHTML = `<i class="fa-solid fa-check mr-1.5 text-emerald-500"></i> Plotted Successfully`;
    } catch (e) {
        alert(`Failed to plot graphs: ${e}`);
        btn.innerHTML = `<i class="fa-solid fa-chart-line mr-1.5"></i> Analyse & Plot Graphs`;
        btn.disabled = false;
    }
}

function renderRowCharts(runId, data) {
    const runData = data[runId] || [];
    
    const allParams = new Set();
    const allConfigs = new Set();
    runData.forEach(row => {
        allParams.add(row.param_str);
        allConfigs.add(row.config);
    });
    
    const sortedParams = Array.from(allParams).sort();
    const sortedConfigs = Array.from(allConfigs).sort();
    
    const labels = sortedParams.map(p => {
        const parts = p.split('|');
        return `${parts[0]} (${parts[3]}) - depth ${parts[4]} (${parts[1]} jobs)`;
    });
    
    const datasetsBw = [];
    const datasetsLat = [];
    const datasetsPeakBw = [];
    const datasetsCpu = [];
    const datasetsMem = [];
    const colors = [
        '#1a73e8', '#1e8e3e', '#d93025', '#f97316', '#8b5cf6', '#ec4899', '#f59e0b', '#06b6d4'
    ];
    
    let seriesIdx = 0;
    sortedConfigs.forEach(conf => {
        const bwData = [];
        const latData = [];
        const peakBwData = [];
        const cpuData = [];
        const memData = [];
        let hasData = false;
        
        sortedParams.forEach(param => {
            const match = runData.find(r => r.param_str === param && r.config === conf);
            if (match) {
                hasData = true;
                bwData.push(match.read_bw || match.write_bw || 0);
                latData.push(match.read_lat || match.write_lat || 0);
                peakBwData.push(match.peak_bw || match.read_bw || match.write_bw || 0);
                cpuData.push(match.cpu || 0);
                memData.push(match.mem || 0);
            } else {
                bwData.push(0);
                latData.push(0);
                peakBwData.push(0);
                cpuData.push(0);
                memData.push(0);
            }
        });
        
        if (hasData) {
            const color = colors[seriesIdx % colors.length];
            datasetsBw.push({
                label: conf,
                data: bwData,
                backgroundColor: color + 'bf',
                borderColor: color,
                borderWidth: 1
            });
            
            datasetsLat.push({
                label: conf,
                data: latData,
                fill: false,
                borderColor: color,
                tension: 0.15,
                pointRadius: 4,
                borderWidth: 2
            });

            datasetsPeakBw.push({
                label: conf,
                data: peakBwData,
                backgroundColor: color + 'bf',
                borderColor: color,
                borderWidth: 1
            });

            datasetsCpu.push({
                label: conf,
                data: cpuData,
                fill: false,
                borderColor: color,
                tension: 0.15,
                pointRadius: 4,
                borderWidth: 2
            });

            datasetsMem.push({
                label: conf,
                data: memData,
                backgroundColor: color + 'bf',
                borderColor: color,
                borderWidth: 1
            });

            seriesIdx++;
        }
    });
    
    renderChart(`throughput-chart-${runId}`, 'bar', labels, datasetsBw, 'Throughput (MB/s)');
    renderChart(`latency-chart-${runId}`, 'line', labels, datasetsLat, 'Latency (ms)');
    renderChart(`peak-bw-chart-${runId}`, 'bar', labels, datasetsPeakBw, 'Peak Throughput (MB/s)');
    renderChart(`cpu-chart-${runId}`, 'line', labels, datasetsCpu, 'CPU Usage (%)');
    renderChart(`mem-chart-${runId}`, 'bar', labels, datasetsMem, 'RSS Memory (MB)');
}
