/* 新建实例向导：选核心 → 选版本 → 内存/端口 → Java → 确认下载并创建（步骤条 + 进度 + 可返回） */
window.Pages = window.Pages || {};

Pages.create = function (app) {
  var st = {
    step: 0,
    sources: [],
    source: 'paper',
    versions: [],
    version: '',
    builds: [],
    build: '',
    memory: 2048,
    port: 25565,
    name: '',
    dir: '',
    javaPath: '',
    javas: [],
    autoRestart: true,
    eula: true,
    download: true,
    rcon: false,
    rconPort: 25575,
    loadingVersions: false,
    versionError: '',
    created: null
  };

  app.innerHTML = C.shell({
    active: 'create',
    crumbs: [{ text: '服务器', hash: '#/instances' }, { text: '新建实例' }]
  });
  C.bindShell(app);
  var body = document.getElementById('page-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>加载下载源与 Java 运行时…</div>';

  var SOURCE_DESC = {
    vanilla: 'Mojang 官方原版服务端，最纯净，无优化无插件。',
    paper: '最常用的高性能插件服，兼容 Bukkit/Spigot 插件，自带 TPS 输出。',
    purpur: 'Paper 的可配置分支，暴露大量原版可调项。',
    fabric: '轻量模组加载器，启动快、版本跟进快。',
    quilt: 'Fabric 的分支，生态较少；源不稳定时可改用 Fabric。',
    forge: '老牌模组加载器，需要先运行安装器生成启动脚本。',
    neoforge: 'Forge 的现代分支，社区活跃，同样需要安装器。'
  };

  Promise.all([
    API.get('/api/cores').catch(function () { return { sources: [] }; }),
    API.get('/api/java').catch(function () { return { detected: [] }; })
  ]).then(function (r) {
    st.sources = (r[0].sources || []).map(function (s) {
      s.desc = SOURCE_DESC[s.id] || '';
      return s;
    });
    st.javas = r[1].detected || [];
    if (st.javas.length) st.javaPath = st.javas[0].path;
    if (!st.name) st.name = '我的服务器';
    render();
    loadVersions('paper');
  });

  function steps() {
    var names = ['选核心', '选版本', '内存 / 端口', 'Java 运行时', '确认并创建'];
    return '<div class="steps">' + names.map(function (n, i) {
      var cls = i === st.step ? 'active' : (i < st.step ? 'done' : '');
      return '<div class="step ' + cls + '"><span class="n">' + (i + 1) + '</span>' + U.esc(n) +
        '<span class="st">' + (i === st.step ? '进行中' : i < st.step ? '已完成' : '待填写') + '</span></div>';
    }).join('') + '</div>';
  }

  function nav(back, nextLabel, nextId, nextDisabled) {
    return '<div class="row mt" style="margin-top:var(--sp-5)">' +
      (back ? '<button class="btn" id="w-back">' + Icon.svg('arrowLeft', 15) + '上一步</button>' : '') +
      '<div class="spacer"></div>' +
      (nextLabel ? '<button class="btn primary" id="' + nextId + '"' + (nextDisabled ? ' disabled' : '') + '>' +
        U.esc(nextLabel) + Icon.svg('chevron', 15) + '</button>' : '') +
      '</div>';
  }

  function stepCore() {
    return '<div class="card"><div class="card-head"><h3>选择服务端核心</h3>' +
      '<div class="right muted small">不同核心决定插件/模组生态与性能特性</div></div>' +
      '<div class="core-grid">' + st.sources.map(function (s) {
        return '<div class="core-card' + (s.id === st.source ? ' sel' : '') + '" data-src="' + s.id + '">' +
          '<div class="nm">' + Icon.svg('box', 16) + U.esc(s.label.split('（')[0]) + '</div>' +
          '<div class="ds">' + U.esc(s.desc) + '</div>' +
          '<span class="badge' + (s.id === 'paper' || s.id === 'purpur' ? ' gold' : '') + '">' +
            U.esc(s.id === 'forge' || s.id === 'neoforge' ? '需安装器' : '可直接运行') + '</span>' +
          '</div>';
      }).join('') + '</div>' + nav(false, '下一步：选版本', 'w-next') + '</div>';
  }

  function stepVersion() {
    if (st.loadingVersions) {
      return '<div class="card"><div class="loading"><span class="spinner"></span>正在从下载源获取版本列表…</div></div>';
    }
    if (st.versionError) {
      return '<div class="card"><div class="card-head"><h3>选择游戏版本</h3></div>' +
        '<div class="tip err">源不可达：' + U.esc(st.versionError) + '</div>' +
        '<div class="mt muted small">可以改用其它核心，或在创建完成后使用「本地上传 jar」通道。</div>' +
        '<div class="row mt"><button class="btn" id="w-retry">' + Icon.svg('refresh', 15) + '重试</button>' +
        '<button class="btn" data-src-jump="vanilla">改用原版 Vanilla</button></div>' +
        nav(true) + '</div>';
    }
    var rel = st.versions.filter(function (v) { return v.type === 'release'; });
    var other = st.versions.filter(function (v) { return v.type !== 'release'; });
    return '<div class="card"><div class="card-head"><h3>选择游戏版本</h3>' +
      '<div class="right"><span class="badge">共 ' + st.versions.length + ' 个 · 正式版 ' + rel.length + '</span></div></div>' +
      '<div class="tip mb">点下面的版本即可选中（浅色高亮的就是当前选择）。' +
        '找不到想要的版本时，再到最底下的输入框手填号（例如快照或未列出的版本）。</div>' +
      '<div class="grid cols-2 tight ver-cols">' +
        '<div>' + verListHead('正式版本', 'v-rel', rel, '该源没有正式版') + '</div>' +
        '<div>' + verListHead('快照 / 预发布', 'v-other', other, '无') + '</div>' +
      '</div>' +
    '</div>' +
    /* "当前选择" + 手填，放在版本卡片**外面的独立卡片**里（版本列表是滚动容器，
       挤在同一张卡里会叠在一起）。 */
    '<div class="card">' +
      '<div class="ver-picked mb"><span class="kicker">当前选择</span>' +
        '<span class="badge ok mono" id="v-picked">' + U.esc(st.version || '尚未选择') + '</span>' +
        (st.build ? '<span class="badge mono">构建 #' + U.esc(st.build) + '（自动取最新）</span>' : '') +
      '</div>' +
      /* 构建列表已去掉：Paper/Forge 这类源动辄几百个构建，把列表摊开会让整页超高，
         上一步/下一步被顶到屏幕外点不到（实测）。默认**自动取该版本的最新构建**，
         想换构建的人用下面这个下拉即可（只渲染最近若干条）。 */
      '<div id="v-build-host">' + buildsBlock() + '</div>' +
      '<div class="row"><div class="field" style="flex:0 0 260px;margin:0">' +
        '<label>手填版本号（可选）</label><input type="text" id="v-manual" class="mono" placeholder="例如 1.21.4" value="" /></div>' +
        '<button class="btn sm" id="v-manual-apply">用这个版本</button>' +
      '</div>' + nav(true, '下一步：内存 / 端口', 'w-next') + '</div>';
  }

  /* 版本列表：固定高度滚动容器（高度写在 CSS，避免列表项溢出盖住后面的按钮） */
  function verListHead(title, id, list, emptyText) {
    return '<div class="kicker mb">' + U.esc(title) + ' <span class="faint">(' + list.length + ')</span></div>' +
      '<div class="version-list" id="' + id + '">' +
      (list.length ? list.slice(0, 300).map(vitem).join('')
                   : '<div class="empty small">' + U.esc(emptyText) + '</div>') +
      '</div>';
  }

  /* 构建选择：**紧凑下拉**，不再是摊开的列表。
     为什么改：Paper / Forge / NeoForge 的构建动辄几百个，把列表摊开（190px 滚动区）
     会让整页超高 → 底部的「上一步 / 下一步」被顶到屏幕外，用户点不到（实测）。
     默认自动选该版本的**最新构建**，想换的人从下拉里选最近的若干条。 */
  function buildsBlock() {
    if (!st.builds.length) return '';
    var list = st.builds.slice(0, 40);
    return '<div class="row mb" style="align-items:flex-end;gap:var(--sp-3)">' +
      '<div class="field" style="flex:0 1 240px;margin:0">' +
        '<label>构建（默认最新，共 ' + st.builds.length + ' 个）</label>' +
        '<select id="v-build" class="mono">' +
        list.map(function (b) {
          var id = String(b.id);
          return '<option value="' + U.esc(id) + '"' + (id === String(st.build) ? ' selected' : '') + '>' +
            '#' + U.esc(id) + (b.channel ? ' · ' + U.esc(b.channel) : '') +
            (b.time ? ' · ' + U.esc(String(b.time).slice(0, 10)) : '') + '</option>';
        }).join('') +
        '</select></div>' +
      '<div class="small faint" style="padding-bottom:8px">列表只显示最近 ' + list.length + ' 个构建</div>' +
      '</div>';
  }

  function vitem(v) {
    return '<div class="version-item' + (v.id === st.version ? ' sel' : '') + '" data-v="' + U.esc(v.id) + '">' +
      '<span>' + U.esc(v.id) + '</span><div class="spacer"></div>' +
      '<span class="small faint">' + U.esc(v.released || v.type || '') + '</span></div>';
  }

  function stepRes() {
    var cores = st.sources.length;
    return '<div class="card"><div class="card-head"><h3>内存与端口</h3>' +
      '<div class="right muted small">端口要未被占用；内存不要超过物理内存</div></div>' +
      '<div class="grid cols-2 tight">' +
        '<div class="field"><label for="f-name">实例名称</label>' +
          '<input type="text" id="f-name" value="' + U.esc(st.name) + '" maxlength="40" />' +
          '<div class="desc">会作为实例目录名的一部分，建议用英文/数字</div></div>' +
        '<div class="field"><label for="f-port">服务端端口</label>' +
          '<input type="number" id="f-port" class="num" value="' + st.port + '" min="1024" max="65535" />' +
          '<div class="desc">默认 25565；面板会检查是否与已有实例冲突</div></div>' +
        '<div class="field"><label for="f-mem">最大内存（MB）</label>' +
          '<input type="number" id="f-mem" class="num" value="' + st.memory + '" min="512" max="65536" step="512" />' +
          '<div class="desc">JVM -Xmx，建议 2048MB 起（模组服 4096MB+）</div></div>' +
        '<div class="field"><label>选项</label>' +
          '<label class="check"><input type="checkbox" id="f-auto"' + (st.autoRestart ? ' checked' : '') + ' />崩溃自动重启（指数退避，有上限）</label>' +
          '<label class="check mt"><input type="checkbox" id="f-eula"' + (st.eula ? ' checked' : '') + ' />自动接受 Minecraft EULA</label>' +
          '<label class="check mt"><input type="checkbox" id="f-dl"' + (st.download ? ' checked' : '') + ' />创建后自动下载服务端核心</label>' +
          '<label class="check mt"><input type="checkbox" id="f-rcon"' + (st.rcon ? ' checked' : '') + ' />写入 RCON 配置（用于取玩家列表）</label>' +
        '</div>' +
      '</div>' +
      /* 存放目录：留空用默认位置（数据目录/instances/…），填了就按你给的绝对路径建。
         后端会挡系统目录与面板自身目录，所以这里只做"填了就必须是绝对路径"的简单引导。 */
      '<div class="field mt"><label for="f-dir">服务器存放目录（可留空）</label>' +
        '<input type="text" id="f-dir" class="mono" value="' + U.esc(st.dir) + '" ' +
          'placeholder="留空 = 默认位置（数据目录/instances/…）" />' +
        '<div class="desc">填绝对路径可把这个世界放到别的盘/目录，例如 ' +
          '<span class="mono">/opt/mc-servers/生存服</span> 或 <span class="mono">/data/mc/survival</span>。' +
          '必须是<b>绝对路径</b>且指向一个<b>空目录或尚不存在的位置</b>；' +
          '系统目录（/etc、/usr、/root、/home 等）与面板自身目录会被拒绝。</div>' +
      '</div>' +
      nav(true, '下一步：Java 运行时', 'w-next') + '</div>';
  }

  function stepJava() {
    return '<div class="card"><div class="card-head"><h3>Java 运行时</h3>' +
      '<div class="right muted small">面板会自动检测系统 Java，也可从这里下载（优先国内镜像加速）</div></div>' +
      (st.javas.length
        ? '<div class="version-list mb">' + st.javas.map(function (j) {
            return '<div class="version-item' + (j.path === st.javaPath ? ' sel' : '') + '" data-java="' + U.esc(j.path) + '">' +
              Icon.svg('java', 15) + '<span>Java ' + (j.major || '?') + '</span>' +
              '<div class="spacer"></div><span class="small faint mono">' + U.esc(j.path) + '</span>' +
              (j.source === 'panel' ? '<span class="badge gold">面板安装</span>' : '') + '</div>';
          }).join('') + '</div>'
        : '<div class="tip warn mb">未检测到可用的 Java。可以现在从 Adoptium 下载一个（17 / 21），或稍后在设置里配置。</div>') +
      '<div class="row">' +
        '<div class="field" style="flex:1 1 320px;margin:0">' +
          '<label>手动指定 java 路径（留空=自动）</label>' +
          '<input type="text" id="f-java" class="mono" value="' + U.esc(st.javaPath) + '" placeholder="例如 C:\\Program Files\\Java\\jdk-21\\bin\\java.exe" /></div>' +
        '<button class="btn" id="f-java-test">测试</button>' +
      '</div>' +
      '<hr class="hair" />' +
      '<div class="kicker mb">从 Adoptium 下载（Temurin JRE）</div>' +
      '<div class="row tight">' +
        '<button class="btn sm" data-adopt="17">下载 Java 17</button>' +
        '<button class="btn sm" data-adopt="21">下载 Java 21</button>' +
        '<button class="btn sm" data-adopt="25">下载 Java 25</button>' +
        '<span class="small muted">较大文件，可能耗时数分钟；失败会明确提示源不可达</span>' +
      '</div>' +
      '<div id="java-progress" class="mt"></div>' +
      nav(true, '下一步：确认', 'w-next') + '</div>';
  }

  function stepConfirm() {
    var core = (st.sources.filter(function (s) { return s.id === st.source; })[0] || {});
    var needInstaller = st.source === 'forge' || st.source === 'neoforge';
    return '<div class="card"><div class="card-head"><h3>确认并创建</h3>' +
      '<div class="right">' + C.statusBadge('stopped', '尚未创建') + '</div></div>' +
      '<div class="kv mb">' +
        '<div class="k">核心</div><div class="v">' + U.esc(core.label || st.source) + '</div>' +
        '<div class="k">游戏版本</div><div class="v">' + U.esc(st.version || '未选择') + '</div>' +
        (st.build ? '<div class="k">构建</div><div class="v">' + U.esc(st.build) + '</div>' : '') +
        '<div class="k">实例名</div><div class="v">' + U.esc(st.name) + '</div>' +
        '<div class="k">端口</div><div class="v">' + st.port + '</div>' +
        '<div class="k">最大内存</div><div class="v">' + st.memory + ' MB</div>' +
        '<div class="k">存放目录</div><div class="v mono">' + (st.dir ? U.esc(st.dir) : '默认位置（数据目录/instances/…）') + '</div>' +
        '<div class="k">Java</div><div class="v">' + U.esc(st.javaPath || '自动检测（PATH 中的 java）') + '</div>' +
        '<div class="k">自动重启</div><div class="v">' + (st.autoRestart ? '开' : '关') + '</div>' +
        '<div class="k">接受 EULA</div><div class="v">' + (st.eula ? '是' : '否') + '</div>' +
      '</div>' +
      (needInstaller
        ? '<div class="tip warn mb">该核心需要先运行官方安装器生成启动脚本：创建后请到「实例详情 → 设置」点「运行安装器」，' +
          '完成后面板会自动记录启动方式。</div>' : '') +
      '<div id="create-progress"></div>' +
      '<div id="create-result"></div>' +
      nav(true, '创建并开始下载', 'w-create') + '</div>';
  }

  function render() {
    var stepsHtml = [stepCore, stepVersion, stepRes, stepJava, stepConfirm];
    body.innerHTML = '' +
      '<div class="page-head"><div class="titles"><div class="kicker">New Instance</div>' +
        '<h1>新建实例向导</h1><div class="sub">五步完成：选核心 → 选版本 → 内存/端口 → Java → 确认下载并创建</div></div></div>' +
      steps() + stepsHtml[st.step]();
    bind();
  }

  function bind() {
    body.querySelectorAll('[data-src]').forEach(function (el) {
      el.addEventListener('click', function () {
        st.source = el.getAttribute('data-src');
        st.versions = []; st.version = ''; st.builds = []; st.build = '';
        render(); loadVersions(st.source);
      });
    });
    body.querySelectorAll('[data-src-jump]').forEach(function (el) {
      el.addEventListener('click', function () {
        st.source = el.getAttribute('data-src-jump');
        st.versionError = ''; render(); loadVersions(st.source);
      });
    });
    body.querySelectorAll('[data-v]').forEach(function (el) {
      el.addEventListener('click', function () {
        st.version = el.getAttribute('data-v');
        /* 只改选中态，不整页重渲染 —— 重渲染会把滚动位置与手填框内容一起清掉 */
        body.querySelectorAll('[data-v]').forEach(function (x) { x.classList.remove('sel'); });
        el.classList.add('sel');
        var pk = document.getElementById('v-picked');
        if (pk) pk.textContent = st.version;
        var man = document.getElementById('v-manual');
        if (man) man.value = '';
        st.builds = []; st.build = '';
        loadBuilds();
      });
    });
    bindBuildSelect();
    var manualApply = document.getElementById('v-manual-apply');
    if (manualApply) manualApply.addEventListener('click', function () {
      var man = document.getElementById('v-manual');
      var v = man ? man.value.trim() : '';
      if (!v) { Toast.warn('请先在输入框里填版本号'); return; }
      st.version = v;
      body.querySelectorAll('[data-v]').forEach(function (x) { x.classList.remove('sel'); });
      var pk = document.getElementById('v-picked');
      if (pk) pk.textContent = v + '（手填）';
      st.builds = []; st.build = '';
      loadBuilds();
      Toast.ok('已使用手填版本 ' + v);
    });
    var manual = document.getElementById('v-manual');
    if (manual) manual.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); if (manualApply) manualApply.click(); }
    });
    body.querySelectorAll('[data-java]').forEach(function (el) {
      el.addEventListener('click', function () {
        st.javaPath = el.getAttribute('data-java');
        render();
      });
    });
    body.querySelectorAll('[data-adopt]').forEach(function (el) {
      el.addEventListener('click', function () {
        var major = el.getAttribute('data-adopt');
        var box = document.getElementById('java-progress');
        box.innerHTML = '<div class="row small"><span class="spinner"></span>正在从 Adoptium 下载 Java ' + major + '…（大文件，请稍候）</div>';
        C.withLoading(el, API.post('/api/java/install', { major: Number(major), image: 'jre' }).then(function (r) {
          box.innerHTML = '<div class="tip">已安装 Java ' + r.major + '：<span class="mono">' + U.esc(r.path) + '</span></div>';
          return API.get('/api/java');
        }).then(function (j) {
          st.javas = j.detected || [];
          st.javaPath = (st.javas[0] || {}).path || st.javaPath;
          render(); Toast.ok('Java 安装完成');
        }).catch(function (e) {
          box.innerHTML = '<div class="tip err">下载失败（源不可达）：' + U.esc(e.message) + '</div>';
        }));
      });
    });
    var jt = document.getElementById('f-java-test');
    if (jt) jt.addEventListener('click', function () {
      var p = document.getElementById('f-java').value.trim();
      C.withLoading(jt, API.post('/api/java/test', { path: p }).then(function (r) {
        if (r.ok) Toast.ok('可用：Java ' + r.major + ' — ' + r.version_line);
        else Toast.err('不可用：' + (r.error || '未知错误'));
      }).catch(function (e) { Toast.err(e.message); }));
    });
    var back = document.getElementById('w-back');
    if (back) back.addEventListener('click', function () { if (st.step > 0) { st.step--; render(); } });
    var next = document.getElementById('w-next');
    if (next) next.addEventListener('click', function () { gotoNext(); });
    var create = document.getElementById('w-create');
    if (create) create.addEventListener('click', function () { doCreate(create); });
    var retry = document.getElementById('w-retry');
    if (retry) retry.addEventListener('click', function () { loadVersions(st.source); });
  }

  function gotoNext() {
    if (st.step === 0) {
      if (!st.source) { Toast.warn('请先选择核心'); return; }
      if (!st.versions.length) loadVersions(st.source);
      st.step = 1; render(); return;
    }
    if (st.step === 1) {
      var man = document.getElementById('v-manual');
      if (man && man.value.trim()) st.version = man.value.trim();
      if (!st.version) { Toast.warn('请选择或填写游戏版本'); return; }
      st.step = 2; render(); return;
    }
    if (st.step === 2) {
      st.name = (document.getElementById('f-name').value || '').trim();
      st.port = parseInt(document.getElementById('f-port').value, 10);
      st.memory = parseInt(document.getElementById('f-mem').value, 10);
      var dEl = document.getElementById('f-dir');
      st.dir = dEl ? (dEl.value || '').trim() : '';
      // 前端先挡一道：填了就必须是绝对路径，省得提交后才报错
      if (st.dir && !/^([A-Za-z]:[\\/]|\/)/.test(st.dir)) {
        Toast.warn('存放目录要填绝对路径，例如 /opt/mc-servers/生存服（留空则用默认位置）');
        if (dEl) dEl.focus();
        return false;
      }
      st.autoRestart = document.getElementById('f-auto').checked;
      st.eula = document.getElementById('f-eula').checked;
      st.download = document.getElementById('f-dl').checked;
      st.rcon = document.getElementById('f-rcon').checked;
      if (!st.name) { Toast.warn('请填写实例名称'); return; }
      if (!(st.port >= 1024 && st.port <= 65535)) { Toast.warn('端口需在 1024~65535 之间'); return; }
      if (!(st.memory >= 512 && st.memory <= 65536)) { Toast.warn('内存需在 512~65536 MB 之间'); return; }
      st.step = 3; render(); return;
    }
    if (st.step === 3) {
      var jf = document.getElementById('f-java');
      if (jf) st.javaPath = jf.value.trim();
      st.step = 4; render(); return;
    }
  }

  function loadVersions(source) {
    st.loadingVersions = true; st.versionError = ''; render();
    API.get('/api/cores/' + source + '/versions').then(function (d) {
      st.loadingVersions = false;
      if (!d.ok) {
        st.versions = []; st.versionError = d.error || '源不可达';
      } else {
        st.versions = d.versions || [];
        st.versionError = '';
        if (!st.version && st.versions.length) st.version = st.versions[0].id;
        loadBuilds();
      }
      render();
    }).catch(function (e) {
      st.loadingVersions = false;
      st.versions = []; st.versionError = e.message;
      render();
    });
  }

  function loadBuilds() {
    if (['paper', 'forge', 'neoforge'].indexOf(st.source) < 0 || !st.version) return;
    API.get('/api/cores/' + st.source + '/builds?mc=' + encodeURIComponent(st.version)).then(function (d) {
      if (d.ok && (d.builds || []).length) {
        st.builds = d.builds;
        if (!st.build) st.build = String(st.builds[0].id);
        /* 只替换构建那一块，不整页重渲染 —— 否则版本列表的滚动位置会被重置。
           构建现在是**下拉**（不再是摊开的列表），所以替换 .ver-picked 之后的构建行即可。 */
        var host = document.getElementById('v-build-host');
        if (host) {
          host.innerHTML = buildsBlock();
          bindBuildSelect();
        } else {
          render();
        }
      }
    }).catch(function () {});
  }

  /* 绑定构建下拉的 change（抽出来，供首次渲染与局部替换复用） */
  function bindBuildSelect() {
    var bsel = document.getElementById('v-build');
    if (!bsel || bsel.dataset.bound === '1') return;
    bsel.dataset.bound = '1';
    bsel.addEventListener('change', function () {
      st.build = bsel.value;
      var pk = document.getElementById('v-picked');
      if (pk) pk.textContent = st.version + '  构建 #' + st.build;
    });
  }

  function doCreate(btn) {
    var prog = document.getElementById('create-progress');
    C.withLoading(btn, API.post('/api/instances', {
      name: st.name, core_type: st.source, mc_version: st.version, core_version: st.build,
      memory_mb: st.memory, port: st.port, java_path: st.javaPath, auto_restart: st.autoRestart,
      accept_eula: st.eula, download: st.download,
      rcon_enabled: st.rcon, rcon_port: st.rconPort,
      /* 存放目录：留空 = 后端用默认位置；填了就按绝对路径建（后端会挡系统目录） */
      dir: st.dir || ''
    }).then(function (r) {
      st.created = r;
      var dl = r.download || null;
      /* 判断"下载有没有开始"只看 **task_id** —— 不再看 `ok`。
         历史坑：后端曾用 `ok` 表示"已经下载完成"，而下载是异步的，创建那一刻必然
         还在下载中，于是 ok:false 被这里当成"下载没启动"，**进度条根本不显示**，
         还打出"核心下载未开始：核心正在后台下载中（0%）"这种自相矛盾的话。 */
      var started = !!(dl && dl.task_id);
      var html = '<div class="tip mb">实例已创建（ID ' + r.id + '）。</div>';
      if (started) {
        html += '<div class="kicker mb">下载服务端核心</div><div id="dl-box" class="mb"></div>';
        if (dl.filename) {
          html += '<div class="small muted mb">' + U.esc(dl.filename) +
            (dl.note ? '（' + U.esc(dl.note) + '）' : '') +
            (dl.source_used ? ' · 来源：' + U.esc(dl.source_used) : '') +
            (dl.mirror_used ? ' · 走了镜像' : '') + '</div>';
        }
        html += '<div class="tip mb">下载在后台进行，<b>完成前请不要点启动</b>。' +
          '可以先离开这个页面，到实例详情里能看到进度。</div>';
      } else if (dl) {
        html += '<div class="tip err mb">核心下载未开始：' + U.esc(dl.error || '未知原因') +
          '　可以到实例详情 → 设置 里重试，或本地上传 jar。</div>';
      } else if (st.download) {
        html += '<div class="tip err mb">没有拿到下载任务（未指定游戏版本？）。' +
          '可以到实例详情 → 设置 里重试，或本地上传 jar。</div>';
      }
      html += '<div class="row"><button class="btn primary" id="go-detail">' + Icon.svg('terminal', 15) +
        '进入实例详情</button><button class="btn" onclick="location.hash=\'#/instances\'">返回列表</button></div>';
      prog.innerHTML = html;
      document.getElementById('go-detail').addEventListener('click', function () {
        location.hash = '#/instances/' + r.id;
      });
      if (started) {
        C.trackDownload(dl.task_id, document.getElementById('dl-box'), function (d) {
          if (d.status === 'done') Toast.ok('核心下载完成并已校验：' + d.label);
          else Toast.err('核心下载失败：' + (d.error || '未知原因'));
        });
      }
      Toast.ok('实例「' + st.name + '」创建成功');
      return false;   // 不关弹窗（这里是页面内进度区）
    }).catch(function (e) {
      prog.innerHTML = '<div class="tip err">创建失败：' + U.esc(e.message) + '</div>';
    }));
  }
};
