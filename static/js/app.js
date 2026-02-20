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
            if (t) applyTheme(t);
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
        function switchTab(name, el) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + name).classList.add('active');
            if (el) el.classList.add('active');
            if (name === 'dashboard') { ensureChartsInit(); loadData(); loadWorkStartData(); }
            if (name === 'todo') loadTodos();
            if (name === 'pomodoro') { loadPomoStatus(); loadPomoFocusOptions(); }
            if (name === 'checkin') { loadCheckinData(); loadEveningSummary(); }
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
            loadPomoStatus();
            connectWebSocket();
            setInterval(() => { if (chartsInitialized) loadData(); }, 30000);
            setInterval(loadPomoStatus, 1000);
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
                if(dlDate){
                    const timeTag = dlTime ? ` ${dlTime}` : '';
                    if(t.is_overdue) deadlineStr = `<span class="overdue">已逾期${timeTag ? ' ('+dlTime+')' : ''}</span>`;
                    else if(t.days_until_deadline===0) deadlineStr = `<span style="color:var(--amber)">今天${timeTag}到期</span>`;
                    else deadlineStr = `${dlDate}${timeTag} (${t.days_until_deadline}天后)`;
                }
                const pi = priIcons[t.priority]||'';
                const priHtml = (t.priority&&t.priority!=='normal') ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--${priColors[t.priority]||'blue'}-dim,rgba(100,100,100,0.1));color:var(--${priColors[t.priority]||'blue'});">${priLabels[t.priority]}</span>` : '';
                const tagsHtml = (t.tags&&t.tags.length) ? t.tags.map(tg=>`<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--green-dim,rgba(16,185,129,0.1));color:var(--green);">${tg}</span>`).join(' ') : '';
                return `<div class="todo-item">
                    <div class="todo-check ${t.completed?'done':''}" onclick="toggleTodo('${t.id}')">${t.completed?'✓':''}</div>
                    <div class="todo-body">
                        <div class="todo-title ${t.completed?'done':''}">${pi} ${t.title}</div>
                        <div class="todo-meta">${deadlineStr?`<span>📅 ${deadlineStr}</span>`:''}${priHtml?' '+priHtml:''}${tagsHtml?' '+tagsHtml:''}</div>
                    </div>
                    <button class="todo-del" onclick="deleteTodo('${t.id}')">✕</button>
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

        function toggleChat() {
            chatOpen = !chatOpen;
            const panel = document.getElementById('chatPanel');
            const fab = document.getElementById('chatFab');
            if (chatOpen) {
                panel.classList.add('show');
                fab.classList.add('open');
                document.getElementById('chatInput').focus();
                loadChatHistory();
            } else {
                panel.classList.remove('show');
                fab.classList.remove('open');
            }
        }

        function setChatMode(mode) {
            chatMode = mode;
            document.querySelectorAll('.chat-mode-tab').forEach(t => t.classList.remove('active'));
            document.querySelector(`.chat-mode-tab[data-mode="${mode}"]`).classList.add('active');
            const input = document.getElementById('chatInput');
            const sendBtn = document.getElementById('chatSendBtn');
            if (mode === 'memo') {
                input.placeholder = '随手记下你的想法...';
                sendBtn.textContent = '📝';
            } else if (mode === 'ask_ai') {
                input.placeholder = '问 AI 任何问题...';
                sendBtn.textContent = '➤';
            } else if (mode === 'focus') {
                input.placeholder = '快速记录，不打断专注...';
                sendBtn.textContent = '📌';
            }
        }

        async function sendChatMessage() {
            const input = document.getElementById('chatInput');
            const text = input.value.trim();
            if (!text) return;

            const sendBtn = document.getElementById('chatSendBtn');
            sendBtn.disabled = true;
            input.value = '';

            const now = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});

            if (chatMode === 'memo') {
                // 随手记模式 — 直接存为 Markdown，不调用 AI
                appendChatMsg('user', text, now);
                try {
                    const res = await fetch('/api/memo/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({content: text})
                    });
                    const data = await res.json();
                    if (data.success) {
                        appendChatMsg('memo', '📝 已保存到长期记忆 (' + data.filename + ')', now);
                    } else {
                        appendChatMsg('ai', '保存失败: ' + (data.error || '未知错误'), now);
                    }
                } catch(e) {
                    appendChatMsg('ai', '保存失败，请检查网络', now);
                }
            } else if (chatMode === 'focus') {
                // 专注模式 — 快速记录，简洁确认
                appendChatMsg('user', text, now);
                try {
                    const res = await fetch('/api/memo/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({content: text})
                    });
                    const data = await res.json();
                    appendChatMsg('memo', '📌 已记录，继续专注！', now);
                } catch(e) {
                    appendChatMsg('memo', '📌 已记录', now);
                }
            } else {
                // 问 AI 模式
                appendChatMsg('user', text, now);
                appendChatMsg('ai', '思考中...', now, 'chatThinking');
                try {
                    const res = await fetch('/api/chat/send', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({text: text})
                    });
                    const data = await res.json();
                    // Remove thinking indicator
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
            }

            sendBtn.disabled = false;
            input.focus();
        }

        function appendChatMsg(role, text, time, id) {
            const box = document.getElementById('chatMessages');
            const div = document.createElement('div');
            div.className = 'chat-msg ' + (role === 'user' ? 'user' : role === 'memo' ? 'memo' : 'ai');
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
                    if (box.children.length <= 1) {  // Only load if empty or just welcome
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
            box.innerHTML = providers.map(p => {
                const isActive = p.is_active;
                const hasKey = p.api_key_set;
                return `<div class="api-provider-card ${isActive ? 'active' : ''}" id="provider-${p.provider}">
                    <div class="api-provider-header">
                        <span class="api-provider-name">${p.display_name || p.provider}</span>
                        <span class="api-provider-badge badge ${isActive ? 'badge-green' : hasKey ? 'badge-blue' : 'badge-amber'}">${isActive ? '当前使用' : hasKey ? '已配置' : '未配置'}</span>
                    </div>
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;">
                        模型: ${p.text_model} ${p.vision_model ? '| 视觉: ' + p.vision_model : ''}
                    </div>
                    <div class="api-key-row">
                        <input type="password" class="api-key-input" id="apikey-${p.provider}"
                            placeholder="${hasKey ? '••••••••（已配置，输入新值覆盖）' : '输入 API Key...'}"
                            autocomplete="off">
                        <button class="api-test-btn" onclick="testAPIKey('${p.provider}')">测试</button>
                        ${!isActive && hasKey ? `<button class="api-activate-btn" onclick="activateProvider('${p.provider}')">激活</button>` : ''}
                    </div>
                    <div class="api-test-result" id="testResult-${p.provider}"></div>
                </div>`;
            }).join('');
        }

        async function testAPIKey(provider) {
            const input = document.getElementById('apikey-' + provider);
            const apiKey = input.value.trim();
            const resultEl = document.getElementById('testResult-' + provider);
            const btn = input.parentElement.querySelector('.api-test-btn');

            btn.classList.add('testing');
            btn.textContent = '测试中...';
            resultEl.className = 'api-test-result';
            resultEl.style.display = 'none';

            try {
                // If new key entered, save it first
                if (apiKey) {
                    await fetch(`/api/settings/providers/${provider}/key`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({api_key: apiKey})
                    });
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
                resultEl.textContent = '❌ 网络错误: ' + e.message;
            }

            btn.classList.remove('testing');
            btn.textContent = '测试';
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
