        // ==================== GLOBALS ====================
        // Chart instances — declared FIRST so theme init can reference them
        var statusChart = null, hourlyChart = null, weeklyChart = null, activityChart = null;
        var ws = null, reconnectAttempts = 0;
        var blurInterval = null;

        // ─── THEME ───
        function getStoredTheme() { try { return localStorage.getItem('aos-theme'); } catch(e) { return null; } }
        function applyTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            try { localStorage.setItem('aos-theme', theme); } catch(e) {}
            updateChartColors(theme);
            // 同步到后端，让悬浮窗也跟随切换
            try { fetch('/api/settings/theme?theme=' + theme, {method: 'POST'}); } catch(e) {}
        }
        function toggleTheme() {
            var current = document.documentElement.getAttribute('data-theme') || 'dark';
            applyTheme(current === 'dark' ? 'light' : 'dark');
        }
        function updateChartColors(theme) {
            var isDark = theme !== 'light';
            Chart.defaults.color = isDark ? '#5a5e6a' : '#7a7e8a';
            Chart.defaults.borderColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)';
            // Update existing chart instances (skip if not yet created)
            var charts = [statusChart, hourlyChart, weeklyChart, activityChart];
            for (var i = 0; i < charts.length; i++) {
                var c = charts[i];
                if (!c) continue;
                try {
                    if (c.options && c.options.scales) {
                        var scaleKeys = Object.keys(c.options.scales);
                        for (var j = 0; j < scaleKeys.length; j++) {
                            var s = c.options.scales[scaleKeys[j]];
                            if (s.grid) s.grid.color = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)';
                            if (s.ticks) s.ticks.color = isDark ? '#5a5e6a' : '#7a7e8a';
                        }
                    }
                    if (c.options && c.options.plugins && c.options.plugins.legend && c.options.plugins.legend.labels) {
                        c.options.plugins.legend.labels.color = isDark ? '#5a5e6a' : '#7a7e8a';
                    }
                    c.update('none');
                } catch(e) { /* skip */ }
            }
        }
        // Apply saved theme immediately (before DOMContentLoaded)
        (function() {
            var t = getStoredTheme();
            if (t) {
                applyTheme(t);
            } else {
                // 本地无偏好时，从后端读取（与悬浮窗保持一致）
                fetch('/api/settings/theme').then(function(r) { return r.json(); })
                    .then(function(d) { if (d && d.theme) applyTheme(d.theme); })
                    .catch(function() {});
            }
            // Sync settings toggle after DOM loads
            document.addEventListener('DOMContentLoaded', function() {
                syncThemeSettingsToggle();
            });
        })();
        function syncThemeSettingsToggle() {
            var toggle = document.getElementById('darkModeToggle');
            if (toggle) toggle.checked = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';
        }
        function toggleThemeFromSetting() {
            var isDark = document.getElementById('darkModeToggle').checked;
            applyTheme(isDark ? 'dark' : 'light');
        }
        // Override applyTheme to also sync the settings toggle
        var _origApplyTheme = applyTheme;
        applyTheme = function(theme) {
            _origApplyTheme(theme);
            syncThemeSettingsToggle();
        };

        Chart.defaults.color = '#5a5e6a';
        Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';

        // ==================== TAB SWITCHING ====================
        var _currentTab = 'todo';
        function switchTab(name, el) {
            _currentTab = name;
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + name).classList.add('active');

            // 面包屑导航：非任务页显示返回按钮
            const breadcrumb = document.getElementById('viewBreadcrumb');
            const breadcrumbTitle = document.getElementById('breadcrumbTitle');
            const tabTitles = {
                dashboard: '📊 仪表盘', chatlog: '💬 对话记录',
                plugins: '🧩 插件', settings: '⚙️ 设置'
            };
            if (name === 'todo') {
                breadcrumb.style.display = 'none';
            } else {
                breadcrumb.style.display = 'flex';
                breadcrumbTitle.textContent = tabTitles[name] || name;
            }

            if (name === 'dashboard') { ensureChartsInit(); loadData(); loadWorkStartData(); loadAppsData(); }
            if (name === 'todo') loadTodos();
            if (name === 'chatlog') { loadChatLogDates(); }
            if (name === 'plugins') { loadPlugins(); }
            if (name === 'settings') { loadAPIProviders(); loadAutoStartStatus(); loadScreenshotAnalysisSetting(); }

            window.scrollTo({top: 0, behavior: 'smooth'});
        }

        function backToTasks() {
            switchTab('todo', null);
        }

        function jumpToTab(name) {
            switchTab(name, null);
            closeOnboarding();
        }

        // 更多菜单
        function toggleMoreMenu() {
            const dd = document.getElementById('moreMenuDropdown');
            dd.classList.toggle('show');
        }
        function closeMoreMenu() {
            const dd = document.getElementById('moreMenuDropdown');
            dd.classList.remove('show');
        }
        // 点击外部关闭下拉
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.more-menu-wrap')) closeMoreMenu();
        });


        // ==================== FIRST-USE ONBOARDING ====================
        function shouldShowOnboarding() {
            try {
                if (localStorage.getItem('aos-onboarding-disabled') === '1') return false;
                return localStorage.getItem('aos-onboarding-complete') !== '1';
            } catch(e) { return true; }
        }
        function openOnboarding() {
            const modal = document.getElementById('onboardingModal');
            if (modal) modal.classList.add('show');
        }
        function closeOnboarding() {
            const modal = document.getElementById('onboardingModal');
            if (modal) modal.classList.remove('show');
        }
        function finishOnboarding() {
            try {
                localStorage.setItem('aos-onboarding-complete', '1');
                const neverShow = document.getElementById('onboardingDontShow');
                if (neverShow && neverShow.checked) {
                    localStorage.setItem('aos-onboarding-disabled', '1');
                }
            } catch(e) {}
            closeOnboarding();
        }

        // ==================== INIT ====================
        var chartsInitialized = false;
        function ensureChartsInit() {
            if (!chartsInitialized) {
                initCharts();
                const theme = document.documentElement.getAttribute('data-theme') || 'dark';
                updateChartColors(theme);
                chartsInitialized = true;
            }
        }
        document.addEventListener('DOMContentLoaded', () => {
            // Check daily briefing first
            checkBriefing();
            // Load todos first since it's the default tab
            loadTodos();
            loadCheckinSettings();
            loadBreakSettings();
            connectWebSocket();
            if (shouldShowOnboarding()) {
                setTimeout(openOnboarding, 700);
            }
            setInterval(() => { if (chartsInitialized) loadData(); }, 30000);
        });

        // ==================== CHARTS INIT ====================
        function initCharts() {
            statusChart = new Chart(document.getElementById('statusChart'), {
                type:'doughnut', data:{labels:['专注','投入','游离','分心','离开'],
                datasets:[{data:[0,0,0,0,0],backgroundColor:['#34d399','#60a5fa','#fbbf24','#f87171','#3f3f46'],borderWidth:0}]},
                options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{padding:12,usePointStyle:true,font:{size:11}}}},cutout:'65%'}
            });
            hourlyChart = new Chart(document.getElementById('hourlyChart'), {
                type:'bar', data:{labels:Array.from({length:24},(_,i)=>`${i}:00`),
                datasets:[{label:'生产率',data:Array(24).fill(0),backgroundColor:'rgba(52,211,153,0.6)',borderRadius:3},
                {label:'分心率',data:Array(24).fill(0),backgroundColor:'rgba(248,113,113,0.6)',borderRadius:3}]},
                options:{responsive:true,maintainAspectRatio:false,scales:{x:{grid:{display:false},ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:12,font:{size:10}}},
                y:{beginAtZero:true,max:1,ticks:{callback:v=>Math.round(v*100)+'%',font:{size:10}}}},plugins:{legend:{position:'bottom',labels:{usePointStyle:true,font:{size:11}}}}}
            });
            weeklyChart = new Chart(document.getElementById('weeklyChart'), {
                type:'line', data:{labels:[],datasets:[{label:'生产率',data:[],borderColor:'#34d399',backgroundColor:'rgba(52,211,153,0.08)',fill:true,tension:.4},
                {label:'分心率',data:[],borderColor:'#f87171',backgroundColor:'rgba(248,113,113,0.08)',fill:true,tension:.4}]},
                options:{responsive:true,maintainAspectRatio:false,scales:{y:{beginAtZero:true,max:1,ticks:{callback:v=>Math.round(v*100)+'%',font:{size:10}}}},
                plugins:{legend:{position:'bottom',labels:{usePointStyle:true,font:{size:11}}}}}
            });
            activityChart = new Chart(document.getElementById('activityChart'), {
                type:'line', data:{labels:[],datasets:[{label:'活动率 (平滑)',data:[],borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,0.08)',fill:true,tension:.4,pointRadius:0}]},
                options:{responsive:true,maintainAspectRatio:false,scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10,font:{size:10}}},
                y:{beginAtZero:true,max:1,ticks:{callback:v=>Math.round(v*100)+'%',font:{size:10}}}},plugins:{legend:{display:false}}}
            });
        }

        // ==================== WORK START TIME ====================
        async function loadWorkStartData() {
            try {
                const [todayRes, histRes] = await Promise.all([
                    fetch('/api/work-start/today'),
                    fetch('/api/work-start/history')
                ]);
                const today = await todayRes.json();
                const hist = await histRes.json();
                renderWorkStartToday(today);
                renderWorkStartHistory(hist.history || {});
            } catch(e) { console.error('Load work start failed:', e); }
        }

        function renderWorkStartToday(data) {
            const el = document.getElementById('workStartTime');
            const cmp = document.getElementById('workStartCompare');
            if (data.recorded && data.start_time) {
                el.textContent = data.start_time.substring(0, 5);
                cmp.textContent = data.is_workday ? '工作日' : '休息日';
            } else {
                el.textContent = '未记录';
                cmp.textContent = '今日尚未开工';
            }
        }

        function renderWorkStartHistory(hist) {
            const box = document.getElementById('workStartHistory');
            const avgWd = document.getElementById('avgWorkday');
            const avgWe = document.getElementById('avgWeekend');

            avgWd.textContent = hist.avg_workday || '--:--';
            avgWe.textContent = hist.avg_weekend || '--:--';

            const days = hist.days || [];
            if (!days.length) { box.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">暂无开工记录</span>'; return; }

            // Render bars: height = time mapped to a visual range (6:00=full, 12:00=0)
            // Earlier = taller bar (good), later = shorter bar
            const MIN_HOUR = 6, MAX_HOUR = 12;
            const reversed = [...days].reverse(); // oldest first (left to right)
            box.innerHTML = reversed.map(d => {
                const isWd = d.is_workday;
                const color = isWd ? 'var(--blue)' : 'var(--purple)';
                if (!d.start_time) {
                    return `<div title="${d.date} (${d.weekday})\n无记录" style="width:14px;height:6px;border-radius:2px;background:rgba(128,128,128,0.2);cursor:pointer;flex-shrink:0;"></div>`;
                }
                const parts = d.start_time.split(':');
                const hour = parseInt(parts[0]) + parseInt(parts[1]) / 60;
                const pct = Math.max(5, Math.min(100, ((MAX_HOUR - hour) / (MAX_HOUR - MIN_HOUR)) * 100));
                const weekday_cn = ['周一','周二','周三','周四','周五','周六','周日'][new Date(d.date).getDay() === 0 ? 6 : new Date(d.date).getDay() - 1];
                return `<div title="${d.date} ${weekday_cn}\n开工: ${d.start_time.substring(0,5)}" style="width:14px;height:${pct * 0.7}px;min-height:6px;border-radius:2px 2px 0 0;background:${color};cursor:pointer;flex-shrink:0;opacity:${d.date===new Date().toISOString().substring(0,10)?'1':'0.7'};"></div>`;
            }).join('');
        }

        // ==================== DATA LOADING ====================
        async function loadData() {
            try {
                var results = await Promise.allSettled([
                    fetch('/api/today'), fetch('/api/hourly'), fetch('/api/weekly'), fetch('/api/status')
                ]);
                if (results[0].status==='fulfilled' && results[0].value.ok) updateTodayData(await results[0].value.json());
                if (results[1].status==='fulfilled' && results[1].value.ok) updateHourlyChart((await results[1].value.json()).hourly_pattern);
                if (results[2].status==='fulfilled' && results[2].value.ok) updateWeeklyChart((await results[2].value.json()).weekly_trend);
                if (results[3].status==='fulfilled' && results[3].value.ok) updateCurrentStatus(await results[3].value.json());
            } catch(e) { console.error('Load failed:', e); }
        }

        function updateTodayData(data) {
            const s = data.statistics || {};
            document.getElementById('productiveRatio').textContent = Math.round((s.productive_ratio||0)*100)+'%';
            document.getElementById('distractedRatio').textContent = Math.round((s.distracted_ratio||0)*100)+'%';
            document.getElementById('totalRecords').textContent = (s.total_records||0)+' 条记录';
            // Timeline
            const c = document.getElementById('timeline'); c.innerHTML = '';
            (data.timeline||[]).forEach(item => {
                const b = document.createElement('div'); b.className = 'tl-block';
                b.classList.add(item.is_productive?'prod':item.is_distracted?'dist':item.activity_ratio<0.1?'idle':'neut');
                b.title = `${item.time}\n${item.engagement}\n${item.app||'?'}`;
                c.appendChild(b);
            });
            // Charts
            const ad = s.attention_distribution || {};
            statusChart.data.datasets[0].data = [ad['专注']||0,ad['投入']||0,ad['游离']||0,ad['分心']||0,ad['离开']||0];
            statusChart.update();
            updateAppList(data.app_usage || []);
            const tl = data.timeline || [];
            activityChart.data.labels = tl.map(t=>t.time.split(' ')[1].substring(0,5));
            // Apply 5-point moving average to smooth activity data
            const rawActivity = tl.map(t=>t.activity_ratio||0);
            const MA_WINDOW = 5;
            const smoothedActivity = rawActivity.map((val, i) => {
                const start = Math.max(0, i - Math.floor(MA_WINDOW / 2));
                const end = Math.min(rawActivity.length, i + Math.ceil(MA_WINDOW / 2));
                const window = rawActivity.slice(start, end);
                return window.reduce((a, b) => a + b, 0) / window.length;
            });
            activityChart.data.datasets[0].data = smoothedActivity;
            activityChart.update();
        }
        function updateHourlyChart(hp) {
            hourlyChart.data.datasets[0].data = hp.map(h=>h.productive_ratio);
            hourlyChart.data.datasets[1].data = hp.map(h=>h.distracted_ratio);
            hourlyChart.update();
        }
        function updateWeeklyChart(wt) {
            weeklyChart.data.labels = wt.map(d=>d.date);
            weeklyChart.data.datasets[0].data = wt.map(d=>d.productive_ratio);
            weeklyChart.data.datasets[1].data = wt.map(d=>d.distracted_ratio);
            weeklyChart.update();
        }
        function updateAppList(au) {
            const mx = Math.max(...au.map(a=>a.minutes),1);
            document.getElementById('appList').innerHTML = au.map(a=>`
                <div class="app-item"><div class="app-ico">${getAppEmoji(a.app)}</div>
                <div class="app-info"><div class="app-name">${a.app||'未知'}</div><div class="app-time">${a.minutes} 分钟</div></div>
                <div class="app-bar"><div class="app-bar-fill" style="width:${(a.minutes/mx)*100}%"></div></div></div>`).join('');
        }
        function updateCurrentStatus(data) {
            const latest = data.latest_record, fused = latest?.fused_state||{}, analysis = latest?.analysis||{};
            const dot = document.getElementById('statusDot'), stxt = document.getElementById('statusText');
            if(data.monitor_running){dot.classList.remove('off');stxt.textContent='监控中';}
            else{dot.classList.add('off');stxt.textContent='已停止';}
            document.getElementById('currentEngagement').textContent = fused.user_engagement||'--';
            document.getElementById('currentApp').textContent = (fused.active_window_app||'--') + ' · ' + (fused.attention_level||'');
            document.getElementById('workStatus').textContent = analysis.work_status||'--';
            document.getElementById('userEngagement').textContent = fused.user_engagement||'--';
            document.getElementById('focusWindow').textContent = (fused.active_window_title||'--').substring(0,30);
            document.getElementById('idleDuration').textContent = (data.idle_duration||0)+' 秒';
            // Recovery panel
            const rec = data.recovery;
            if(rec && rec.is_slacking && rec.slacking_duration_seconds > 60) {
                document.getElementById('recoveryCard').style.display = 'block';
                document.getElementById('ntBar').style.width = (rec.neurotransmitter_recovery*100)+'%';
                document.getElementById('ntPct').textContent = Math.round(rec.neurotransmitter_recovery*100)+'%';
                document.getElementById('arBar').style.width = (rec.attention_residue_cleared*100)+'%';
                document.getElementById('arPct').textContent = Math.round(rec.attention_residue_cleared*100)+'%';
                document.getElementById('ciBar').style.width = (rec.context_integrity*100)+'%';
                document.getElementById('ciPct').textContent = Math.round(rec.context_integrity*100)+'%';
                const msg = rec.recovery_message;
                const msgEl = document.getElementById('recoveryMsg');
                if(msg && msg.title) {
                    msgEl.style.display = 'block';
                    let cls = msg.phase==='optimal'?'':msg.phase==='context_fading'?'warning':'danger';
                    msgEl.className = 'recovery-msg ' + cls;
                    msgEl.innerHTML = `<div class="recovery-msg-title">${msg.title}</div><div>${msg.body||''}</div>` +
                        (msg.detail_lines?msg.detail_lines.map(l=>`<div style="font-size:12px;margin-top:4px;">${l}</div>`).join(''):'') +
                        (msg.suggestion?`<div style="margin-top:8px;font-weight:600;">${msg.suggestion}</div>`:'');
                } else { msgEl.style.display = 'none'; }
            } else {
                document.getElementById('recoveryCard').style.display = 'none';
            }
            // Pomodoro blur
            const pomo = data.pomodoro;
            if(pomo && pomo.should_blur) { showBlur(pomo.remaining_seconds, pomo.remaining_display); }
            else { hideBlur(); }
        }
        function getAppEmoji(n) {
            const l=(n||'').toLowerCase();
            if(l.includes('code')||l.includes('studio'))return'💻';if(l.includes('chrome')||l.includes('safari')||l.includes('firefox'))return'🌐';
            if(l.includes('terminal')||l.includes('iterm'))return'⌨️';if(l.includes('slack')||l.includes('teams'))return'💬';
            if(l.includes('微信'))return'💬';if(l.includes('music'))return'🎵';if(l.includes('notion'))return'📝';return'📱';
        }

        // ==================== APPS VISUALIZATION ====================
        const CAT_LABELS = {work:'工作', communication:'沟通', learning:'学习', entertainment:'娱乐', unknown:'其他'};
        const CAT_COLORS = {work:'green', communication:'blue', learning:'purple', entertainment:'red', unknown:''};

        async function loadAppsData() {
            const el = document.getElementById('appsVizList');
            if (!el) return;
            try {
                const r = await fetch('/api/apps');
                const data = await r.json();
                renderAppsViz(data.apps || []);
            } catch(e) {
                el.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">加载失败</div>';
            }
        }

        function renderAppsViz(apps) {
            const el = document.getElementById('appsVizList');
            if (!el) return;
            if (!apps.length) {
                el.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:12px 0;">暂无记录，使用一段时间后会在此显示</div>';
                return;
            }
            el.innerHTML = apps.map(a => {
                const mins = a.minutes || 0;
                const timeStr = mins >= 60 ? `${Math.floor(mins/60)}h ${mins%60}m` : `${mins}m`;
                const cat = a.category || 'unknown';
                const catColor = CAT_COLORS[cat] || '';
                const badgeClass = 'app-cat-select badge' + (catColor ? ' badge-'+catColor : '');
                const opts = Object.entries(CAT_LABELS).map(([v,l]) =>
                    `<option value="${v}"${v===cat?' selected':''}>${l}</option>`
                ).join('');
                const overrideMark = a.is_user_overridden ? '<span style="font-size:9px;color:var(--blue);margin-left:4px;vertical-align:middle;">●</span>' : '';
                return `<div class="app-viz-item">
                    <div class="app-ico">${getAppEmoji(a.app)}</div>
                    <div class="app-viz-body">
                        <div class="app-viz-name">${a.app||'未知'}${overrideMark}</div>
                        <div class="app-viz-time">${timeStr}</div>
                    </div>
                    <select class="${badgeClass}" data-app="${encodeURIComponent(a.app)}" onchange="updateAppCategory(this)" title="${a.is_user_overridden?'已自定义分类，点击修改':'自动分类，点击修改'}">${opts}</select>
                </div>`;
            }).join('');
        }

        async function updateAppCategory(selectEl) {
            const appName = decodeURIComponent(selectEl.dataset.app);
            const category = selectEl.value;
            const catColor = CAT_COLORS[category] || '';
            selectEl.className = 'app-cat-select badge' + (catColor ? ' badge-'+catColor : '');
            try {
                await fetch('/api/apps/category', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({app: appName, category})
                });
            } catch(e) {
                console.error('updateAppCategory failed:', e);
            }
        }

        // ==================== SCREENSHOT ANALYSIS SETTING ====================
        async function loadScreenshotAnalysisSetting() {
            try {
                const r = await fetch('/api/settings/screenshot-analysis');
                const data = await r.json();
                const toggle = document.getElementById('screenshotAnalysisToggle');
                if (toggle) toggle.checked = !!data.enabled;
            } catch(e) {
                console.error('loadScreenshotAnalysisSetting failed:', e);
            }
        }

        async function toggleScreenshotAnalysis(enabled) {
            try {
                await fetch('/api/settings/screenshot-analysis', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({enabled})
                });
            } catch(e) {
                console.error('toggleScreenshotAnalysis failed:', e);
            }
        }

        // ==================== WEBSOCKET ====================
        function connectWebSocket() {
            if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
            try {
                const p = location.protocol==='https:'?'wss:':'ws:';
                ws = new WebSocket(p + '//' + location.host + '/ws');
                ws.onopen = function(){reconnectAttempts=0;console.log('WS connected');};
                ws.onmessage = function(e){try{updateCurrentStatus(JSON.parse(e.data));}catch(err){console.error('WS parse error:',err);}};
                ws.onclose = function(){ws=null;if(reconnectAttempts<10){reconnectAttempts++;var delay=Math.min(1000*Math.pow(1.5,reconnectAttempts),15000);setTimeout(connectWebSocket,delay);}};
                ws.onerror = function(e){console.error('WS error:',e);};
            } catch(e) {
                console.error('WS create failed:', e);
                if(reconnectAttempts<10){reconnectAttempts++;setTimeout(connectWebSocket,5000);}
            }
        }

        // ==================== POMODORO ====================
        async function loadPomoFocusOptions() {
            // Populate the focus task dropdown from goals + todos
            const sel = document.getElementById('pomoFocusSelect');
            const currentVal = sel.value;
            let options = '<option value="">（无绑定，自由专注）</option>';
            try {
                // Goals
                const bRes = await fetch('/api/briefing');
                const bData = await bRes.json();
                const goals = (bData.goals || []).filter(g => !g.done);
                if (goals.length) {
                    options += '<optgroup label="🎯 今日目标">';
                    goals.forEach(g => { options += `<option value="goal:${g.text}">${g.text}</option>`; });
                    options += '</optgroup>';
                }
                // Todos due today or high priority
                const tRes = await fetch('/api/todos');
                const tData = await tRes.json();
                const urgent = (tData.todos || []).filter(t => !t.completed && (t.days_until_deadline === 0 || t.priority === 'urgent' || t.priority === 'high'));
                if (urgent.length) {
                    options += '<optgroup label="📋 紧急/今日任务">';
                    urgent.forEach(t => { options += `<option value="todo:${t.title}">${t.title}</option>`; });
                    options += '</optgroup>';
                }
            } catch(e) {}
            sel.innerHTML = options;
            if (currentVal) sel.value = currentVal;
        }

        async function pomoStartWithTask() {
            const sel = document.getElementById('pomoFocusSelect');
            const val = sel.value;
            let focusTask = null, taskSource = null;
            if (val) {
                const parts = val.split(':');
                taskSource = parts[0];
                focusTask = parts.slice(1).join(':');
            }
            try {
                await fetch('/api/pomodoro/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({focus_task: focusTask, task_source: taskSource})
                });
            } catch(e) {}
            loadPomoStatus();
        }

        async function loadPomoStatus() {
            try {
                const res = await fetch('/api/pomodoro/status');
                const s = await res.json();
                // Timer display
                document.getElementById('pomoTime').textContent = s.remaining_display || '00:00';
                document.getElementById('pomoLabel').textContent = s.phase_label || '空闲';
                // Ring
                const ring = document.getElementById('pomoRing');
                const circumference = 553;
                ring.style.strokeDashoffset = circumference * (1 - s.progress);
                ring.style.stroke = s.is_break ? 'var(--blue)' : s.phase==='idle'?'var(--text-muted)':'var(--green)';
                // Cycles
                const cd = document.getElementById('pomoCycles');
                let dots = '';
                for(let i=1;i<=s.cycles_before_long;i++) {
                    let cls = 'pomo-dot';
                    if(i < s.current_cycle || (s.is_break && i <= s.current_cycle)) cls += ' done';
                    else if(i === s.current_cycle && s.phase==='working') cls += ' current';
                    dots += `<div class="${cls}"></div>`;
                }
                cd.innerHTML = dots;
                // Focus task display
                const focusEl = document.getElementById('pomoCurrentFocus');
                const focusArea = document.getElementById('pomoFocusArea');
                if (s.phase === 'working' && s.focus_task) {
                    focusEl.textContent = '🎯 ' + s.focus_task;
                    focusEl.style.display = 'block';
                    document.getElementById('pomoFocusSelect').style.display = 'none';
                } else {
                    focusEl.style.display = 'none';
                    document.getElementById('pomoFocusSelect').style.display = '';
                }
                // Buttons
                const btns = document.getElementById('pomoBtns');
                if(s.phase==='idle') btns.innerHTML = `<button class="btn btn-green" onclick="pomoStartWithTask()">▶ 开始专注</button>`;
                else if(s.phase==='working') btns.innerHTML = `<button class="btn btn-amber" onclick="pomoAction('pause')">⏸ 暂停</button><button class="btn btn-red" onclick="pomoAction('stop')">⏹ 停止</button>`;
                else if(s.phase==='paused') btns.innerHTML = `<button class="btn btn-green" onclick="pomoAction('resume')">▶ 继续</button><button class="btn btn-red" onclick="pomoAction('stop')">⏹ 停止</button>`;
                else if(s.is_break) btns.innerHTML = `<button class="btn btn-amber" onclick="pomoAction('skip-break')">⏩ 跳过休息</button>`;
                // Stats
                document.getElementById('pomoCompleted').textContent = s.completed_cycles;
                document.getElementById('pomoWorkMin').textContent = s.total_work_minutes + ' 分钟';
                document.getElementById('pomoBreakMin').textContent = s.total_break_minutes + ' 分钟';
                document.getElementById('pomoSkipped').textContent = s.skipped_breaks + ' 次';
                // Focus Session Log
                const logEl = document.getElementById('pomoSessionLog');
                const sessions = s.focus_sessions || [];
                if (sessions.length) {
                    logEl.innerHTML = sessions.map(fs => `<div style="display:flex;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;">
                        <span style="color:var(--text-muted);font-family:var(--mono);min-width:44px;">${fs.completed_at||''}</span>
                        <span style="flex:1;color:var(--text-primary);">${fs.task||'自由专注'}</span>
                        <span style="color:var(--green);">${fs.duration_minutes}min</span>
                    </div>`).join('');
                } else {
                    logEl.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:4px 0;">今日尚无专注记录</div>';
                }
                // Settings
                if(s.settings) {
                    document.getElementById('pomoWorkMins').value = s.settings.work_minutes;
                    document.getElementById('pomoShortBreak').value = s.settings.short_break_minutes;
                    document.getElementById('pomoLongBreak').value = s.settings.long_break_minutes;
                    document.getElementById('pomoForceBreak').checked = s.settings.force_break;
                }
            } catch(e) {}
        }
        async function pomoAction(action) {
            await fetch('/api/pomodoro/'+action, {method:'POST'});
            loadPomoStatus();
        }
        async function updatePomoSettings() {
            const params = new URLSearchParams({
                work_minutes: document.getElementById('pomoWorkMins').value,
                short_break_minutes: document.getElementById('pomoShortBreak').value,
                long_break_minutes: document.getElementById('pomoLongBreak').value,
                force_break: document.getElementById('pomoForceBreak').checked,
            });
            await fetch('/api/pomodoro/settings?'+params, {method:'POST'});
        }

        // ==================== SCREEN BLUR ====================
        function showBlur(remaining, display) {
            const overlay = document.getElementById('blurOverlay');
            overlay.classList.add('show');
            document.getElementById('blurTimer').textContent = display;
            const tips = ['站起来伸展一下身体','闭上眼睛深呼吸3次','看看窗外远处的风景','给自己倒杯水','活动一下脖子和肩膀'];
            document.getElementById('blurTip').textContent = tips[Math.floor(Math.random()*tips.length)];
        }
        function hideBlur() { document.getElementById('blurOverlay').classList.remove('show'); }
        async function skipBreak() {
            if(confirm('确定要跳过休息吗？适当的休息能让你更高效地工作。')) {
                await fetch('/api/pomodoro/skip-break', {method:'POST'});
                hideBlur();
            }
        }

        // ==================== CHECKIN SETTINGS ====================
        async function loadCheckinSettings() {
            try {
                const r = await (await fetch('/api/checkin/status')).json();
                const s = r.settings || {};
                document.getElementById('checkinEnabled').checked = s.enabled !== false;
                document.getElementById('checkinInterval').value = s.interval_minutes || 60;
                document.getElementById('checkinStartHour').value = s.start_hour || 9;
                document.getElementById('checkinEndHour').value = s.end_hour || 23;
                document.getElementById('checkinSummaryHour').value = s.evening_summary_hour || 22;
                document.getElementById('checkinSound').checked = s.sound_enabled !== false;
                const t = document.getElementById('checkinStatusText');
                if (r.running && r.next_checkin) {
                    const m = r.minutes_until_next;
                    t.textContent = m > 0 ? `下次签到: ${r.next_checkin} (${m}分钟后)` : '即将签到';
                } else if (s.enabled) {
                    t.textContent = '签到已启用';
                } else {
                    t.textContent = '签到已禁用';
                }
            } catch(e) {}
        }
        async function toggleCheckin() {
            const e = document.getElementById('checkinEnabled').checked;
            await fetch(`/api/checkin/toggle?enabled=${e}`, {method:'POST'});
            loadCheckinSettings();
        }
        async function updateCheckinSettings() {
            const p = new URLSearchParams({
                interval_minutes: document.getElementById('checkinInterval').value,
                start_hour: document.getElementById('checkinStartHour').value,
                end_hour: document.getElementById('checkinEndHour').value,
                evening_summary_hour: document.getElementById('checkinSummaryHour').value,
                sound_enabled: document.getElementById('checkinSound').checked,
            });
            await fetch('/api/checkin/settings?' + p, {method:'POST'});
            loadCheckinSettings();
        }

        // ==================== BREAK SETTINGS ====================
        async function loadBreakSettings() {
            try {
                const r = await (await fetch('/api/break/settings')).json();
                const s = r.settings || {};
                document.getElementById('breakEnabled').checked = s.enabled !== false;
                document.getElementById('breakInterval').value = s.interval_minutes || 45;
                document.getElementById('breakSound').checked = s.sound_enabled !== false;
                document.getElementById('breakRestEndEnabled').checked = s.rest_end_reminder_enabled !== false;
                document.getElementById('breakRestEndMinutes').value = s.rest_end_reminder_minutes || 10;
                document.getElementById('breakRestEndSound').checked = s.rest_end_sound_enabled !== false;
                document.getElementById('breakRestEndChat').checked = s.rest_end_chat_enabled !== false;
                const st = r.status || {};
                const t = document.getElementById('breakStatusText');
                if (st.running && st.next_reminder) {
                    const m = st.minutes_until_next;
                    t.textContent = m > 0 ? '下次提醒: ' + st.next_reminder + ' (' + m + '分钟后)' : '即将提醒';
                } else if (s.enabled) {
                    t.textContent = '休息提醒已启用';
                } else {
                    t.textContent = '休息提醒已禁用';
                }
            } catch(e) {}
        }
        async function updateBreakSettings() {
            const p = new URLSearchParams({
                enabled: document.getElementById('breakEnabled').checked,
                interval_minutes: document.getElementById('breakInterval').value,
                sound_enabled: document.getElementById('breakSound').checked,
                rest_end_reminder_enabled: document.getElementById('breakRestEndEnabled').checked,
                rest_end_reminder_minutes: document.getElementById('breakRestEndMinutes').value,
                rest_end_sound_enabled: document.getElementById('breakRestEndSound').checked,
                rest_end_chat_enabled: document.getElementById('breakRestEndChat').checked,
            });
            await fetch('/api/break/settings?' + p, {method:'POST'});
            loadBreakSettings();
        }

        // ==================== DAILY REPORT ====================
        async function openReport() {
            try {
                const r = await (await fetch('/api/report/yesterday')).json();
                if(!r.has_data){
                    const r2 = await (await fetch('/api/report/latest')).json();
                    if(r2.has_data) renderReport(r2); else alert('暂无报告数据');
                    return;
                }
                renderReport(r);
            } catch(e){alert('加载报告失败');}
        }
        async function generateReport() {
            try{const r=await(await fetch('/api/report/generate',{method:'POST'})).json();
            if(r.has_data)renderReport(r);else alert('没有足够的数据生成报告');}catch(e){alert('生成失败');}
        }
        function renderReport(r) {
            document.getElementById('reportDate').textContent = `${r.date} ${r.weekday||''} · 生成于 ${r.generated_at||''}`;
            const s = r.summary||{};
            const cmp = r.comparison||{};
            let html = `<div class="report-stat-grid">
                <div class="report-stat"><div class="val" style="color:var(--green)">${Math.round((s.productive_ratio||0)*100)}%</div><div class="lab">生产率</div>
                ${cmp.productive_delta?`<div style="font-size:11px;margin-top:4px;" class="${cmp.productive_delta>=0?'delta-up':'delta-down'}">${cmp.productive_delta>=0?'↑':'↓'} ${Math.abs(Math.round(cmp.productive_delta*100))}% vs 均值</div>`:''}</div>
                <div class="report-stat"><div class="val" style="color:var(--red)">${Math.round((s.distracted_ratio||0)*100)}%</div><div class="lab">分心率</div>
                ${cmp.distracted_delta?`<div style="font-size:11px;margin-top:4px;" class="${cmp.distracted_delta<=0?'delta-up':'delta-down'}">${cmp.distracted_delta<=0?'↓':'↑'} ${Math.abs(Math.round(cmp.distracted_delta*100))}% vs 均值</div>`:''}</div>
                <div class="report-stat"><div class="val">${s.total_records||0}</div><div class="lab">记录数</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">活跃 ${s.active_hours||0} 小时</div></div>
            </div>`;
            // Category distribution
            const cats = r.app_usage?.category_ratios||{};
            if(Object.keys(cats).length) {
                html += `<div class="card-title" style="margin:16px 0 8px;">应用类别分布</div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">`;
                const catNames = {work:'工作',communication:'沟通',learning:'学习',entertainment:'娱乐',unknown:'其他'};
                const catColors = {work:'var(--green)',communication:'var(--blue)',learning:'var(--purple)',entertainment:'var(--red)',unknown:'var(--text-muted)'};
                for(const [k,v] of Object.entries(cats)){
                    if(v>0) html += `<span class="badge" style="background:${catColors[k]||'var(--text-muted)'}22;color:${catColors[k]||'var(--text-muted)'}">${catNames[k]||k} ${Math.round(v*100)}%</span>`;
                }
                html += `</div>`;
            }
            // Top apps
            const apps = r.app_usage?.top_apps||[];
            if(apps.length){
                html += `<div class="card-title" style="margin:16px 0 8px;">TOP 应用</div>`;
                apps.slice(0,5).forEach(a=>{html+=`<div class="status-row"><span class="sr-label">${a.app}</span><span class="sr-value">${a.minutes} 分钟</span></div>`;});
            }
            // Suggestions
            const sug = r.suggestions||[];
            if(sug.length){
                html += `<div class="card-title" style="margin:20px 0 10px;">💡 建议</div>`;
                sug.forEach(s=>{html+=`<div class="suggestion-card"><div class="suggestion-icon">${s.icon}</div><div class="suggestion-body"><div class="suggestion-title">${s.title}</div><div class="suggestion-text">${s.content}</div></div></div>`;});
            }
            document.getElementById('reportContent').innerHTML = html;
            document.getElementById('reportModal').classList.add('show');
        }
        function closeReport(){document.getElementById('reportModal').classList.remove('show');}

        // ==================== TODO LIST ====================
        async function loadTodos() {
            try{const r=await(await fetch('/api/todos')).json();
            renderTodos(r.todos||[]);
            const st=r.stats||{};
            document.getElementById('todoTotal').textContent=st.total||0;
            document.getElementById('todoPending').textContent=st.pending||0;
            document.getElementById('todoDueToday').textContent=st.due_today||0;
            document.getElementById('todoOverdue').textContent=st.overdue||0;
            document.getElementById('todoCompleted').textContent=st.completed||0;
            }catch(e){console.error(e);}
        }
        function renderTodos(todos) {
            const el = document.getElementById('todoList');
            if(!todos.length){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:13px;">暂无任务，添加一个吧 ✨</div>';return;}
            const priIcons={urgent:'🔴',high:'🟠',normal:'',low:'🔵'};
            const priLabels={urgent:'紧急',high:'重要',normal:'',low:'低优先'};
            const priColors={urgent:'red',high:'amber',low:'blue'};
            el.innerHTML = todos.map(t=>{
                const dlDate = t.deadline ? t.deadline.split(' ')[0] : null;
                const dlTime = t.deadline_time || null;
                let deadlineStr = '';
                if(t.completed) {
                    // 已完成的任务：显示完成标记，不显示逾期
                    const completedAt = t.completed_at ? t.completed_at.substring(0,10) : '';
                    deadlineStr = completedAt ? `<span style="color:var(--green);font-size:11px;">✓ ${completedAt} 完成</span>` : '';
                } else if(dlDate){
                    const timeTag = dlTime ? ` ${dlTime}` : '';
                    if(t.is_overdue) deadlineStr = `<span class="overdue">⚠ 已逾期${timeTag ? ' ('+dlTime+')' : ''}</span>`;
                    else if(t.days_until_deadline===0) deadlineStr = `<span style="color:var(--amber)">📅 今天${timeTag}到期</span>`;
                    else deadlineStr = `<span style="color:var(--text-muted)">📅 ${dlDate}${timeTag}</span>`;
                }
                const pi = priIcons[t.priority]||'';
                const priHtml = (t.priority&&t.priority!=='normal'&&!t.completed) ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--${priColors[t.priority]||'blue'}-dim,rgba(100,100,100,0.1));color:var(--${priColors[t.priority]||'blue'});">${priLabels[t.priority]}</span>` : '';
                const tagsHtml = (t.tags&&t.tags.length&&!t.completed) ? t.tags.map(tg=>`<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--green-dim,rgba(16,185,129,0.1));color:var(--green);">${tg}</span>`).join(' ') : '';
                const checkTitle = t.completed ? '点击取消完成' : '点击标记完成';
                return `<div class="todo-item${t.completed?' todo-done':''}">
                    <div class="todo-check ${t.completed?'done':''}" onclick="toggleTodo('${t.id}')" title="${checkTitle}">${t.completed?'✓':''}</div>
                    <div class="todo-body">
                        <div class="todo-title ${t.completed?'done':''}">${pi?pi+' ':''}${t.title}</div>
                        <div class="todo-meta">${deadlineStr?deadlineStr+' ':''}${priHtml}${tagsHtml?' '+tagsHtml:''}</div>
                    </div>
                    <button class="todo-del" onclick="deleteTodo('${t.id}')" title="删除">✕</button>
                </div>`;
            }).join('');
        }

        // ==================== 智能添加 ====================
        let _pendingSmartText = '';

        async function smartAddTodo() {
            const input = document.getElementById('todoSmartInput');
            const text = input.value.trim();
            if(!text) return;
            _pendingSmartText = text;

            // 直接提交（后端会用 LLM 解析）
            const btn = document.getElementById('smartAddBtn');
            btn.textContent = '⏳';
            btn.disabled = true;
            try {
                const resp = await fetch('/api/todos/smart-add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text})
                });
                const r = await resp.json();
                if(r.success) {
                    input.value = '';
                    hidePreview();
                    loadTodos();
                } else {
                    alert(r.error || '添加失败');
                }
            } catch(e) {
                console.error(e);
                alert('网络错误，请重试');
            } finally {
                btn.textContent = '＋';
                btn.disabled = false;
            }
        }

        function showPreview(parsed) {
            const prev = document.getElementById('parsePreview');
            document.getElementById('previewTitle').textContent = '📌 ' + (parsed.title||'');
            const dlEl = document.getElementById('previewDeadline');
            if(parsed.deadline) { dlEl.textContent = '📅 ' + parsed.deadline; dlEl.style.display='inline-block'; }
            else { dlEl.style.display='none'; }
            const priEl = document.getElementById('previewPriority');
            const priMap = {urgent:'🔴 紧急',high:'🟠 重要',normal:'',low:'🔵 低优先'};
            if(parsed.priority && parsed.priority!=='normal') { priEl.textContent=priMap[parsed.priority]||''; priEl.style.display='inline-block'; }
            else { priEl.style.display='none'; }
            const tagsEl = document.getElementById('previewTags');
            tagsEl.innerHTML = (parsed.tags||[]).map(t=>`<span class="tag-item">${t}</span>`).join('');
            prev.style.display = 'block';
        }
        function hidePreview() {
            document.getElementById('parsePreview').style.display='none';
            _pendingSmartText = '';
        }
        async function confirmSmartAdd() {
            if(!_pendingSmartText) return;
            const resp = await fetch('/api/todos/smart-add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: _pendingSmartText})
            });
            const r = await resp.json();
            if(r.success) {
                document.getElementById('todoSmartInput').value = '';
                hidePreview();
                loadTodos();
            }
        }

        // 保留旧的 addTodo 兼容
        async function addTodo() { await smartAddTodo(); }

        async function toggleTodo(id){await fetch(`/api/todos/${id}/toggle`,{method:'POST'});loadTodos();}
        async function deleteTodo(id){if(confirm('确认删除？')){await fetch(`/api/todos/${id}`,{method:'DELETE'});loadTodos();}}

        // ==================== VOICE INPUT (SenseVoice Backend) ====================
        let mediaRecorder = null, audioChunks = [], isRecording = false;

        async function toggleVoice() {
            if (isRecording) { stopVoice(); return; }

            // 检查 SenseVoice 后端是否可用
            try {
                const statusRes = await fetch('/api/speech/status');
                const statusData = await statusRes.json();
                if (!statusData.available) {
                    // fallback: 尝试浏览器 Web Speech API
                    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                        toggleVoiceFallback(); return;
                    }
                    alert('语音识别不可用。请安装 SenseVoice: pip install funasr modelscope'); return;
                }
            } catch(e) {
                // 后端不可达时 fallback
                if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                    toggleVoiceFallback(); return;
                }
                alert('语音服务连接失败'); return;
            }

            // 使用 MediaRecorder 录音 → 发送到 SenseVoice 后端
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                audioChunks = [];
                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
                mediaRecorder.onstop = async () => {
                    stream.getTracks().forEach(t => t.stop());
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const formData = new FormData();
                    formData.append('audio', audioBlob, 'voice.webm');

                    try {
                        document.getElementById('voiceHint').textContent = '识别中...';
                        const res = await fetch('/api/speech/transcribe', { method: 'POST', body: formData });
                        const result = await res.json();
                        if (result.success && result.text) {
                            document.getElementById('todoSmartInput').value = result.text;
                            // 显示情感标签（SenseVoice 附加功能）
                            if (result.emotion && result.emotion !== 'neutral') {
                                console.log('语音情感:', result.emotion);
                            }
                            smartAddTodo();
                        } else {
                            alert('语音识别失败: ' + (result.error || '未识别到内容'));
                        }
                    } catch(err) { console.error('Speech transcribe error:', err); }
                    document.getElementById('voiceHint').textContent = '🎙 说出你的任务...';
                    setVoiceUI(false);
                };
                mediaRecorder.start();
                setVoiceUI(true);
                // 自动 5 秒后停止
                setTimeout(() => { if (isRecording) stopVoice(); }, 5000);
            } catch(e) {
                alert('无法访问麦克风: ' + e.message);
            }
        }

        function stopVoice() {
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                mediaRecorder.stop();
            }
            setVoiceUI(false);
        }

        function setVoiceUI(recording) {
            isRecording = recording;
            const btn = document.getElementById('voiceBtn');
            const hint = document.getElementById('voiceHint');
            if (recording) {
                btn.classList.add('recording');
                hint.classList.add('show');
            } else {
                btn.classList.remove('recording');
                hint.classList.remove('show');
            }
        }

        // Fallback: 浏览器 Web Speech API（Chrome/Edge）
        let recognition = null;
        function toggleVoiceFallback() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            recognition = new SR();
            recognition.lang = 'zh-CN';
            recognition.continuous = false;
            recognition.interimResults = false;
            recognition.onresult = (e) => {
                document.getElementById('todoSmartInput').value = e.results[0][0].transcript;
                setVoiceUI(false);
                smartAddTodo();
            };
            recognition.onerror = () => { setVoiceUI(false); };
            recognition.onend = () => { setVoiceUI(false); };
            recognition.start();
            setVoiceUI(true);
        }

        // ==================== DAILY BRIEFING ====================
        var _briefingData = null;

        async function checkBriefing() {
            try {
                const res = await fetch('/api/briefing');
                _briefingData = await res.json();
                if (_briefingData.needs_briefing) {
                    showBriefingModal(_briefingData);
                }
                renderGoalsPanel(_briefingData.goals || []);
            } catch(e) { console.error('Briefing check failed:', e); }
        }

        function showBriefingModal(data) {
            const modal = document.getElementById('briefingModal');
            const dateEl = document.getElementById('briefingDate');
            const alertsEl = document.getElementById('briefingAlerts');
            const greetEl = document.getElementById('briefingGreeting');

            // 问候语
            const hour = new Date().getHours();
            greetEl.textContent = hour < 12 ? '早上好 ☀️' : hour < 18 ? '下午好 🌤' : '晚上好 🌙';
            dateEl.textContent = data.date;

            // 提醒区域
            let alertHtml = '';
            if (data.overdue && data.overdue.length) {
                alertHtml += `<div style="padding:10px 14px;background:var(--red-dim);border:1px solid rgba(248,113,113,0.2);border-radius:var(--radius-sm);margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:var(--red);margin-bottom:6px;">⚠️ 有 ${data.overdue.length} 个逾期任务</div>
                    ${data.overdue.map(t => `<div style="font-size:12px;color:var(--text-secondary);padding:2px 0;">· ${t.title}${t.deadline ? ' (截止 '+t.deadline+')' : ''}</div>`).join('')}
                </div>`;
            }
            if (data.due_today && data.due_today.length) {
                alertHtml += `<div style="padding:10px 14px;background:var(--amber-dim);border:1px solid rgba(251,191,36,0.2);border-radius:var(--radius-sm);margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:var(--amber);margin-bottom:6px;">📅 今日到期 (${data.due_today.length})</div>
                    ${data.due_today.map(t => `<div style="font-size:12px;color:var(--text-secondary);padding:2px 0;">· ${t.title}${t.deadline_time ? ' ('+t.deadline_time+')' : ''}</div>`).join('')}
                </div>`;
            }
            if (data.upcoming && data.upcoming.length) {
                alertHtml += `<div style="padding:10px 14px;background:var(--blue-dim);border:1px solid rgba(96,165,250,0.2);border-radius:var(--radius-sm);margin-bottom:8px;">
                    <div style="font-size:13px;font-weight:600;color:var(--blue);margin-bottom:6px;">📋 即将到期 (${data.upcoming.length})</div>
                    ${data.upcoming.slice(0,5).map(t => `<div style="font-size:12px;color:var(--text-secondary);padding:2px 0;">· ${t.title} (${t.days_until_deadline}天后)</div>`).join('')}
                </div>`;
            }
            if (!alertHtml) {
                alertHtml = '<div style="padding:8px 14px;background:var(--green-dim);border-radius:var(--radius-sm);font-size:13px;color:var(--green);">✅ 今天没有紧急的 deadline 任务。</div>';
            }
            alertsEl.innerHTML = alertHtml;

            // Reset inputs
            document.getElementById('briefingGoalInputs').innerHTML = `
                <div style="display:flex;gap:8px;margin-bottom:8px;">
                    <input type="text" class="todo-input briefing-goal-input" placeholder="今日最重要的任务..." style="flex:1;" onkeydown="if(event.key==='Enter'){event.preventDefault();addBriefingGoalInput();}">
                </div>`;

            modal.classList.add('show');
        }

        function addBriefingGoalInput() {
            const container = document.getElementById('briefingGoalInputs');
            const inputs = container.querySelectorAll('.briefing-goal-input');
            if (inputs.length >= 5) return; // 最多5个
            // 如果最后一个input为空则聚焦它
            const last = inputs[inputs.length - 1];
            if (last && !last.value.trim()) { last.focus(); return; }
            const div = document.createElement('div');
            div.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;';
            div.innerHTML = `<input type="text" class="todo-input briefing-goal-input" placeholder="还有什么想做的..." style="flex:1;" onkeydown="if(event.key==='Enter'){event.preventDefault();addBriefingGoalInput();}">`;
            container.appendChild(div);
            div.querySelector('input').focus();
        }

        async function submitBriefing() {
            const inputs = document.querySelectorAll('.briefing-goal-input');
            const goals = Array.from(inputs).map(i => i.value.trim()).filter(Boolean);
            if (!goals.length) {
                // 如果没输入目标也允许提交，只是给个提示
                if (!confirm('没有输入任何目标，确定开始工作吗？')) return;
            }
            try {
                await fetch('/api/briefing/goals', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({goals: goals.length ? goals : ['自由探索']})
                });
            } catch(e) { console.error(e); }
            document.getElementById('briefingModal').classList.remove('show');
            checkBriefing(); // refresh goals panel
        }

        async function dismissBriefing() {
            try { await fetch('/api/briefing/dismiss', {method:'POST'}); } catch(e) {}
            document.getElementById('briefingModal').classList.remove('show');
        }

        // ---- Today's Goals Panel (in todo sidebar) ----
        function renderGoalsPanel(goals) {
            const box = document.getElementById('todayGoalsPanel');
            if (!goals || !goals.length) {
                box.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:4px 0;">今日尚未设定目标。<span style="cursor:pointer;color:var(--blue);text-decoration:underline;" onclick="showBriefingManual()">现在设定</span></div>';
                return;
            }
            box.innerHTML = goals.map((g, i) => {
                const done = g.done;
                return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);">
                    <div class="todo-check ${done?'done':''}" onclick="toggleGoal(${i})" style="width:18px;height:18px;font-size:10px;">${done?'✓':''}</div>
                    <span style="flex:1;font-size:13px;${done?'text-decoration:line-through;color:var(--text-muted);':''}">${g.text}</span>
                    <button style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:12px;padding:2px 4px;" onclick="removeGoal(${i})">✕</button>
                </div>`;
            }).join('');
        }

        async function toggleGoal(index) {
            try {
                const res = await fetch(`/api/briefing/goals/${index}/toggle`, {method:'POST'});
                const data = await res.json();
                renderGoalsPanel(data.goals || []);
            } catch(e) {}
        }

        async function removeGoal(index) {
            try {
                const res = await fetch(`/api/briefing/goals/${index}/remove`, {method:'POST'});
                const data = await res.json();
                renderGoalsPanel(data.goals || []);
            } catch(e) {}
        }

        async function addGoalFromPanel() {
            const input = document.getElementById('addGoalInput');
            const text = input.value.trim();
            if (!text) { input.focus(); return; }
            try {
                const res = await fetch('/api/briefing/goals/add', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({text: text})
                });
                const data = await res.json();
                renderGoalsPanel(data.goals || []);
                input.value = '';
            } catch(e) {}
        }

        function showBriefingManual() {
            // Manually trigger briefing modal
            fetch('/api/briefing').then(r=>r.json()).then(data => {
                _briefingData = data;
                showBriefingModal(data);
            });
        }

        // ==================== EVENING REVIEW ====================
        async function openEveningReview() {
            try {
                const res = await fetch('/api/briefing/evening-review');
                const data = await res.json();
                renderEveningReview(data);
                document.getElementById('eveningReviewModal').classList.add('show');
            } catch(e) { console.error('Evening review failed:', e); }
        }

        function renderEveningReview(data) {
            document.getElementById('reviewDate').textContent = data.date || '';
            const box = document.getElementById('eveningReviewContent');
            const r = data.reflection || {};
            const prod = data.productivity || {};

            let html = '';

            // Overall score
            html += `<div style="text-align:center;padding:16px 0;">
                <div style="font-size:48px;">${r.overall_emoji||'📊'}</div>
                <div style="font-size:16px;font-weight:600;margin:8px 0;">${r.overall_message||''}</div>
                <div style="font-size:13px;color:var(--text-muted);">综合评分: ${r.score||0}/100</div>
            </div>`;

            // Goals comparison
            if (data.total_goals > 0) {
                html += `<div class="card-title" style="margin-top:12px;">🎯 目标完成情况 (${data.completed_goals}/${data.total_goals})</div>`;
                html += '<div style="margin-bottom:12px;">';
                (data.goals||[]).forEach(g => {
                    const icon = g.done ? '✅' : '❌';
                    html += `<div style="display:flex;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);font-size:13px;">
                        <span>${icon}</span>
                        <span style="${g.done?'color:var(--text-muted);text-decoration:line-through;':''}">${g.text}</span>
                    </div>`;
                });
                html += '</div>';
                // completion bar
                const pct = Math.round(data.goal_completion_rate * 100);
                html += `<div style="background:var(--bg-card);border-radius:4px;height:8px;overflow:hidden;margin-bottom:16px;">
                    <div style="height:100%;width:${pct}%;background:${pct>=80?'var(--green)':pct>=50?'var(--amber)':'var(--red)'};border-radius:4px;transition:width .5s;"></div>
                </div>`;
            }

            // Productivity stats
            if (prod.total_records > 0) {
                html += `<div class="card-title">📊 效率数据</div>`;
                html += `<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;">
                    <div style="text-align:center;padding:12px;background:var(--bg-card);border-radius:var(--radius-sm);">
                        <div style="font-family:var(--mono);font-size:20px;font-weight:700;color:var(--green);">${Math.round(prod.productive_ratio*100)}%</div>
                        <div style="font-size:11px;color:var(--text-muted);">生产率</div>
                    </div>
                    <div style="text-align:center;padding:12px;background:var(--bg-card);border-radius:var(--radius-sm);">
                        <div style="font-family:var(--mono);font-size:20px;font-weight:700;color:var(--red);">${Math.round(prod.distracted_ratio*100)}%</div>
                        <div style="font-size:11px;color:var(--text-muted);">分心率</div>
                    </div>
                    <div style="text-align:center;padding:12px;background:var(--bg-card);border-radius:var(--radius-sm);">
                        <div style="font-family:var(--mono);font-size:20px;font-weight:700;color:var(--blue);">${prod.total_records}</div>
                        <div style="font-size:11px;color:var(--text-muted);">采样点</div>
                    </div>
                </div>`;
                if (data.work_start) {
                    html += `<div style="font-size:13px;color:var(--text-secondary);margin-bottom:8px;">⏰ 开工时间: <b>${data.work_start.substring(0,5)}</b></div>`;
                }
            }

            // Pomodoro sessions
            const pomo = data.pomodoro_stats || {};
            const sessions = data.focus_sessions || [];

            // Radar chart — 五维评分
            if (prod.total_records > 0 || pomo.completed_cycles > 0 || data.total_goals > 0) {
                const goalScore = data.total_goals > 0 ? Math.round(data.goal_completion_rate * 100) : 50;
                const prodScore = Math.round((prod.productive_ratio || 0) * 100);
                const focusScore = Math.min(Math.round((pomo.total_work_minutes || 0) / 120 * 100), 100);
                const startScore = data.work_start ? (parseInt(data.work_start.substring(0,2)) <= 9 ? 90 : parseInt(data.work_start.substring(0,2)) <= 10 ? 70 : 40) : 50;
                const lowDistScore = Math.round((1 - (prod.distracted_ratio || 0)) * 100);

                html += `<div class="card-title">📈 五维评分</div>`;
                html += `<div style="max-width:280px;margin:0 auto 16px auto;"><canvas id="reviewRadarChart"></canvas></div>`;

                // defer chart creation
                setTimeout(() => {
                    const radarCtx = document.getElementById('reviewRadarChart');
                    if (radarCtx) {
                        new Chart(radarCtx, {
                            type: 'radar',
                            data: {
                                labels: ['目标完成', '生产率', '专注时长', '准时开工', '低分心'],
                                datasets: [{
                                    data: [goalScore, prodScore, focusScore, startScore, lowDistScore],
                                    backgroundColor: 'rgba(99, 132, 255, 0.15)',
                                    borderColor: 'rgba(99, 132, 255, 0.8)',
                                    borderWidth: 2,
                                    pointBackgroundColor: 'rgba(99, 132, 255, 1)',
                                    pointRadius: 3,
                                }]
                            },
                            options: {
                                scales: { r: { beginAtZero: true, max: 100, ticks: { display: false }, grid: { color: 'rgba(128,128,128,0.2)' }, pointLabels: { font: { size: 11 } } } },
                                plugins: { legend: { display: false } },
                                responsive: true,
                                maintainAspectRatio: true,
                            }
                        });
                    }
                }, 100);
            }

            if (pomo.completed_cycles > 0) {
                html += `<div class="card-title">🍅 番茄钟</div>`;
                html += `<div style="font-size:13px;color:var(--text-secondary);margin-bottom:8px;">完成 <b>${pomo.completed_cycles}</b> 个番茄，专注 <b>${pomo.total_work_minutes}</b> 分钟</div>`;
                if (sessions.length) {
                    html += '<div style="margin-bottom:12px;">';
                    sessions.forEach(s => {
                        html += `<div style="display:flex;gap:8px;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border);">
                            <span style="color:var(--text-muted);font-family:var(--mono);min-width:44px;">${s.completed_at||''}</span>
                            <span style="flex:1;">${s.task||'自由专注'}</span>
                            <span style="color:var(--green);">${s.duration_minutes}min</span>
                        </div>`;
                    });
                    html += '</div>';
                }
            }

            // Highlights & areas to improve
            if (r.highlights && r.highlights.length) {
                html += '<div style="padding:12px;background:var(--green-dim);border-radius:var(--radius-sm);margin-bottom:8px;">';
                r.highlights.forEach(h => { html += `<div style="font-size:13px;color:var(--green);padding:2px 0;">${h}</div>`; });
                html += '</div>';
            }
            if (r.areas_to_improve && r.areas_to_improve.length) {
                html += '<div style="padding:12px;background:var(--amber-dim);border-radius:var(--radius-sm);margin-bottom:8px;">';
                r.areas_to_improve.forEach(a => { html += `<div style="font-size:13px;color:var(--amber);padding:2px 0;">⚡ ${a}</div>`; });
                html += '</div>';
            }

            // LLM 鼓励语（如果有）
            if (r.encouragement) {
                html += `<div style="padding:12px;text-align:center;font-size:14px;color:var(--text-secondary);margin-top:12px;font-style:italic;">💬 ${r.encouragement}</div>`;
            }

            box.innerHTML = html;
        }

        // Load todos on first visit
        loadTodos();

        // ==================== CHECKIN ====================
        const FEEL_ICONS = {great:'🔥',good:'😊',normal:'😐',tired:'😴',bad:'😫'};
        const FEEL_LABELS = {great:'极佳',good:'不错',normal:'一般',tired:'有点累',bad:'很差'};
        const CAT_ICONS = {
            coding:'💻',writing:'✍️',meeting:'🤝',learning:'📚',reading:'📖',
            communication:'💬',rest:'☕',entertainment:'🎮',exercise:'🏃',
            meal:'🍜',work:'💼',other:'📌'
        };

        async function loadCheckinData() {
            try {
                const [todayRes, statusRes] = await Promise.all([
                    fetch('/api/checkin/today'), fetch('/api/checkin/status')
                ]);
                const today = await todayRes.json();
                const status = await statusRes.json();
                renderCheckinTimeline(today.entries || []);
                renderCheckinStatus(status);
            } catch(e) { console.error('Load checkin failed:', e); }
        }

        function renderCheckinTimeline(entries) {
            const box = document.getElementById('checkinTimeline');
            if (!entries.length) {
                box.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:16px 0;">今天还没有签到记录。试试上方的快速签到，或等待整点弹窗。</div>';
                return;
            }
            box.innerHTML = entries.map(e => {
                const time = (e.timestamp||'').split(' ')[1]||'';
                const timeShort = time.substring(0,5);
                if (e.skipped) {
                    return `<div style="display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border);opacity:0.5;">
                        <div style="font-family:var(--mono);font-size:13px;color:var(--text-muted);min-width:44px;">${timeShort}</div>
                        <div style="font-size:13px;color:var(--text-muted);">— 跳过 —</div>
                    </div>`;
                }
                const feelIcon = FEEL_ICONS[e.feeling]||'😐';
                const catIcon = CAT_ICONS[e.category]||'📌';
                const feelColor = e.feeling==='great'?'var(--green)':e.feeling==='good'?'var(--blue)':e.feeling==='tired'?'var(--amber)':e.feeling==='bad'?'var(--red)':'var(--text-secondary)';
                return `<div style="display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border);">
                    <div style="font-family:var(--mono);font-size:13px;color:var(--text-muted);min-width:44px;">${timeShort}</div>
                    <div style="flex:1;">
                        <div style="font-size:14px;color:var(--text-primary);margin-bottom:4px;">${e.doing||'—'}</div>
                        <div style="font-size:12px;color:var(--text-secondary);display:flex;gap:10px;flex-wrap:wrap;">
                            <span>${catIcon} ${e.category||'other'}</span>
                            <span style="color:${feelColor};">${feelIcon} ${FEEL_LABELS[e.feeling]||'一般'}</span>
                            ${e.auto_app?`<span style="color:var(--text-muted);">🪟 ${e.auto_app}</span>`:''}
                        </div>
                    </div>
                </div>`;
            }).join('');
        }

        function renderCheckinStatus(status) {
            document.getElementById('checkinCount').textContent = (status.stats||{}).checkins_today || 0;
            document.getElementById('checkinSkipped').textContent = (status.stats||{}).skipped_today || 0;
            document.getElementById('checkinNext').textContent = status.next_checkin || '--';
            document.getElementById('checkinRunning').textContent = status.running ? '✅ 运行中' : '⏹ 已停止';
        }

        async function submitWebCheckin() {
            const input = document.getElementById('checkinInput');
            const feeling = document.getElementById('checkinFeeling').value;
            const doing = input.value.trim();
            if (!doing) { input.focus(); return; }
            try {
                await fetch(`/api/checkin/add?doing=${encodeURIComponent(doing)}&feeling=${feeling}`, {method:'POST'});
                input.value = '';
                loadCheckinData();
            } catch(e) { console.error('Checkin failed:', e); }
        }

        async function triggerCheckinPopup() {
            try {
                await fetch('/api/checkin/trigger', {method:'POST'});
            } catch(e) { console.error(e); }
        }

        async function generateSummaryNow() {
            try {
                const res = await fetch('/api/summary/generate', {method:'POST'});
                const data = await res.json();
                renderEveningSummary(data);
                loadCheckinData();
            } catch(e) { console.error(e); }
        }

        async function loadEveningSummary() {
            try {
                const res = await fetch('/api/summary/latest');
                const data = await res.json();
                renderEveningSummary(data);
            } catch(e) { console.error(e); }
        }

        function renderEveningSummary(data) {
            const box = document.getElementById('eveningSummaryBox');
            if (!data || data.message || !data.date) {
                box.innerHTML = '<span style="color:var(--text-muted);">暂无总结。点击「生成晚间总结」手动生成，或等待晚间自动生成。</span>';
                return;
            }
            const actual = (data.total_checkins||0) - (data.skipped_checkins||0);
            let html = `<div style="margin-bottom:10px;font-size:14px;color:var(--text-primary);font-weight:500;">📅 ${data.date} 回顾</div>`;
            html += `<div style="margin-bottom:8px;">签到 ${actual} 次 / 跳过 ${data.skipped_checkins||0} 次</div>`;

            // 高光
            if (data.highlights && data.highlights.length) {
                html += '<div style="margin:10px 0;">';
                data.highlights.forEach(h => {
                    html += `<div style="padding:4px 0;color:var(--text-primary);">${h}</div>`;
                });
                html += '</div>';
            }

            // 类别分布
            const cats = data.category_breakdown || {};
            if (Object.keys(cats).length) {
                html += '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">';
                for (const [cat, count] of Object.entries(cats)) {
                    const icon = CAT_ICONS[cat]||'📌';
                    html += `<span style="background:var(--bg-card-hover);padding:3px 8px;border-radius:6px;font-size:12px;">${icon} ${cat} ×${count}</span>`;
                }
                html += '</div>';
            }

            // 感受分布
            const feels = data.feeling_breakdown || {};
            if (Object.keys(feels).length) {
                html += '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">';
                for (const [f, count] of Object.entries(feels)) {
                    const icon = FEEL_ICONS[f]||'😐';
                    html += `<span style="background:var(--bg-card-hover);padding:3px 8px;border-radius:6px;font-size:12px;">${icon} ×${count}</span>`;
                }
                html += '</div>';
            }

            // 反思提示
            if (data.reflection_prompt) {
                html += `<div style="margin-top:12px;padding:10px 12px;background:var(--purple-dim);border-radius:var(--radius-sm);font-size:12px;color:var(--purple);line-height:1.7;">💭 ${data.reflection_prompt.replace(/\n/g,'<br>')}</div>`;
            }

            box.innerHTML = html;
        }

        // ==================== FLOATING CHAT WIDGET ====================
        var chatMode = 'ask_ai'; // 'memo' | 'ask_ai' | 'focus'
        var chatOpen = false;
        var focusRefreshTimer = null;

        function toggleChat() {
            chatOpen = !chatOpen;
            const panel = document.getElementById('chatPanel');
            const fab = document.getElementById('chatFab');
            if (chatOpen) {
                panel.classList.add('show');
                fab.classList.add('open');
                if (chatMode === 'ask_ai') {
                    document.getElementById('chatInput').focus();
                    loadChatHistory();
                } else if (chatMode === 'memo') {
                    document.getElementById('memoInput').focus();
                } else if (chatMode === 'focus') {
                    loadFocusPanelData();
                }
            } else {
                panel.classList.remove('show');
                fab.classList.remove('open');
                stopFocusRefresh();
            }
        }

        function setChatMode(mode) {
            chatMode = mode;
            document.querySelectorAll('.chat-mode-tab').forEach(t => t.classList.remove('active'));
            document.querySelector(`.chat-mode-tab[data-mode="${mode}"]`).classList.add('active');
            // Switch panels
            document.querySelectorAll('.chat-content-panel').forEach(p => p.classList.remove('active'));
            if (mode === 'memo') {
                document.getElementById('panelMemo').classList.add('active');
                document.getElementById('memoInput').focus();
                stopFocusRefresh();
            } else if (mode === 'ask_ai') {
                document.getElementById('panelAI').classList.add('active');
                document.getElementById('chatInput').focus();
                loadChatHistory();
                stopFocusRefresh();
            } else if (mode === 'focus') {
                document.getElementById('panelFocus').classList.add('active');
                loadFocusPanelData();
                startFocusRefresh();
            }
        }

        // ── 随手记 (Memo) ──
        async function sendMemo() {
            const input = document.getElementById('memoInput');
            const text = input.value.trim();
            if (!text) return;
            input.value = '';
            const now = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
            appendMemoMsg('user', text, now);
            try {
                const res = await fetch('/api/memo/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content: text})
                });
                const data = await res.json();
                if (data.success) {
                    appendMemoMsg('memo', '📝 已保存到长期记忆', now);
                } else {
                    appendMemoMsg('memo', '保存失败: ' + (data.error || '未知错误'), now);
                }
            } catch(e) {
                appendMemoMsg('memo', '保存失败，请检查网络', now);
            }
        }

        function appendMemoMsg(role, text, time) {
            const box = document.getElementById('memoMessages');
            const div = document.createElement('div');
            div.className = 'chat-msg ' + (role === 'user' ? 'user' : 'memo');
            div.innerHTML = text + '<span class="msg-time">' + (time || '') + '</span>';
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
        }

        function handleMemoKeydown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMemo();
            }
        }

        // ── 随手记语音录入 ──
        let memoRecorder = null, memoChunks = [], memoRecording = false;

        async function toggleMemoVoice() {
            if (memoRecording) { stopMemoVoice(); return; }
            // Check SenseVoice backend
            try {
                const sRes = await fetch('/api/speech/status');
                const sData = await sRes.json();
                if (!sData.available) {
                    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                        memoVoiceFallback(); return;
                    }
                    alert('语音识别不可用。请安装 SenseVoice: pip install funasr modelscope'); return;
                }
            } catch(e) {
                if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
                    memoVoiceFallback(); return;
                }
                alert('语音服务连接失败'); return;
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                memoChunks = [];
                memoRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                memoRecorder.ondataavailable = (e) => { if (e.data.size > 0) memoChunks.push(e.data); };
                memoRecorder.onstop = async () => {
                    stream.getTracks().forEach(t => t.stop());
                    const blob = new Blob(memoChunks, { type: 'audio/webm' });
                    const fd = new FormData();
                    fd.append('audio', blob, 'memo_voice.webm');
                    setMemoVoiceUI(false, '识别中...');
                    try {
                        const res = await fetch('/api/speech/transcribe', { method: 'POST', body: fd });
                        const result = await res.json();
                        if (result.success && result.text) {
                            // Auto-save recognized text as memo
                            const now = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
                            appendMemoMsg('user', '🎙 ' + result.text, now);
                            const saveRes = await fetch('/api/memo/save', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({content: '[语音] ' + result.text})
                            });
                            const saveData = await saveRes.json();
                            appendMemoMsg('memo', saveData.success ? '📝 语音已保存' : '保存失败', now);
                        } else {
                            alert('语音识别失败: ' + (result.error || '未识别到内容'));
                        }
                    } catch(err) { console.error('Memo voice error:', err); }
                    setMemoVoiceUI(false, '点击录音');
                };
                memoRecorder.start();
                setMemoVoiceUI(true, '录音中... 点击停止');
                setTimeout(() => { if (memoRecording) stopMemoVoice(); }, 8000);
            } catch(e) {
                alert('无法访问麦克风: ' + e.message);
            }
        }

        function stopMemoVoice() {
            if (memoRecorder && memoRecorder.state === 'recording') memoRecorder.stop();
            setMemoVoiceUI(false, '点击录音');
        }

        function setMemoVoiceUI(recording, hint) {
            memoRecording = recording;
            const btn = document.getElementById('memoVoiceBtn');
            const hintEl = document.getElementById('memoVoiceHint');
            if (recording) {
                btn.classList.add('recording');
                btn.textContent = '⏹';
            } else {
                btn.classList.remove('recording');
                btn.textContent = '🎙';
            }
            if (hint) hintEl.textContent = hint;
        }

        function memoVoiceFallback() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            const rec = new SR();
            rec.lang = 'zh-CN'; rec.continuous = false; rec.interimResults = false;
            rec.onresult = async (e) => {
                const text = e.results[0][0].transcript;
                setMemoVoiceUI(false, '点击录音');
                const now = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
                appendMemoMsg('user', '🎙 ' + text, now);
                try {
                    await fetch('/api/memo/save', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:'[语音] '+text}) });
                    appendMemoMsg('memo', '📝 语音已保存', now);
                } catch(err) { appendMemoMsg('memo', '保存失败', now); }
            };
            rec.onerror = () => { setMemoVoiceUI(false, '点击录音'); };
            rec.onend = () => { setMemoVoiceUI(false, '点击录音'); };
            rec.start();
            setMemoVoiceUI(true, '录音中...');
        }

        // ── 问 AI ──
        async function sendChatMessage() {
            const input = document.getElementById('chatInput');
            const text = input.value.trim();
            if (!text) return;
            const sendBtn = document.getElementById('chatSendBtn');
            sendBtn.disabled = true;
            input.value = '';
            const now = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
            appendChatMsg('user', text, now);
            appendChatMsg('ai', '思考中...', now, 'chatThinking');
            try {
                const res = await fetch('/api/chat/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: text})
                });
                const data = await res.json();
                const thinking = document.getElementById('chatThinking');
                if (thinking) thinking.remove();
                if (data.success) {
                    appendChatMsg('ai', data.response, now);
                } else {
                    appendChatMsg('ai', '出错了: ' + (data.error || '未知错误'), now);
                }
            } catch(e) {
                const thinking = document.getElementById('chatThinking');
                if (thinking) thinking.remove();
                appendChatMsg('ai', '网络错误，请重试', now);
            }
            sendBtn.disabled = false;
            input.focus();
        }

        function appendChatMsg(role, text, time, id) {
            const box = document.getElementById('chatMessages');
            const div = document.createElement('div');
            div.className = 'chat-msg ' + (role === 'user' ? 'user' : 'ai');
            if (id) div.id = id;
            div.innerHTML = text + '<span class="msg-time">' + (time || '') + '</span>';
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
        }

        async function loadChatHistory() {
            try {
                const res = await fetch('/api/chat/history');
                const data = await res.json();
                if (data.success && data.messages) {
                    const box = document.getElementById('chatMessages');
                    if (box.children.length <= 1) {
                        data.messages.slice(-10).forEach(m => {
                            const time = m.timestamp ? m.timestamp.split(' ')[1].substring(0,5) : '';
                            appendChatMsg(m.role === 'user' ? 'user' : 'ai', m.content, time);
                        });
                    }
                }
            } catch(e) {}
        }

        function handleChatKeydown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        }

        // ── 专注模式 (Focus Dashboard) ──
        async function loadFocusPanelData() {
            try {
                const [statusRes, workStartRes, todosRes] = await Promise.all([
                    fetch('/api/status'),
                    fetch('/api/work-start/today'),
                    fetch('/api/todos')
                ]);
                const status = await statusRes.json();
                const workStart = await workStartRes.json();
                const todos = await todosRes.json();

                // Work start time
                const wsEl = document.getElementById('focusWorkStart');
                if (workStart.start_time) {
                    wsEl.textContent = workStart.start_time.substring(0, 5);
                    wsEl.style.color = 'var(--green)';
                } else {
                    wsEl.textContent = '未开工';
                    wsEl.style.color = 'var(--text-muted)';
                }

                // Work rate (productive_ratio)
                const stats = status.today_stats || {};
                const rate = Math.round((stats.productive_ratio || 0) * 100);
                const rateEl = document.getElementById('focusWorkRate');
                rateEl.textContent = rate + '%';
                rateEl.style.color = rate >= 60 ? 'var(--green)' : rate >= 30 ? 'var(--amber)' : 'var(--red)';
                const fill = document.getElementById('focusRateFill');
                fill.style.width = rate + '%';
                fill.style.background = rate >= 60 ? 'var(--green)' : rate >= 30 ? 'var(--amber)' : 'var(--red)';

                // Todos
                const todoList = todos.todos || [];
                const todoStats = todos.stats || {};
                const statsEl = document.getElementById('focusTodoStats');
                statsEl.textContent = `(${todoStats.completed || 0}/${todoStats.total || 0})`;

                const listEl = document.getElementById('focusTodoList');
                if (todoList.length === 0) {
                    listEl.innerHTML = '<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:8px;">暂无待办</div>';
                } else {
                    listEl.innerHTML = todoList.slice(0, 8).map(t => {
                        const done = t.done || t.completed;
                        const icon = done ? '✅' : (t.priority === 'high' ? '🔴' : t.priority === 'low' ? '🔵' : '⚪');
                        const style = done ? 'text-decoration:line-through;color:var(--text-muted);' : '';
                        return `<div class="focus-todo-item">
                            <span class="focus-todo-icon">${icon}</span>
                            <span class="focus-todo-text" style="${style}">${t.title || t.text || ''}</span>
                        </div>`;
                    }).join('');
                }

            } catch(e) {
                console.error('Focus panel data error:', e);
            }
        }

        function startFocusRefresh() {
            stopFocusRefresh();
            focusRefreshTimer = setInterval(loadFocusPanelData, 30000); // refresh every 30s
        }
        function stopFocusRefresh() {
            if (focusRefreshTimer) { clearInterval(focusRefreshTimer); focusRefreshTimer = null; }
        }
        // ==================== 开机自启 ====================
        async function loadAutoStartStatus() {
            try {
                const res = await fetch('/api/settings/autostart');
                const data = await res.json();
                const toggle = document.getElementById('autoStartToggle');
                const statusEl = document.getElementById('autoStartStatus');
                if (toggle) toggle.checked = !!data.enabled;
                if (statusEl) {
                    const platform = data.platform || '';
                    const hint = platform === 'Darwin' ? '（macOS LaunchAgent）'
                               : platform === 'Windows' ? '（Windows 启动项）'
                               : platform === 'Linux' ? '（systemd 用户服务）' : '';
                    statusEl.textContent = data.enabled
                        ? `已启用 ${hint} — 下次登录后将自动在后台启动`
                        : `未启用 — 开启后登录系统时自动在后台运行 ${hint}`;
                }
            } catch(e) {
                console.error('loadAutoStartStatus failed:', e);
            }
        }

        async function toggleAutoStart(enabled) {
            const statusEl = document.getElementById('autoStartStatus');
            if (statusEl) statusEl.textContent = '正在' + (enabled ? '启用' : '禁用') + '…';
            try {
                const res = await fetch('/api/settings/autostart', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({enabled})
                });
                const data = await res.json();
                if (statusEl) statusEl.textContent = data.message || (data.success ? '操作成功' : '操作失败');
                // 重新拉取最新状态同步 toggle
                setTimeout(loadAutoStartStatus, 500);
            } catch(e) {
                if (statusEl) statusEl.textContent = '请求失败，请重试';
                console.error('toggleAutoStart failed:', e);
            }
        }

        // ==================== API SETTINGS ====================
        async function loadAPIProviders() {
            try {
                const res = await fetch('/api/settings/providers');
                const data = await res.json();
                renderAPIProviders(data.providers || []);
            } catch(e) { console.error('Load providers failed:', e); }
        }

        function renderAPIProviders(providers) {
            const box = document.getElementById('apiProvidersList');
            if (!box) return;

            // Short abbreviation icons for each provider
            const providerIcons = {
                modelscope: 'MS', dashscope: 'DS', deepseek: 'DK',
                openai: 'OAI', anthropic: 'CL', default: '??'
            };

            box.className = 'api-providers-grid';
            box.innerHTML = providers.map(p => {
                const isActive = p.is_active;
                const hasKey  = p.api_key_set;
                const icon    = providerIcons[p.provider] || p.provider.substring(0, 2).toUpperCase();
                const suggested = p.model_suggestions || {text: [], vision: []};
                const textOpts   = (suggested.text   || []).map(m => `<option value="${m}">`).join('');
                const visionOpts = (suggested.vision || []).map(m => `<option value="${m}">`).join('');
                const subText = p.text_model
                    ? p.text_model
                    : (hasKey ? '已配置 · 未选择模型' : '点击展开配置');
                const badgeCls = isActive ? 'badge-green' : hasKey ? 'badge-blue' : 'badge-amber';
                const badgeTxt = isActive ? '✓ 使用中' : hasKey ? '已配置' : '未配置';

                return `<div class="api-provider-card ${isActive ? 'active expanded' : ''}" id="provider-${p.provider}">
                    <div class="api-provider-header" onclick="toggleProviderCard('${p.provider}')">
                        <div class="api-provider-icon">${icon}</div>
                        <div class="api-provider-info">
                            <div class="api-provider-name">${p.display_name || p.provider}</div>
                            <div class="api-provider-sub">${subText}</div>
                        </div>
                        <span class="api-provider-badge badge ${badgeCls}">${badgeTxt}</span>
                        <span class="api-provider-chevron">▼</span>
                    </div>
                    <div class="api-provider-body">
                        <div class="api-model-grid">
                            <div class="api-model-field">
                                <label><span class="lbl-icon">💬</span>文本模型</label>
                                <input class="api-model-input" list="text-models-${p.provider}"
                                    id="textmodel-${p.provider}" value="${p.text_model || ''}"
                                    placeholder="输入或选择模型名称">
                                <datalist id="text-models-${p.provider}">${textOpts}</datalist>
                            </div>
                            <div class="api-model-field">
                                <label><span class="lbl-icon">🖼️</span>视觉模型</label>
                                <input class="api-model-input" list="vision-models-${p.provider}"
                                    id="visionmodel-${p.provider}" value="${p.vision_model || ''}"
                                    placeholder="无则留空">
                                <datalist id="vision-models-${p.provider}">${visionOpts}</datalist>
                            </div>
                        </div>
                        <div class="api-key-section">
                            <div class="api-field-label">🔑 API Key</div>
                            <div class="api-key-wrapper">
                                <input type="password" class="api-key-input" id="apikey-${p.provider}"
                                    placeholder="${hasKey ? '••••••••  （已设置，输入新值覆盖）' : '输入 API Key...'}"
                                    autocomplete="off">
                                <button class="api-key-toggle" onclick="toggleKeyVisibility('${p.provider}')" title="显示/隐藏 Key">👁</button>
                            </div>
                        </div>
                        <div class="api-actions">
                            <button class="api-btn" onclick="saveProviderConfig('${p.provider}')">💾 保存模型</button>
                            <button class="api-btn primary" id="testBtn-${p.provider}" onclick="testAPIKey('${p.provider}')">⚡ 测试连通</button>
                            ${!isActive && hasKey ? `<button class="api-btn success" onclick="activateProvider('${p.provider}')">✓ 激活使用</button>` : ''}
                        </div>
                        <div class="api-test-result" id="testResult-${p.provider}"></div>
                    </div>
                </div>`;
            }).join('');
        }

        function toggleProviderCard(provider) {
            const card = document.getElementById('provider-' + provider);
            if (card) card.classList.toggle('expanded');
        }

        function toggleKeyVisibility(provider) {
            const input = document.getElementById('apikey-' + provider);
            if (input) input.type = input.type === 'password' ? 'text' : 'password';
        }

        async function saveProviderConfig(provider) {
            const textModel = (document.getElementById('textmodel-' + provider)?.value || '').trim();
            const visionModel = (document.getElementById('visionmodel-' + provider)?.value || '').trim();
            const resultEl = document.getElementById('testResult-' + provider);
            resultEl.className = 'api-test-result';
            resultEl.style.display = 'none';

            if (!textModel) {
                resultEl.className = 'api-test-result fail';
                resultEl.textContent = '❌ 文本模型不能为空';
                return false;
            }

            try {
                const res = await fetch(`/api/settings/providers/${provider}/config`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text_model: textModel, vision_model: visionModel})
                });
                const data = await res.json();
                if (!data.success) {
                    resultEl.className = 'api-test-result fail';
                    resultEl.textContent = '❌ ' + (data.error || '模型保存失败');
                    return false;
                }
                resultEl.className = 'api-test-result success';
                resultEl.textContent = '✅ ' + (data.message || '模型配置已保存');
                return true;
            } catch(e) {
                resultEl.className = 'api-test-result fail';
                resultEl.textContent = '❌ 保存失败: ' + e.message;
                return false;
            }
        }

        async function testAPIKey(provider) {
            const input = document.getElementById('apikey-' + provider);
            const apiKey = input.value.trim();
            const resultEl = document.getElementById('testResult-' + provider);
            const btn = document.getElementById('testBtn-' + provider);

            btn.classList.add('testing');
            btn.innerHTML = '<span class="api-spinner"></span> 测试中...';
            resultEl.className = 'api-test-result';
            resultEl.style.display = 'none';

            try {
                const modelSaved = await saveProviderConfig(provider);
                if (!modelSaved) {
                    throw new Error('请先修正模型配置后再测试');
                }

                // If new key entered, save it first and give explicit feedback
                if (apiKey) {
                    const saveRes = await fetch(`/api/settings/providers/${provider}/key`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({api_key: apiKey})
                    });
                    const saveData = await saveRes.json();
                    if (!saveData.success) {
                        resultEl.className = 'api-test-result fail';
                        throw new Error('保存 API Key 失败：' + (saveData.error || '未知错误'));

                    }
                    resultEl.className = 'api-test-result success';
                    resultEl.style.display = 'block';
                    resultEl.textContent = '✅ ' + (saveData.message || 'API Key 已保存，正在测试连通性...');
                }

                const res = await fetch(`/api/settings/providers/${provider}/test`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({api_key: apiKey || null})
                });
                const data = await res.json();

                if (data.success) {
                    resultEl.className = 'api-test-result success';
                    resultEl.textContent = '✅ ' + data.message + ' (' + data.latency_ms + 'ms)';
                } else {
                    resultEl.className = 'api-test-result fail';
                    resultEl.textContent = '❌ ' + data.message;
                }
            } catch(e) {
                resultEl.className = 'api-test-result fail';
                resultEl.textContent = '❌ 操作失败: ' + e.message;
            }

            btn.classList.remove('testing');
            btn.innerHTML = '⚡ 测试连通';
            // Refresh provider list
            setTimeout(loadAPIProviders, 500);
        }

        async function activateProvider(provider) {
            try {
                const res = await fetch('/api/settings/providers/active', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({provider: provider})
                });
                const data = await res.json();
                if (data.success) {
                    loadAPIProviders();
                } else {
                    alert(data.error || '激活失败');
                }
            } catch(e) { alert('网络错误'); }
        }

        // ==================== CHAT LOG VIEWER ====================
        var _chatlogDates = [];

        async function loadChatLogDates() {
            try {
                const res = await fetch('/api/chatlog/list');
                const data = await res.json();
                _chatlogDates = data.dates || [];
                renderChatLogDateSelect(_chatlogDates);
                renderChatLogCalendar(_chatlogDates);
                // Stats
                document.getElementById('chatlogTotalDays').textContent = _chatlogDates.length;
                if (_chatlogDates.length) {
                    document.getElementById('chatlogEarliestDate').textContent = _chatlogDates[_chatlogDates.length - 1];
                    document.getElementById('chatlogLatestDate').textContent = _chatlogDates[0];
                } else {
                    document.getElementById('chatlogEarliestDate').textContent = '--';
                    document.getElementById('chatlogLatestDate').textContent = '--';
                }
                // Auto-select today or latest
                const sel = document.getElementById('chatlogDateSelect');
                const today = new Date().toISOString().substring(0, 10);
                if (_chatlogDates.includes(today)) {
                    sel.value = today;
                    loadChatLogContent();
                } else if (_chatlogDates.length) {
                    sel.value = _chatlogDates[0];
                    loadChatLogContent();
                }
            } catch(e) {
                console.error('Load chat log dates failed:', e);
            }
        }

        function refreshChatLogDates() { loadChatLogDates(); }

        function renderChatLogDateSelect(dates) {
            const sel = document.getElementById('chatlogDateSelect');
            sel.innerHTML = '<option value="">选择日期...</option>' +
                dates.map(d => `<option value="${d}">${d}</option>`).join('');
        }

        function renderChatLogCalendar(dates) {
            const box = document.getElementById('chatlogCalendar');
            if (!dates.length) {
                box.innerHTML = '<div style="color:var(--text-muted);padding:8px 0;">暂无对话记录</div>';
                return;
            }
            const dateSet = new Set(dates);
            // Show last 30 days as a grid
            const today = new Date();
            let html = '<div style="display:flex;gap:3px;flex-wrap:wrap;">';
            for (let i = 29; i >= 0; i--) {
                const d = new Date(today);
                d.setDate(d.getDate() - i);
                const ds = d.toISOString().substring(0, 10);
                const hasLog = dateSet.has(ds);
                const isToday = i === 0;
                const weekday = ['日','一','二','三','四','五','六'][d.getDay()];
                html += `<div title="${ds} 周${weekday}${hasLog ? '\n有对话记录' : ''}"
                    style="width:22px;height:22px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;cursor:${hasLog?'pointer':'default'};
                    background:${hasLog ? 'var(--green)' : 'var(--bg-card-hover, rgba(128,128,128,0.1))'};
                    color:${hasLog ? '#fff' : 'var(--text-muted)'};
                    opacity:${isToday ? '1' : '0.8'};
                    ${isToday ? 'box-shadow:0 0 0 2px var(--blue);' : ''}"
                    ${hasLog ? `onclick="document.getElementById('chatlogDateSelect').value='${ds}';loadChatLogContent()"` : ''}>${d.getDate()}</div>`;
            }
            html += '</div>';
            html += '<div style="display:flex;gap:10px;margin-top:8px;font-size:11px;color:var(--text-muted);">';
            html += '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--green);"></span> 有记录</span>';
            html += '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--bg-card-hover, rgba(128,128,128,0.1));"></span> 无记录</span>';
            html += '</div>';
            box.innerHTML = html;
        }

        async function loadChatLogContent() {
            const sel = document.getElementById('chatlogDateSelect');
            const date = sel.value;
            const box = document.getElementById('chatlogContent');
            if (!date) {
                box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">选择日期查看对话记录</div>';
                return;
            }
            box.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">加载中...</div>';
            try {
                const res = await fetch(`/api/chatlog/read/${date}`);
                const data = await res.json();
                if (data.success && data.content) {
                    renderChatLogMarkdown(data.content);
                } else {
                    box.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-muted);">${data.error || '该日期暂无对话记录'}</div>`;
                }
            } catch(e) {
                box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--red);">加载失败</div>';
            }
        }

        function renderChatLogMarkdown(md) {
            const box = document.getElementById('chatlogContent');
            // Simple markdown rendering
            let html = md
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/^# (.+)$/gm, '<h2 style="font-size:18px;font-weight:700;color:var(--text-primary);margin:16px 0 8px;">$1</h2>')
                .replace(/^## (.+)$/gm, '<h3 style="font-size:15px;font-weight:600;color:var(--text-primary);margin:14px 0 6px;">$1</h3>')
                .replace(/^### (.+)$/gm, '<h4 style="font-size:13px;font-weight:600;color:var(--blue);margin:12px 0 4px;">$1</h4>')
                .replace(/^\&gt; (.+)$/gm, '<div style="border-left:3px solid var(--blue);padding:4px 12px;margin:4px 0;background:var(--bg-card-hover, rgba(128,128,128,0.05));font-size:12px;color:var(--text-secondary);">$1</div>')
                .replace(/\*\*(.+?)\*\*/g, '<b style="color:var(--text-primary);">$1</b>')
                .replace(/\*(.+?)\*/g, '<em style="color:var(--text-muted);">$1</em>')
                .replace(/^- \[ \] (.+)$/gm, '<div style="padding:2px 0;">☐ $1</div>')
                .replace(/^- \[x\] (.+)$/gm, '<div style="padding:2px 0;color:var(--green);">☑ $1</div>')
                .replace(/^- (.+)$/gm, '<div style="padding:2px 0 2px 8px;">· $1</div>')
                .replace(/^\|(.+)\|$/gm, function(match) {
                    const cells = match.split('|').filter(c => c.trim());
                    if (cells.every(c => /^[\s-]+$/.test(c))) return ''; // skip separator
                    return '<div style="display:flex;gap:8px;padding:3px 0;border-bottom:1px solid var(--border);font-size:12px;">' +
                        cells.map(c => `<span style="flex:1;">${c.trim()}</span>`).join('') + '</div>';
                })
                .replace(/^---$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:16px 0;">')
                .replace(/\n\n/g, '<br>')
                .replace(/\n/g, '\n');
            box.innerHTML = html;
        }

        async function exportTodayChatLog() {
            try {
                const res = await fetch('/api/chat/export', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    alert('已导出: ' + data.message);
                    loadChatLogDates(); // refresh
                } else {
                    alert('导出失败: ' + (data.error || '未知错误'));
                }
            } catch(e) {
                alert('导出失败');
            }
        }

        // ==================== PLUGIN MANAGEMENT ====================

        const PLUGIN_TYPE_LABELS = {
            'general': '通用',
            'analyzer': '分析',
            'nudge': '提醒',
            'reporter': '报告',
            'exporter': '导出',
            'provider': 'LLM',
        };

        const PLUGIN_TYPE_COLORS = {
            'general': 'var(--text-muted)',
            'analyzer': 'var(--blue)',
            'nudge': 'var(--amber)',
            'reporter': 'var(--green)',
            'exporter': 'var(--purple, #a78bfa)',
            'provider': 'var(--red, #f87171)',
        };

        async function loadPlugins() {
            try {
                const res = await fetch('/api/plugins');
                const data = await res.json();
                renderPluginList(data.plugins || []);
            } catch (e) {
                document.getElementById('pluginsList').innerHTML =
                    '<div class="card" style="text-align:center;color:var(--text-muted);padding:40px;">无法加载插件列表</div>';
            }
        }

        function renderPluginList(plugins) {
            const container = document.getElementById('pluginsList');
            if (!plugins.length) {
                container.innerHTML = `
                    <div class="card" style="text-align:center;padding:40px;">
                        <div style="font-size:32px;margin-bottom:12px;">🧩</div>
                        <div style="color:var(--text-secondary);margin-bottom:8px;">暂无插件</div>
                        <div style="font-size:12px;color:var(--text-muted);line-height:1.8;">
                            将插件放入 <code>plugins/</code> 目录即可自动发现<br>
                            复制 <code>plugins/_template/</code> 快速创建新插件
                        </div>
                    </div>`;
                return;
            }

            container.innerHTML = plugins.map(p => {
                const typeLabel = PLUGIN_TYPE_LABELS[p.plugin_type] || p.plugin_type;
                const typeColor = PLUGIN_TYPE_COLORS[p.plugin_type] || 'var(--text-muted)';
                const statusDot = p.active
                    ? '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px;"></span>'
                    : '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text-muted);opacity:0.4;margin-right:6px;"></span>';
                const toggleBtn = p.active
                    ? `<button class="btn btn-sm" style="color:var(--text-muted);" onclick="togglePlugin('${p.name}', false)">停用</button>`
                    : `<button class="btn btn-sm btn-green" onclick="togglePlugin('${p.name}', true)">启用</button>`;
                const errorBadge = p.error
                    ? `<div style="margin-top:8px;padding:6px 10px;background:rgba(239,68,68,0.1);border-radius:6px;font-size:11px;color:var(--red, #f87171);">⚠️ ${p.error}</div>`
                    : '';
                const tags = (p.tags || []).map(t =>
                    `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-secondary);color:var(--text-muted);">${t}</span>`
                ).join(' ');

                const configFields = (p.config_schema || []).map(field => {
                    const val = (p.config && p.config[field.key]) || '';
                    if (field.type === 'boolean') {
                        const checked = val ? 'checked' : '';
                        return `<div class="setting-group" style="margin-top:6px;">
                            <label class="setting-label" style="font-size:11px;">${field.label}</label>
                            <label class="switch"><input type="checkbox" ${checked} data-plugin="${p.name}" data-key="${field.key}" onchange="onPluginConfigChange(this)"><span class="slider"></span></label>
                        </div>`;
                    } else if (field.type === 'select') {
                        const opts = (field.options || []).map(o =>
                            `<option value="${o}" ${o === val ? 'selected' : ''}>${o}</option>`
                        ).join('');
                        return `<div class="setting-group" style="margin-top:6px;">
                            <label class="setting-label" style="font-size:11px;">${field.label}</label>
                            <select class="setting-input" style="font-size:11px;" data-plugin="${p.name}" data-key="${field.key}" onchange="onPluginConfigChange(this)">${opts}</select>
                        </div>`;
                    } else {
                        return `<div class="setting-group" style="margin-top:6px;">
                            <label class="setting-label" style="font-size:11px;">${field.label}</label>
                            <input type="text" class="setting-input" style="font-size:11px;" value="${val}" data-plugin="${p.name}" data-key="${field.key}" onchange="onPluginConfigChange(this)" placeholder="${field.key}">
                        </div>`;
                    }
                }).join('');

                const configSection = configFields ? `
                    <details style="margin-top:10px;">
                        <summary style="font-size:11px;color:var(--text-muted);cursor:pointer;user-select:none;">配置</summary>
                        <div style="margin-top:6px;">${configFields}</div>
                    </details>` : '';

                return `
                    <div class="card" style="padding:14px 16px;">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                            <div style="flex:1;">
                                <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                                    ${statusDot}
                                    <span style="font-weight:600;font-size:14px;color:var(--text-primary);">${p.display_name}</span>
                                    <span style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid ${typeColor};color:${typeColor};">${typeLabel}</span>
                                    <span style="font-size:11px;color:var(--text-muted);">v${p.version}</span>
                                </div>
                                <div style="font-size:12px;color:var(--text-secondary);margin-left:14px;">${p.description}</div>
                                ${p.author ? `<div style="font-size:11px;color:var(--text-muted);margin-left:14px;margin-top:2px;">by ${p.author}</div>` : ''}
                                ${tags ? `<div style="margin-left:14px;margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;">${tags}</div>` : ''}
                                ${errorBadge}
                                ${configSection}
                            </div>
                            <div style="margin-left:12px;flex-shrink:0;">
                                ${toggleBtn}
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        async function togglePlugin(name, activate) {
            const action = activate ? 'activate' : 'deactivate';
            try {
                const res = await fetch(`/api/plugins/${name}/${action}`, { method: 'POST' });
                const data = await res.json();
                if (!data.success) {
                    alert(data.error || `${action} 失败`);
                }
                loadPlugins();
            } catch (e) {
                alert('网络错误');
            }
        }

        async function onPluginConfigChange(el) {
            const pluginName = el.dataset.plugin;
            const key = el.dataset.key;
            let value;
            if (el.type === 'checkbox') {
                value = el.checked;
            } else {
                value = el.value;
            }
            try {
                await fetch(`/api/plugins/${pluginName}/config`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config: { [key]: value } }),
                });
            } catch (e) {
                console.warn('保存插件配置失败:', e);
            }
        }

        async function discoverPlugins() {
            try {
                await fetch('/api/plugins/discover', { method: 'POST' });
                loadPlugins();
            } catch (e) {
                alert('扫描失败');
            }
        }

        async function loadEventHistory() {
            try {
                const res = await fetch('/api/plugins/events');
                const data = await res.json();
                const container = document.getElementById('eventHistoryList');
                const history = data.history || [];
                const listeners = data.listeners || [];

                if (!history.length) {
                    container.innerHTML = '<div style="color:var(--text-muted);">暂无事件记录</div>';
                    return;
                }

                let html = history.reverse().map(e =>
                    `<div style="padding:3px 0;border-bottom:1px solid var(--border-color);">
                        <span style="color:var(--text-muted);">${e.timestamp}</span>
                        <span style="color:var(--blue);font-weight:600;">${e.event}</span>
                        <span style="color:var(--text-muted);">[${(e.data_keys||[]).join(', ')}]</span>
                    </div>`
                ).join('');

                if (listeners.length) {
                    html += `<div style="margin-top:12px;padding-top:8px;border-top:2px solid var(--border-color);">
                        <div style="font-weight:600;margin-bottom:4px;color:var(--text-secondary);">已注册监听器 (${listeners.length})</div>
                        ${listeners.map(l =>
                            `<div style="padding:2px 0;"><span style="color:var(--amber);">${l.event}</span> ← <span style="color:var(--green);">${l.source || 'anonymous'}</span> <span style="color:var(--text-muted);">(priority: ${l.priority})</span></div>`
                        ).join('')}
                    </div>`;
                }

                container.innerHTML = html;
            } catch (e) {
                document.getElementById('eventHistoryList').innerHTML =
                    '<div style="color:var(--red, #f87171);">加载失败</div>';
            }
        }
