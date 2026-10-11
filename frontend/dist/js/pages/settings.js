/* 设置页：面板 / Java / 下载源 / RCON / 主题 / 高级（缓存、日志占用、统一应用日志） */
window.Pages = window.Pages || {};

Pages.settings = function (app) {
  app.innerHTML = C.shell({
    active: 'settings',
    crumbs: [{ text: '面板', hash: '#/settings' }, { text: '设置' }]
  });
  C.bindShell(app);
  var body = document.getElementById('page-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>读取面板配置…</div>';

  var TAB = 'general';
  var cfg = null, javaInfo = null, probe = null, appLog = null;

  function loadAll() {
    return Promise.all([
      API.get('/api/settings'),
      API.get('/api/java').catch(function () { return { detected: [], installed: [] }; }),
      API.get('/api/logs/app?limit=200').catch(function () { return { lines: [], files: [] }; })
    ]).then(function (r) {
      cfg = r[0]; javaInfo = r[1]; appLog = r[2];
      draw();
    }).catch(function (e) {
      /* 403 = 角色没有 `system.settings`（规格 §2：只有超管能看面板设置）
         → 「权限不足」页；其余错误按类型给人话 + 重试（不再是一句"读取配置失败"）。 */
      if (Perm.denyIfForbidden(e, app, { perm: 'system.settings', active: 'settings',
                                         crumbs: [{ text: '面板' }, { text: '设置' }] })) return;
      View.showError(body, e, { title: '读取面板配置失败', showStack: true },
                     { onRetry: function () { loadAll(); } });
    });
  }

  function draw() {
    var c = cfg.config;
    body.innerHTML = '' +
      '<div class="page-head"><div class="titles"><div class="kicker">Panel Settings</div>' +
        '<h1>设置</h1><div class="sub">面板端口 / 数据目录 / Java 运行时 / 下载源 / RCON 默认值 / 主题</div></div>' +
        '<div class="acts"><span class="badge gold">v' + U.esc(cfg.version) + '</span></div></div>' +
      '<div class="tabs">' + [
        ['general', '常规'], ['java', 'Java 运行时'], ['sources', '下载源'],
        ['rcon', 'RCON'], ['theme', '主题与观感'], ['advanced', '高级与日志'], ['password', '修改口令']
      ].map(function (t) {
        return '<div class="tab' + (TAB === t[0] ? ' active' : '') + '" data-t="' + t[0] + '">' + U.esc(t[1]) + '</div>';
      }).join('') + '</div><div id="set-body"></div>';
    body.querySelectorAll('[data-t]').forEach(function (el) {
      el.addEventListener('click', function () { TAB = el.getAttribute('data-t'); draw(); });
    });
    (TABS[TAB] || TABS.general)();
  }

  function field(label, id, value, desc, type) {
    return '<div class="field"><label for="' + id + '">' + U.esc(label) + '</label>' +
      '<input type="' + (type || 'text') + '" id="' + id + '" value="' + U.esc(value === null || value === undefined ? '' : value) + '" />' +
      (desc ? '<div class="desc">' + U.esc(desc) + '</div>' : '') + '</div>';
  }

  var TABS = {};

  TABS.general = function () {
    var c = cfg.config;
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-head"><h3>面板</h3>' +
          '<div class="right"><button class="btn sm primary" id="g-save">保存</button></div></div>' +
          field('面板名称', 'g-name', c.site_name) +
          '<div class="grid cols-2 tight">' +
            field('监听端口', 'g-port', c.port, '修改后需重启面板才生效', 'number') +
            field('绑定地址', 'g-host', c.bind_host, '127.0.0.1 仅本机；0.0.0.0 全网卡（注意安全）') +
          '</div>' +
          '<div class="grid cols-3 tight">' +
            field('会话时长（小时）', 'g-sess', c.session_hours, '', 'number') +
            field('登录失败上限', 'g-fails', c.max_login_fails, '', 'number') +
            field('锁定时长（分钟）', 'g-lock', c.lock_minutes, '', 'number') +
          '</div>' +
          '<div class="desc">数据目录由环境变量 MC_DATA_DIR 控制，当前：<span class="mono">' + U.esc(cfg.paths.data) + '</span></div>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>实例与监控默认值</h3>' +
          '<div class="right"><button class="btn sm primary" id="g-save2">保存</button></div></div>' +
          '<div class="grid cols-2 tight">' +
            field('默认内存（MB）', 'g-mem', c.default_memory_mb, '', 'number') +
            field('监控采样（秒）', 'g-sample', c.sample_interval, '越大越省资源，1h/24h 曲线分辨率越低', 'number') +
            field('日志缓冲行数', 'g-ring', c.log_ring_lines, '内存中保留的控制台行数', 'number') +
            field('并发下载数', 'g-dl', c.download_workers, '1~8，避免吃满本机带宽', 'number') +
            field('备份保留份数', 'g-keep', c.backup_keep, '每实例，超出轮转删除最旧的', 'number') +
            field('自动重启次数上限', 'g-arl', c.auto_restart_limit, '崩溃后指数退避重启的最大次数', 'number') +
          '</div>' +
          field('默认 JVM 参数', 'g-jvm', c.default_jvm_args, '实例没有单独配置时使用') +
          '<div class="tip small">面板自身的改动都会写进审计日志（操作人 / 时间 / 实例 / 详情）。</div>' +
        '</div>' +
      '</div>';

    function save(btn) {
      var updates = {
        site_name: document.getElementById('g-name').value,
        port: parseInt(document.getElementById('g-port').value, 10),
        bind_host: document.getElementById('g-host').value,
        session_hours: parseInt(document.getElementById('g-sess').value, 10),
        max_login_fails: parseInt(document.getElementById('g-fails').value, 10),
        lock_minutes: parseInt(document.getElementById('g-lock').value, 10),
        default_memory_mb: parseInt(document.getElementById('g-mem').value, 10),
        sample_interval: parseInt(document.getElementById('g-sample').value, 10),
        log_ring_lines: parseInt(document.getElementById('g-ring').value, 10),
        download_workers: parseInt(document.getElementById('g-dl').value, 10),
        backup_keep: parseInt(document.getElementById('g-keep').value, 10),
        auto_restart_limit: parseInt(document.getElementById('g-arl').value, 10),
        default_jvm_args: document.getElementById('g-jvm').value
      };
      C.withLoading(btn, API.put('/api/settings', updates).then(function () {
        Toast.ok('已保存（端口/绑定地址需重启面板）');
      }).catch(function (e) { Toast.err(e.message); }));
    }
    document.getElementById('g-save').addEventListener('click', function () { save(this); });
    document.getElementById('g-save2').addEventListener('click', function () { save(this); });
  };

  TABS.java = function () {
    var detected = javaInfo.detected || [];
    var installed = javaInfo.installed || [];
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-head"><h3>已检测到的 Java</h3>' +
          '<div class="right"><button class="btn sm" id="j-rescan">' + Icon.svg('refresh', 14) + '重新检测</button></div></div>' +
          (detected.length
            ? '<div class="table-wrap"><table><thead><tr><th>版本</th><th>来源</th><th>路径</th><th style="text-align:right">设为默认</th></tr></thead><tbody>' +
              detected.map(function (j) {
                return '<tr><td class="mono num">Java ' + (j.major || '?') + '</td>' +
                  '<td><span class="badge' + (j.source === 'panel' ? ' gold' : '') + '">' + (j.source === 'panel' ? '面板安装' : '系统') + '</span></td>' +
                  '<td class="mono small">' + U.esc(j.path) + '</td>' +
                  '<td style="text-align:right"><button class="btn xs" data-def="' + U.esc(j.path) + '">设为默认</button></td></tr>';
              }).join('') + '</tbody></table></div>'
            : C.empty('java', '没有检测到 Java', '系统 PATH、JAVA_HOME 与常见安装目录都没找到可用的 java。可以在这里下载一个。', '')) +
          '<div class="mt"><div class="desc">当前默认：<span class="mono">' + U.esc(cfg.config.default_java || '（自动：PATH 中的 java）') + '</span></div></div>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>从 Adoptium 安装</h3></div>' +
          '<div class="tip mb">官方 API：api.adoptium.net。下载 Temurin JRE 到 <span class="mono">' + U.esc(javaInfo.java_dir) + '</span>，安装后可直接在实例里选用。</div>' +
          '<div class="row tight">' +
            [8, 11, 17, 21, 25].map(function (m) {
              return '<button class="btn sm" data-adopt="' + m + '">Java ' + m + '</button>';
            }).join('') +
          '</div>' +
          '<div id="j-progress" class="mt"></div>' +
          '<hr class="hair" />' +
          '<div class="kicker mb">面板已安装</div>' +
          (installed.length
            ? installed.map(function (j) {
                return '<div class="row between">' +
                  '<span class="mono">Java ' + (j.major || '?') + ' · ' + U.esc(j.dir) + '</span>' +
                  '<button class="btn xs danger" data-jdel="' + U.esc(j.dir) + '">删除</button></div>';
              }).join('')
            : '<div class="small muted">暂无</div>') +
        '</div>' +
      '</div>';

    document.getElementById('j-rescan').addEventListener('click', function () {
      C.withLoading(this, API.get('/api/java').then(function (j) { javaInfo = j; draw(); }));
    });
    document.querySelectorAll('[data-def]').forEach(function (b) {
      b.addEventListener('click', function () {
        C.withLoading(b, API.put('/api/settings', { default_java: b.getAttribute('data-def') })
          .then(function () { Toast.ok('已设为默认 Java'); loadAll(); })
          .catch(function (e) { Toast.err(e.message); }));
      });
    });
    document.querySelectorAll('[data-adopt]').forEach(function (b) {
      b.addEventListener('click', function () {
        var m = b.getAttribute('data-adopt');
        var box = document.getElementById('j-progress');
        box.innerHTML = '<div class="row small"><span class="spinner"></span>下载 Java ' + m + ' 中…</div>';
        C.withLoading(b, API.post('/api/java/install', { major: Number(m), image: 'jre' }).then(function (r) {
          box.innerHTML = '<div class="tip">已安装 Java ' + r.major + '：<span class="mono">' + U.esc(r.path) + '</span></div>';
          return loadAll();
        }).catch(function (e) {
          box.innerHTML = '<div class="tip err">失败（源不可达）：' + U.esc(e.message) + '</div>';
        }));
      });
    });
    document.querySelectorAll('[data-jdel]').forEach(function (b) {
      b.addEventListener('click', function () {
        C.Modal.confirm('删除 Java', '将删除该运行时目录（仍被实例引用的不允许删除）。确定？', function () {
          return API.del('/api/java/' + encodeURIComponent(b.getAttribute('data-jdel'))).then(function () {
            Toast.ok('已删除'); loadAll();
          }).catch(function (e) { Toast.err(e.message); });
        }, true);
      });
    });
  };

  TABS.sources = function () {
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-head"><h3>下载源可达性实测</h3>' +
          '<div class="right"><button class="btn sm primary" id="s-probe">' + Icon.svg('search', 14) + '开始实测</button></div></div>' +
          '<div class="tip mb">逐个源真实请求一次版本清单。外网受限时会如实显示失败原因，不会假装可用。</div>' +
          '<div id="s-result">' + (probe ? renderProbe(probe) : '<div class="small muted">尚未测试。点击「开始实测」。</div>') + '</div>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>下载与镜像</h3>' +
          '<div class="right"><button class="btn sm primary" id="s-save">保存</button></div></div>' +
          field('镜像前缀（可选）', 's-mirror', cfg.config.mirror_prefix,
                '例如 https://bmclapi2.bangbang93.com —— 仅对 Mojang 域名生效，留空直连') +
          field('并发下载数', 's-workers', cfg.config.download_workers, '建议 2；本机有其它任务时不要调高', 'number') +
          '<div class="tip small">下载都会校验源提供的 sha1 / sha256；源不提供哈希时会在任务详情里注明「跳过校验」。</div>' +
          '<hr class="hair" />' +
          '<div class="kicker mb">插件 / 模组源</div>' +
          '<div class="small muted mb">Modrinth 可用；SpigotMC 受 Cloudflare 与登录限制，成功率低；CurseForge 官方接口不允许匿名访问，需要 API Key。</div>' +
          field('CurseForge API Key', 's-cf', '', cfg.config.curseforge_api_key_set ? '已设置（留空不修改）' : '未设置', 'password') +
          '<button class="btn sm" id="s-plugin-probe">实测三个插件源</button>' +
          '<div id="s-plugin-result" class="mt"></div>' +
        '</div>' +
      '</div>';
    document.getElementById('s-save').addEventListener('click', function () {
      var body = {
        mirror_prefix: document.getElementById('s-mirror').value.trim(),
        download_workers: parseInt(document.getElementById('s-workers').value, 10)
      };
      var cf = document.getElementById('s-cf').value.trim();
      if (cf) body.curseforge_api_key = cf;
      C.withLoading(this, API.put('/api/settings', body).then(function () { Toast.ok('已保存'); loadAll(); })
        .catch(function (e) { Toast.err(e.message); }));
    });
    document.getElementById('s-probe').addEventListener('click', function () {
      var box = document.getElementById('s-result');
      box.innerHTML = '<div class="loading"><span class="spinner"></span>逐个源实测中（可能较慢，取决于网络）…</div>';
      C.withLoading(this, API.get('/api/cores-probe').then(function (d) {
        probe = d.results || [];
        box.innerHTML = renderProbe(probe);
      }).catch(function (e) { box.innerHTML = '<div class="tip err">实测失败：' + U.esc(e.message) + '</div>'; }));
    });
    document.getElementById('s-plugin-probe').addEventListener('click', function () {
      var box = document.getElementById('s-plugin-result');
      box.innerHTML = '<div class="loading"><span class="spinner"></span>测试中…</div>';
      C.withLoading(this, API.get('/api/instances/0/plugins/sources').catch(function () {
        // 0 号实例不存在，改用通用探测：直接请求 modrinth / spiget / curseforge
        return API.get('/api/plugins-probe');
      }).then(function (d) {
        box.innerHTML = (d.results || []).map(function (r) {
          return '<div class="row between"><span>' + U.esc(r.source) + '</span>' +
            (r.ok ? '<span class="badge ok">可达</span>' : '<span class="badge err" title="' + U.esc(r.error) + '">不可用</span>') +
            '</div><div class="small faint">' + U.esc(r.error || '') + '</div>';
        }).join('');
      }).catch(function (e) { box.innerHTML = '<div class="tip err">' + U.esc(e.message) + '</div>'; }));
    });
  };

  function renderProbe(list) {
    return '<div class="table-wrap"><table><thead><tr><th>下载源</th><th>状态</th><th class="mono">版本数</th><th>失败原因</th></tr></thead><tbody>' +
      list.map(function (r) {
        return '<tr><td>' + U.esc(r.label || r.source) + '</td>' +
          '<td>' + (r.ok ? '<span class="badge ok">可达</span>' : '<span class="badge err">不可用</span>') + '</td>' +
          '<td class="mono num">' + (r.count || 0) + '</td>' +
          '<td class="small faint">' + U.esc(r.error || '') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  TABS.rcon = function () {
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-head"><h3>RCON 默认值</h3>' +
          '<div class="right"><button class="btn sm primary" id="r-save">保存</button></div></div>' +
          '<div class="tip mb">实例没有单独配置 RCON 时使用这里的默认值。注意：server.properties 里也要开 <span class="mono">enable-rcon=true</span> 并设置同样的密码。</div>' +
          '<div class="field"><label class="check"><input type="checkbox" id="r-en"' + (cfg.config.rcon_enabled ? ' checked' : '') + ' />默认启用 RCON</label></div>' +
          '<div class="grid cols-2 tight">' +
            field('主机', 'r-host', cfg.config.rcon_host) +
            field('端口', 'r-port', cfg.config.rcon_port, '', 'number') +
          '</div>' +
          field('密码', 'r-pwd', '', cfg.config.rcon_password_set ? '已设置（留空不修改）' : '未设置', 'password') +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>测试</h3></div>' +
          '<div class="field"><label>测试目标实例</label><select id="r-inst"></select></div>' +
          '<button class="btn" id="r-test">发送 list 命令</button>' +
          '<div id="r-result" class="mt"></div>' +
          '<hr class="hair" />' +
          '<div class="small muted">RCON 用于取在线玩家列表、执行 op/ban 等命令，以及拿 TPS（若服务端支持）。' +
          '不可用时面板会标注「不可用」，不会编造数据。</div>' +
        '</div>' +
      '</div>';
    API.get('/api/instances').then(function (d) {
      var sel = document.getElementById('r-inst');
      sel.innerHTML = (d.instances || []).map(function (i) {
        return '<option value="' + i.id + '">' + U.esc(i.name) + '（端口 ' + i.port + '）</option>';
      }).join('') || '<option value="">（没有实例）</option>';
    });
    document.getElementById('r-save').addEventListener('click', function () {
      var body = {
        rcon_enabled: document.getElementById('r-en').checked,
        rcon_host: document.getElementById('r-host').value.trim(),
        rcon_port: parseInt(document.getElementById('r-port').value, 10)
      };
      var p = document.getElementById('r-pwd').value;
      if (p) body.rcon_password = p;
      C.withLoading(this, API.put('/api/settings', body).then(function () { Toast.ok('已保存'); loadAll(); })
        .catch(function (e) { Toast.err(e.message); }));
    });
    document.getElementById('r-test').addEventListener('click', function () {
      var iid = document.getElementById('r-inst').value;
      var box = document.getElementById('r-result');
      if (!iid) { Toast.warn('没有可测试的实例'); return; }
      box.innerHTML = '<div class="row small"><span class="spinner"></span>连接中…</div>';
      C.withLoading(this, API.get('/api/instances/' + iid + '/rcon/test').then(function (r) {
        box.innerHTML = r.ok
          ? '<div class="tip">连接成功：<span class="mono">' + U.esc((r.result || '').slice(0, 300)) + '</span></div>'
          : '<div class="tip err">不可用：' + U.esc((r.error || '').slice(0, 300)) + '</div>';
      }).catch(function (e) { box.innerHTML = '<div class="tip err">' + U.esc(e.message) + '</div>'; }));
    });
  };

  TABS.theme = function () {
    var theme = document.documentElement.getAttribute('data-theme') || 'auto';
    var dens = document.documentElement.getAttribute('data-density') || 'comfy';
    var variants = [
      { k: 'darkgold', n: '暗色 · 金', c: '#F5D061', d: '默认：近黑底 + 金色强调' },
      { k: 'lightgold', n: '亮色 · 纸面', c: '#8A6508', d: '米白纸面 + 深金（小字用深金保证 AA）' },
      { k: 'auto', n: '跟随系统', c: '#8494AB', d: '按 prefers-color-scheme 自动切换' },
      { k: 'orange', n: '活力橙', c: '#FF8A3D', d: '暖橙强调，适合夜间长时间运维' },
      { k: 'blue', n: '静谧蓝', c: '#4C9AFF', d: '冷静蓝调，弱化视觉疲劳' },
      { k: 'violet', n: '薰衣草紫', c: '#A78BFA', d: '柔和紫，辨识度高' },
      { k: 'mint', n: '薄荷绿', c: '#3FD6A8', d: '清爽绿，适合监控常驻' }
    ];
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-head"><h3>主题配色</h3>' +
          '<div class="right"><span class="badge gold">7 套</span></div></div>' +
          '<div class="core-grid">' + variants.map(function (v) {
            return '<div class="core-card' + (theme === v.k ? ' sel' : '') + '" data-theme-set="' + v.k + '">' +
              '<div class="nm"><span style="width:14px;height:14px;border-radius:4px;background:' + v.c + ';display:inline-block"></span>' +
              U.esc(v.n) + '</div><div class="ds">' + U.esc(v.d) + '</div></div>';
          }).join('') + '</div>' +
          '<hr class="hair" />' +
          '<div class="kicker mb">护眼与对比度</div>' +
          '<label class="check"><input type="checkbox" id="t-eyecare" />护眼模式（降低饱和与亮度）</label>' +
          '<label class="check mt"><input type="checkbox" id="t-contrast" />高对比度模式（增强描边与文本对比）</label>' +
          '<label class="check mt"><input type="checkbox" id="t-motion" checked />允许动效（关闭=尊重 prefers-reduced-motion）</label>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>信息密度与毛玻璃</h3></div>' +
          '<div class="kicker mb">信息密度</div>' +
          '<div class="btn-group mb"><button class="btn sm' + (dens === 'comfy' ? ' active' : '') + '" data-dens="comfy">舒适</button>' +
            '<button class="btn sm' + (dens === 'compact' ? ' active' : '') + '" data-dens="compact">紧凑</button></div>' +
          '<div class="kicker mb">毛玻璃效果（分别开关）</div>' +
          '<label class="check"><input type="checkbox" id="t-glass-top" checked />顶栏毛玻璃</label>' +
          '<label class="check mt"><input type="checkbox" id="t-glass-side" />侧栏毛玻璃</label>' +
          '<label class="check mt"><input type="checkbox" id="t-glass-bottom" />浮层（弹窗/toast）毛玻璃</label>' +
          '<div class="tip mt small">浏览器不支持 backdrop-filter 时自动降级成实色背景（面板会检测并提示）。</div>' +
          '<div class="kicker mb mt">顶栏元素可见性</div>' +
          '<label class="check"><input type="checkbox" id="t-show-crumbs" checked />面包屑</label>' +
          '<label class="check mt"><input type="checkbox" id="t-show-clock" checked />时钟</label>' +
        '</div>' +
        '<div id="bg-manager"></div>' +
      '</div>';

    renderBgManager();

    document.querySelectorAll('[data-theme-set]').forEach(function (el) {
      el.addEventListener('click', function () {
        var k = el.getAttribute('data-theme-set');
        document.documentElement.setAttribute('data-theme', k);
        try { localStorage.setItem('mc_theme', k); } catch (e) {}
        draw();
        Toast.ok('主题已切换为：' + k);
      });
    });
    document.querySelectorAll('[data-dens]').forEach(function (el) {
      el.addEventListener('click', function () {
        var k = el.getAttribute('data-dens');
        document.documentElement.setAttribute('data-density', k);
        try { localStorage.setItem('mc_density', k); } catch (e) {}
        draw();
      });
    });
    function opt(id, attr, on, onVal, offVal) {
      var el = document.getElementById(id);
      if (!el) return;
      el.checked = on;
      el.addEventListener('change', function () {
        document.documentElement.setAttribute(attr, el.checked ? onVal : offVal);
        try { localStorage.setItem(attr, el.checked ? onVal : offVal); } catch (e) {}
        Toast.info('已' + (el.checked ? '开启' : '关闭') + '：' + id);
        if (attr === 'data-eyecare' || attr === 'data-contrast') Router.refresh();
      });
    }
    var de = document.documentElement;
    opt('t-eyecare', 'data-eyecare', de.getAttribute('data-eyecare') === 'on', 'on', 'off');
    opt('t-contrast', 'data-contrast', de.getAttribute('data-contrast') === 'on', 'on', 'off');
    opt('t-motion', 'data-motion', de.getAttribute('data-motion') !== 'off', 'on', 'off');
    opt('t-glass-top', 'data-glass-top', de.getAttribute('data-glass-top') !== 'off', 'on', 'off');
    opt('t-glass-side', 'data-glass-side', de.getAttribute('data-glass-side') === 'on', 'on', 'off');
    opt('t-glass-bottom', 'data-glass-bottom', de.getAttribute('data-glass-bottom') !== 'off', 'on', 'off');
    opt('t-show-crumbs', 'data-show-crumbs', de.getAttribute('data-show-crumbs') !== 'off', 'on', 'off');
    opt('t-show-clock', 'data-show-clock', de.getAttribute('data-show-clock') === 'on', 'on', 'off');
    if (!window.CSS || !CSS.supports || !CSS.supports('backdrop-filter', 'blur(4px)')) {
      document.getElementById('set-body').insertAdjacentHTML('beforeend',
        '<div class="tip warn mt">当前浏览器不支持 backdrop-filter，毛玻璃已自动降级为实色背景。</div>');
    }
  };

  /* ---------------------------------------------------------------- 背景层管理器
     多图上传 · 拖拽排序 · 每张独立遮罩 · 轮播 10–600s · 启动策略 · 全局模糊/暗角 */
  function renderBgManager() {
    var box = document.getElementById('bg-manager');
    if (!box) return;
    var c = Bg.load();
    box.innerHTML = '' +
      '<div class="card mt"><div class="card-head"><h3>背景层</h3>' +
        '<div class="right">' +
          '<label class="check small"><input type="checkbox" id="bg-en"' + (c.enabled ? ' checked' : '') + ' />启用背景</label>' +
          '<button class="btn sm" id="bg-add">' + Icon.svg('image', 14) + '上传图片</button>' +
        '</div></div>' +
      '<div class="tip mb">背景图存在浏览器本地（localStorage）。支持多张、拖拽排序、每张独立遮罩；运行中按下方间隔轮播，切换为淡入淡出。' +
        '注意：单张建议 &lt; 1.5MB，否则可能超出本地存储配额。</div>' +
      (c.items.length
        ? '<div class="bg-grid" id="bg-grid">' + c.items.map(function (it, i) {
            return '<div class="bg-card" draggable="true" data-bi="' + i + '">' +
              '<div class="bg-thumb" style="background-image:url(\'' + it.data + '\')">' +
                '<span class="idx">#' + (i + 1) + '</span></div>' +
              '<div class="body">' +
                '<div class="nm">' + U.esc(it.name || '') + '</div>' +
                '<div class="field" style="margin:var(--sp-2) 0 0"><label class="small">本图遮罩 ' +
                  Math.round((it.mask === undefined ? c.maskAlpha : it.mask) * 100) + '%</label>' +
                  '<input type="range" min="0" max="100" value="' +
                  Math.round((it.mask === undefined ? c.maskAlpha : it.mask) * 100) + '" data-bmask="' + i + '" /></div>' +
                '<div class="field" style="margin:var(--sp-2) 0 0"><label class="small">本图模糊 ' + (it.blur || 0) + 'px</label>' +
                  '<input type="range" min="0" max="24" value="' + (it.blur || 0) + '" data-bblur="' + i + '" /></div>' +
                '<div class="row tight mt">' +
                  '<button class="btn xs" data-bup="' + i + '">前移</button>' +
                  '<button class="btn xs" data-bdown="' + i + '">后移</button>' +
                  '<button class="btn xs danger" data-bdel="' + i + '">删除</button>' +
                '</div>' +
              '</div></div>';
          }).join('') + '</div>'
        : '<div class="empty"><div class="art">' + Icon.svg('image', 30) + '</div>' +
          '<div class="t">还没有背景图</div><div class="d">上传一张或几张图片，面板就有了自己的氛围 —— 也可以保持默认的近黑纯色。</div>' +
          '<button class="btn primary" id="bg-add2">' + Icon.svg('image', 0) + '上传第一张背景</button></div>') +
      '<hr class="hair" />' +
      '<div class="grid cols-3 tight">' +
        '<div class="field"><label>轮播间隔（秒，10–600）</label>' +
          '<input type="number" id="bg-iv" class="num" min="10" max="600" value="' + c.interval + '" /></div>' +
        '<div class="field"><label>启动时策略</label><select id="bg-strategy">' +
          [['keep', '保持（始终第一张）'], ['sequential', '顺序（从第一张开始）'], ['random', '随机']]
            .map(function (o) {
              return '<option value="' + o[0] + '"' + (c.strategy === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
            }).join('') + '</select></div>' +
        '<div class="field"><label>统一遮罩颜色</label><input type="color" id="bg-mcolor" value="' + (c.maskColor || '#000000') + '" /></div>' +
        '<div class="field"><label>默认遮罩强度 ' + Math.round(c.maskAlpha * 100) + '%</label>' +
          '<input type="range" id="bg-mask" min="0" max="100" value="' + Math.round(c.maskAlpha * 100) + '" /></div>' +
        '<div class="field"><label>全局暗角 ' + Math.round(c.vignette * 100) + '%</label>' +
          '<input type="range" id="bg-vig" min="0" max="100" value="' + Math.round(c.vignette * 100) + '" /></div>' +
      '</div>' +
      '<input type="file" id="bg-file" accept="image/*" multiple style="display:none" />' +
      '</div>';

    function apply() {
      Bg.save();
      // 重建背景层：壳层已渲染，直接替换 #mc-bg
      var old = document.getElementById('mc-bg');
      if (old) {
        var tmp = document.createElement('div');
        tmp.innerHTML = Bg.layerHtml();
        old.parentNode.replaceChild(tmp.firstChild, old);
      }
      if (Router.current()) Router.refresh();
    }

    var en = document.getElementById('bg-en');
    if (en) en.addEventListener('change', function () { Bg.load().enabled = en.checked; apply(); draw(); });
    function pick() { document.getElementById('bg-file').click(); }
    ['bg-add', 'bg-add2'].forEach(function (id) {
      var b = document.getElementById(id);
      if (b) b.addEventListener('click', pick);
    });
    var fi = document.getElementById('bg-file');
    if (fi) fi.addEventListener('change', function (e) {
      var files = Array.prototype.slice.call(e.target.files);
      var left = files.length;
      files.forEach(function (f) {
        if (f.size > 3 * 1024 * 1024) { Toast.warn(f.name + ' 超过 3MB，建议压缩后再用'); }
        var fr = new FileReader();
        fr.onload = function () {
          Bg.add(fr.result, f.name);
          if (--left <= 0) { apply(); draw(); Toast.ok('已添加 ' + files.length + ' 张背景'); }
        };
        fr.readAsDataURL(f);
      });
      e.target.value = '';
    });
    // 每图独立遮罩 / 模糊
    box.querySelectorAll('[data-bmask]').forEach(function (r) {
      r.addEventListener('input', function () {
        var i = Number(r.getAttribute('data-bmask'));
        Bg.load().items[i].mask = Number(r.value) / 100;
        Bg.save();
        r.parentNode.querySelector('label').textContent = '本图遮罩 ' + r.value + '%';
      });
      r.addEventListener('change', apply);
    });
    box.querySelectorAll('[data-bblur]').forEach(function (r) {
      r.addEventListener('input', function () {
        var i = Number(r.getAttribute('data-bblur'));
        Bg.load().items[i].blur = Number(r.value);
        Bg.save();
        r.parentNode.querySelector('label').textContent = '本图模糊 ' + r.value + 'px';
      });
      r.addEventListener('change', apply);
    });
    box.querySelectorAll('[data-bup]').forEach(function (b) {
      b.addEventListener('click', function () {
        Bg.move(Number(b.getAttribute('data-bup')), Number(b.getAttribute('data-bup')) - 1);
        apply(); draw();
      });
    });
    box.querySelectorAll('[data-bdown]').forEach(function (b) {
      b.addEventListener('click', function () {
        Bg.move(Number(b.getAttribute('data-bdown')), Number(b.getAttribute('data-bdown')) + 1);
        apply(); draw();
      });
    });
    box.querySelectorAll('[data-bdel]').forEach(function (b) {
      b.addEventListener('click', function () {
        Bg.remove(Number(b.getAttribute('data-bdel')));
        apply(); draw();
      });
    });
    // 拖拽排序
    var dragFrom = null;
    box.querySelectorAll('.bg-card').forEach(function (card) {
      card.addEventListener('dragstart', function () {
        dragFrom = Number(card.getAttribute('data-bi'));
        card.classList.add('dragging');
      });
      card.addEventListener('dragend', function () { card.classList.remove('dragging'); });
      card.addEventListener('dragover', function (e) { e.preventDefault(); card.classList.add('drop-target'); });
      card.addEventListener('dragleave', function () { card.classList.remove('drop-target'); });
      card.addEventListener('drop', function (e) {
        e.preventDefault();
        card.classList.remove('drop-target');
        var to = Number(card.getAttribute('data-bi'));
        if (dragFrom === null || dragFrom === to) return;
        Bg.move(dragFrom, to);
        dragFrom = null;
        apply(); draw();
      });
    });
    ['bg-iv', 'bg-strategy', 'bg-mcolor', 'bg-mask', 'bg-vig'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', function () {
        var c2 = Bg.load();
        c2.interval = Math.min(600, Math.max(10, Number(document.getElementById('bg-iv').value) || 30));
        c2.strategy = document.getElementById('bg-strategy').value;
        c2.maskColor = document.getElementById('bg-mcolor').value;
        c2.maskAlpha = Number(document.getElementById('bg-mask').value) / 100;
        c2.vignette = Number(document.getElementById('bg-vig').value) / 100;
        apply();
        draw();
      });
    });
  }

  TABS.advanced = function () {
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2">' +
        /* 远程推送更新：检查 → 验签 → 下载 → 备份 → 应用。全流程在面板侧独立校验，
           官网只提供清单；未绑定时也能收到「全局推送」，绑定后才有定向推送。 */
        '<div class="card" style="grid-column:1/-1"><div class="card-head"><h3>面板更新</h3>' +
          '<div class="right">' +
            '<span class="badge mono" id="u-ver">当前版本</span>' +
            '<button class="btn sm" id="u-check">' + Icon.svg('refresh', 14) + '检查更新</button>' +
          '</div></div>' +
          '<div id="u-body" class="small muted">点「检查更新」向官网查询新版本（会做签名校验，篡改的包会被拒绝）。</div>' +
          '<div class="row mt" id="u-acts" style="display:none">' +
            '<button class="btn sm primary" id="u-apply">下载并更新</button>' +
            '<button class="btn sm" id="u-restart">重启面板</button>' +
          '</div>' +
          '<div class="tip small mt">更新只替换代码，<b>不会碰数据目录</b>（实例 / 世界 / 备份 / 配置都安全）。'
            + '更新后需重启面板生效。</div>' +
          '<div class="row mt"><div class="small muted" id="u-bind-info">绑定状态加载中…</div>' +
            '<button class="btn sm" id="u-bind">绑定官网账号</button>' +
            '<button class="btn sm" id="u-unbind" style="display:none">解除绑定</button></div>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>磁盘与缓存</h3>' +
          '<div class="right"><button class="btn sm" id="a-refresh">' + Icon.svg('refresh', 14) + '刷新</button></div></div>' +
          '<div id="a-usage"><div class="loading"><span class="spinner"></span>统计中…</div></div>' +
          '<div class="row mt">' +
            '<button class="btn sm" id="a-clean-tmp">清理下载临时文件</button>' +
            '<button class="btn sm" id="a-clean-metrics">清理 7 天前监控数据</button>' +
            '<button class="btn sm danger" id="a-clean-logs">清除历史日志</button>' +
          '</div>' +
        '</div>' +
        '<div class="card"><div class="card-head"><h3>统一应用日志</h3>' +
          '<div class="right"><button class="btn sm" id="a-log-dl">' + Icon.svg('download', 14) + '打包导出</button>' +
          '<button class="btn sm" id="a-log-refresh">刷新</button></div></div>' +
          '<div class="small muted mb">面板自身的操作 / API / 崩溃都会记录在这里，便于排障。</div>' +
          '<div class="crumbs-file" style="max-height:280px;overflow:auto" id="a-log">加载中…</div>' +
        '</div>' +
      '</div>';
    statUsage();
    loadLog();
    bindUpdateCard();
    document.getElementById('a-refresh').addEventListener('click', statUsage);
    document.getElementById('a-log-refresh').addEventListener('click', loadLog);
    document.getElementById('a-log-dl').addEventListener('click', function () {
      U.download(API.fileUrl('/api/logs/app/download'));
    });
    function act(id, path, confirmText, body) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('click', function () {
        C.Modal.confirm('确认操作', confirmText, function () {
          return API.post(path, body || {}).then(function (r) {
            Toast.ok('完成：' + JSON.stringify(r).slice(0, 160));
            statUsage();
          }).catch(function (e) { Toast.err(e.message); });
        }, true);
      });
    }
    act('a-clean-tmp', '/api/maintenance/clean-tmp', '清理 data/tmp 下超过 24 小时的下载临时文件。');
    act('a-clean-metrics', '/api/maintenance/clean-metrics', '删除 7 天前的监控采样数据（曲线历史会变短）。');
    act('a-clean-logs', '/api/maintenance/clean-logs', '清除所有实例的历史日志文件（保留 latest.log）。');
  };

  /* 远程推送更新的界面逻辑：
     检查更新（面板侧会做 RSA 验签与产品归属校验）→ 展示变更说明 → 应用 → 提示重启。
     绑定官网账号后可收到「定向推送」（官网后台把某个版本推给这台机器）。 */
  function bindUpdateCard() {
    var body = document.getElementById('u-body');
    var acts = document.getElementById('u-acts');
    var verEl = document.getElementById('u-ver');
    var bindInfo = document.getElementById('u-bind-info');
    if (!body) return;
    var lastInfo = null;

    function renderBind(st) {
      if (!bindInfo) return;
      if (st && st.bound) {
        bindInfo.textContent = '已绑定官网账号 ' + (st.account || '') + '（机器号 ' + (st.machine_id || '') + '）';
        document.getElementById('u-bind').style.display = 'none';
        document.getElementById('u-unbind').style.display = '';
      } else {
        bindInfo.textContent = '未绑定官网账号（机器号 ' + ((st && st.machine_id) || '—')
          + '）：仍可收到全局更新，绑定后才能收到定向推送';
        document.getElementById('u-bind').style.display = '';
        document.getElementById('u-unbind').style.display = 'none';
      }
      if (verEl && st && st.server) verEl.textContent = '更新源 ' + st.server.replace(/^https?:\/\//, '');
    }

    function loadBinding() {
      API.get('/api/update/binding').then(function (st) { renderBind(st); })
        .catch(function () { if (bindInfo) bindInfo.textContent = '绑定状态读取失败（需超级管理员）'; });
    }

    function showStatus() {
      API.get('/api/update/status').then(function (d) {
        if (verEl) verEl.textContent = '当前版本 ' + (d.current_version || '');
        if (d.busy) {
          body.innerHTML = '<div class="loading"><span class="spinner"></span>更新进行中…</div>'
            + '<pre class="mono small" style="max-height:180px;overflow:auto">'
            + U.esc((d.log || []).join('\n')) + '</pre>';
          setTimeout(showStatus, 2000);
          return;
        }
        if (d.last_result) {
          var r = d.last_result;
          body.innerHTML = (r.ok
            ? '<div class="tip ok">更新完成：'
              + U.esc(String(r.version || '')) + '（覆盖 ' + r.replaced + ' / 新增 ' + r.created + '）'
              + '，请重启面板生效。</div>'
            : '<div class="tip err">更新失败：' + U.esc(r.error || '未知原因') + '</div>')
            + (r.backup_dir ? '<div class="small muted mt">备份：<span class="mono">' + U.esc(r.backup_dir) + '</span></div>' : '');
        }
      }).catch(function () {});
    }

    document.getElementById('u-check').addEventListener('click', function () {
      body.innerHTML = '<div class="loading"><span class="spinner"></span>正在向官网查询…</div>';
      API.get('/api/update/check').then(function (d) {
        if (verEl && d.current_version) verEl.textContent = '当前版本 ' + d.current_version;
        if (!d.ok) {
          body.innerHTML = '<div class="tip err">' + U.esc(d.error || '检查失败') + '</div>';
          acts.style.display = 'none';
          return;
        }
        if (!d.has_update) {
          body.innerHTML = '<div class="tip ok">已是最新版本（' + U.esc(String(d.current_version || '')) + '）</div>'
            + (d.note ? '<div class="small muted mt">' + U.esc(d.note) + '</div>' : '');
          acts.style.display = 'none';
          return;
        }
        lastInfo = d;
        body.innerHTML = '<div class="tip ok">发现新版本 <b>' + U.esc(String(d.version || ''))
          + '</b>（当前 ' + U.esc(String(d.current_version || '')) + '，共 '
          + (d.file_count || 0) + ' 个文件）</div>'
          + '<div class="small mt" style="white-space:pre-wrap">' + U.esc(d.notes || '（无变更说明）') + '</div>'
          + '<div class="small muted mt">来源：' + U.esc(String(d.push_mode || 'global'))
          + (d.push_mode === 'machine' ? '（定向推送）' : '（全局）') + '</div>';
        acts.style.display = '';
      }).catch(function (e) {
        body.innerHTML = '<div class="tip err">' + U.esc(e.message) + '</div>';
        acts.style.display = 'none';
      });
    });

    document.getElementById('u-apply').addEventListener('click', function () {
      C.Modal.confirm('确认更新', '将从官网下载更新包，校验签名与逐文件哈希后覆盖面板代码'
        + '（会先整份备份，失败自动回滚；不会碰数据目录）。继续？', function () {
        return API.post('/api/update/apply', {}).then(function (r) {
          Toast.ok('已开始更新到 ' + (r.version || ''));
          showStatus();
        });
      }, true);
    });

    document.getElementById('u-restart').addEventListener('click', function () {
      C.Modal.confirm('重启面板', '面板会在 1 秒后重启。若用 systemd / panelctl 部署会自动拉起，'
        + '否则请手动执行 panelctl start。正在运行的 MC 实例不受影响。继续？', function () {
        return API.post('/api/update/restart', {}).then(function () {
          Toast.ok('面板正在重启，请稍后刷新页面');
        });
      }, true);
    });

    document.getElementById('u-bind').addEventListener('click', function () {
      C.Modal.open({
        title: '绑定官网账号',
        footer: false,
        body: '<div class="tip mb">绑定后可收到「定向推送」（官网后台可把某个版本只推给这台机器）。'
          + '全局更新不绑定也能收到。凭据只保存在本机数据目录（0600 权限）。</div>'
          + '<div class="field"><label>官网账号（邮箱或用户名）</label>'
          + '<input type="text" id="ub-acc" autocomplete="off" /></div>'
          + '<div class="field"><label>官网密码</label>'
          + '<input type="password" id="ub-pw" autocomplete="off" /></div>'
          + '<div class="row" style="justify-content:flex-end">'
          + '<button class="btn" id="ub-cancel">取消</button>'
          + '<button class="btn primary" id="ub-ok">绑定</button></div>',
        onMount: function (mask) {
          mask.querySelector('#ub-cancel').addEventListener('click', function () { C.Modal.close(); });
          var doBind = function () {
            var v = {
              account: (mask.querySelector('#ub-acc').value || '').trim(),
              password: mask.querySelector('#ub-pw').value || ''
            };
            if (!v.account || !v.password) { Toast.warn('请填写账号与密码'); return; }
            API.post('/api/update/bind', v).then(function (st) {
              Toast.ok('绑定成功');
              C.Modal.close();
              renderBind(st);
            }).catch(function (e) { Toast.err(e.message); });
          };
          mask.querySelector('#ub-ok').addEventListener('click', doBind);
          mask.querySelector('#ub-pw').addEventListener('keydown', function (e) {
            if (e.key === 'Enter') doBind();
          });
        }
      });
    });

    document.getElementById('u-unbind').addEventListener('click', function () {
      C.Modal.confirm('解除绑定', '仅清除本机保存的绑定信息（不影响官网账号与其它机器）。', function () {
        return API.post('/api/update/unbind', { local_only: true }).then(function (st) {
          Toast.ok('已解除绑定');
          renderBind(st);
        });
      });
    });

    loadBinding();
    showStatus();
  }

  function statUsage() {
    API.get('/api/maintenance/usage').then(function (d) {
      var box = document.getElementById('a-usage');
      if (!box) return;
      box.innerHTML = '<div class="kv">' + Object.keys(d.usage || {}).map(function (k) {
        return '<div class="k">' + U.esc(k) + '</div><div class="v">' + U.fmtSize(d.usage[k]) + '</div>';
      }).join('') +
        '<div class="k">监控采样行数</div><div class="v">' + d.metrics_rows + '</div>' +
        '<div class="k">审计日志行数</div><div class="v">' + d.audit_rows + '</div>' +
        '</div>';
    }).catch(function (e) {
      var box = document.getElementById('a-usage');
      if (box) box.innerHTML = '<div class="tip err">' + U.esc(e.message) + '</div>';
    });
  }

  function loadLog() {
    API.get('/api/logs/app?limit=120').then(function (d) {
      var box = document.getElementById('a-log');
      if (box) box.innerHTML = (d.lines || []).map(function (l) {
        return '<div class="' + U.esc(l.level || 'info') + '">' + U.esc(l.text) + '</div>';
      }).join('') || '<span class="faint">暂无日志</span>';
    }).catch(function () {});
  }

  TABS.password = function () {
    document.getElementById('set-body').innerHTML = '' +
      '<div class="grid cols-2"><div class="card"><div class="card-head"><h3>修改管理员口令</h3></div>' +
        '<div class="tip mb">PBKDF2-SHA256 / 600000 次迭代。至少 12 位，且包含小写、大写、数字、符号中的任意三类。修改后所有会话立即失效。</div>' +
        '<div class="field"><label>当前口令</label><input type="password" id="pw-old" /></div>' +
        '<div class="field"><label>新口令</label><input type="password" id="pw-new" />' +
          '<div class="desc" id="pw-meter">强度：—</div></div>' +
        '<div class="field"><label>确认新口令</label><input type="password" id="pw-new2" /></div>' +
        '<button class="btn primary" id="pw-save">修改口令</button>' +
      '</div><div class="card"><div class="card-head"><h3>口令策略自查</h3></div>' +
        '<div class="kv small" id="pw-check"></div>' +
      '</div></div>';
    var nw = document.getElementById('pw-new');
    function meter() {
      var v = nw.value;
      var classes = [ /[a-z]/.test(v), /[A-Z]/.test(v), /[0-9]/.test(v), /[^A-Za-z0-9]/.test(v) ]
        .filter(Boolean).length;
      var ok = v.length >= 12 && classes >= 3;
      document.getElementById('pw-meter').textContent =
        '长度 ' + v.length + ' / 12 · 字符类别 ' + classes + ' / 3 · ' + (ok ? '符合策略' : '不符合策略');
      document.getElementById('pw-meter').style.color = ok ? 'var(--success)' : 'var(--warning)';
      document.getElementById('pw-check').innerHTML =
        '<div class="k">长度 ≥ 12</div><div class="v">' + (v.length >= 12 ? '通过' : '未通过') + '</div>' +
        '<div class="k">至少 3 类字符</div><div class="v">' + (classes >= 3 ? '通过' : '未通过') + '</div>' +
        '<div class="k">哈希算法</div><div class="v">PBKDF2-SHA256 × 600000</div>';
    }
    nw.addEventListener('input', meter);
    meter();
    document.getElementById('pw-save').addEventListener('click', function () {
      var o = document.getElementById('pw-old').value;
      var a = document.getElementById('pw-new').value;
      var b = document.getElementById('pw-new2').value;
      if (a !== b) { Toast.warn('两次输入的新口令不一致'); return; }
      C.withLoading(this, API.post('/api/auth/password', { old_password: o, new_password: a }).then(function () {
        Toast.ok('口令已修改，请重新登录');
        setTimeout(function () { API.clearToken(); location.hash = '#/login'; }, 900);
      }).catch(function (e) { Toast.err(e.message); }));
    });
  };

  loadAll();
};
